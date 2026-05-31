from __future__ import annotations

import ipaddress
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.demo import demo_readiness, run_demo_init
from sectorscout.hindsight import (
    build_hindsight_hypothesis_registry,
    build_hindsight_observation_links,
    build_hindsight_pattern_observations,
    build_hindsight_replay_gates,
    default_hindsight_cases,
    fetch_hindsight_prices,
    historical_pattern_summary,
    latest_hindsight_evidence,
    latest_hindsight_events,
    latest_hindsight_hypotheses,
    latest_hindsight_hypothesis_case_results,
    latest_hindsight_observation_links,
    latest_hindsight_pattern_observations,
    latest_hindsight_replay_gates,
    resolve_first_tradable_date,
    scan_hindsight_cases,
    seed_hindsight_cases,
    seed_hindsight_evidence,
    seed_hindsight_events,
)
from sectorscout.intel.chandler_seed import load_chandler_fixture, seed_chandler_fixture
from sectorscout.intel.capture_inbox import capture_markdown_text
from sectorscout.intel.manual_inbox import parse_manual_markdown
from sectorscout.intel.models import TradeViewDraft
from sectorscout.intel.overlap import classify_overlap, compute_overlap
from sectorscout.intel.public_sources import collect_public_sources, load_public_sources
from sectorscout.intel.public_web import _HttpFetchResult, collect_public_url
from sectorscout.intel.report import build_report_inclusion_summary, generate_intel_daily_report
from sectorscout.intel.storage import (
    ensure_intel_tables,
    insert_note,
    insert_review_mark,
    insert_trade_view,
    mark_image_observation_reviewed,
    mark_media_trade_views_superseded,
    save_media_bytes,
)
from sectorscout.intel.symbol_normalize import normalize_symbols, related_symbols
from sectorscout.intel.text_extract import extract_trade_view
from sectorscout.intel.vision_extract import extract_image_observation
from sectorscout.intel.workflow import build_research_queue, friendly_bucket_label, friendly_queue_rows, workflow_summary
from sectorscout.intel.x_collector import (
    XSource,
    build_recent_search_query,
    collect_x_recent_search,
    load_x_sources,
    x_api_status,
)
from sectorscout.ui.data import latest_asof_date
from sectorscout.ui.hindsight_presenter import (
    build_case_story_cards,
    build_case_pattern_map_rows,
    build_case_readiness_rows,
    build_gate_review_rows,
    build_hindsight_readout_summary_cards,
    build_methodology_guardrail_rows,
    build_pattern_story_cards,
    build_pattern_insight_rows,
)
from sectorscout.ui.workbench import build_symbol_rows
from sectorscout.ui.pages.capture_inbox import _validate_upload_file
from sectorscout.ui.pages.notes_review import _valid_follow_up as notes_valid_follow_up
from sectorscout.ui.pages.workflow import _valid_follow_up as workflow_valid_follow_up


def _config(tmp_path: Path) -> SectorScoutConfig:
    config = SectorScoutConfig.model_validate({"database": {"path": tmp_path / "test.duckdb"}})
    initialize_database(config)
    ensure_intel_tables(config)
    return config


def _draft(
    *,
    source_id: str = "external_source",
    symbols: list[str] | None = None,
    direction: str = "bullish",
    asof_date: str = "2026-04-27",
    summary: str = "External context for test.",
    media_id: str | None = None,
    requires_review: bool = False,
    user_confirmed: bool = False,
    extraction_method: str = "rule_text_v1",
) -> TradeViewDraft:
    raw_symbols = symbols or ["QQQ"]
    return TradeViewDraft(
        source_id=source_id,
        source_type="manual_capture",
        source_title="Test external context",
        author=None,
        platform="other",
        url=None,
        asof_date=asof_date,
        published_at=None,
        collected_at=None,
        captured_at=None,
        raw_symbols=raw_symbols,
        canonical_symbols=normalize_symbols(raw_symbols),
        asset_class="unknown",
        timeframe="daily",
        direction=direction,
        setup_type=["pullback"],
        key_levels=[],
        trigger_condition=None,
        invalidation_condition=None,
        target_area=None,
        no_trade_condition=None,
        risk_notes=None,
        summary=summary,
        source_excerpt=summary,
        extraction_method=extraction_method,
        extraction_confidence="medium",
        rights_scope="manual_private",
        requires_review=requires_review,
        user_confirmed=user_confirmed,
        media_id=media_id,
    )


def test_manual_capture_parser_preserves_frontmatter_and_body() -> None:
    raw = """---
source_id: discord_manual
author: "source name"
platform: discord
guild_name: "Research"
channel: "index"
published_at: "2026-04-27T06:30:00-07:00"
captured_at: "2026-04-27T07:00:00-07:00"
url: ""
tags: [NQ, QQQ, NVDA]
rights_scope: manual_private
---

NQ demand around 27300. Wait for reclaim.
"""
    parsed = parse_manual_markdown(raw)
    assert parsed.source_id == "discord_manual"
    assert parsed.platform == "discord"
    assert parsed.guild_name == "Research"
    assert parsed.channel == "index"
    assert parsed.tags == ["NQ", "QQQ", "NVDA"]
    assert "NQ demand" in parsed.raw_text


def test_manual_markdown_capture_preserves_timestamps(tmp_path: Path) -> None:
    config = _config(tmp_path)
    raw = """---
source_id: discord_manual
author: "source name"
platform: discord
published_at: "2026-04-27T06:30:00-07:00"
captured_at: "2026-04-27T07:00:00-07:00"
tags: [NQ]
rights_scope: manual_private
---

NQ demand around 27300. Wait for reclaim.
"""
    raw_item_id, view_id = capture_markdown_text(config, raw, asof_date=date(2026, 4, 27))
    assert view_id is not None
    with connect_database(config.database.path) as connection:
        raw_row = connection.execute(
            """
            SELECT published_at, captured_at
            FROM intel_raw_items
            WHERE raw_item_id = ?
            """,
            [raw_item_id],
        ).fetchone()
        view_row = connection.execute(
            """
            SELECT published_at, captured_at
            FROM intel_trade_views
            WHERE intel_view_id = ?
            """,
            [view_id],
        ).fetchone()
    assert str(raw_row[0]).startswith("2026-04-27 13:30:00")
    assert str(raw_row[1]).startswith("2026-04-27 14:00:00")
    assert str(view_row[0]).startswith("2026-04-27 13:30:00")
    assert str(view_row[1]).startswith("2026-04-27 14:00:00")


def test_image_media_storage_deduplicates_by_hash(tmp_path: Path) -> None:
    config = _config(tmp_path)
    payload = b"\x89PNG\r\n\x1a\nsame-image"
    first = save_media_bytes(
        config,
        payload,
        filename="chart.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    second = save_media_bytes(
        config,
        payload,
        filename="chart-copy.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    assert first == second
    with connect_database(config.database.path) as connection:
        count = connection.execute("SELECT COUNT(*) FROM intel_media_items").fetchone()[0]
    assert count == 1


def test_save_media_bytes_sanitizes_uploaded_filename(tmp_path: Path) -> None:
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\npath-traversal",
        filename="../escape.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    with connect_database(config.database.path) as connection:
        local_path = Path(
            connection.execute(
                "SELECT local_path FROM intel_media_items WHERE media_id = ?",
                [media_id],
            ).fetchone()[0]
        )
    assert local_path.parent == tmp_path / "media"
    assert not (tmp_path / "escape.png").exists()


def test_vision_fallback_marks_pending_without_provider(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\npending",
        filename="pending.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    observation_id = extract_image_observation(config, media_id)
    with connect_database(config.database.path) as connection:
        status = connection.execute(
            "SELECT extraction_status FROM intel_media_items WHERE media_id = ?",
            [media_id],
        ).fetchone()[0]
        observation = connection.execute(
            "SELECT extraction_provider, requires_review FROM intel_image_observations WHERE observation_id = ?",
            [observation_id],
        ).fetchone()
    assert status == "pending_vision_provider"
    assert observation == ("none", True)


def test_vision_provider_requires_consent_for_private_media(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\nprivate-no-consent",
        filename="private.png",
        raw_item_id=None,
        source_id="manual_image",
        rights_scope="manual_private",
        media_root=tmp_path / "media",
    )

    def fail_if_called(_media: dict) -> dict:
        raise AssertionError("private media should not be sent to a vision provider without consent")

    monkeypatch.setattr("sectorscout.intel.vision_extract._call_openai_vision", fail_if_called)
    observation_id = extract_image_observation(config, media_id)
    with connect_database(config.database.path) as connection:
        status = connection.execute(
            "SELECT extraction_status FROM intel_media_items WHERE media_id = ?",
            [media_id],
        ).fetchone()[0]
        observation = connection.execute(
            """
            SELECT extraction_provider, inferred_context
            FROM intel_image_observations
            WHERE observation_id = ?
            """,
            [observation_id],
        ).fetchone()
        view_count = connection.execute("SELECT COUNT(*) FROM intel_trade_views").fetchone()[0]
    assert status == "pending_vision_consent"
    assert observation[0] == "none"
    assert "explicit user consent" in observation[1]
    assert view_count == 0


def test_vision_provider_creates_review_required_draft_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\nprovider",
        filename="provider.png",
        raw_item_id=None,
        source_id="manual_image",
        metadata={"vision_provider_consent": True},
        media_root=tmp_path / "media",
    )

    def fake_vision(_media: dict) -> dict:
        return {
            "symbols": ["NQ"],
            "timeframe": "1h",
            "visible_levels": ["27300"],
            "visible_annotations": ["demand", "bullish reaction"],
            "extracted_text": "NQ demand 27300, wait for bullish reaction.",
            "inferred_context": "Visible chart annotation only.",
            "extraction_confidence": "medium",
        }

    monkeypatch.setattr("sectorscout.intel.vision_extract._call_openai_vision", fake_vision)
    observation_id = extract_image_observation(config, media_id)
    with connect_database(config.database.path) as connection:
        observation = connection.execute(
            """
            SELECT extraction_provider, requires_review
            FROM intel_image_observations
            WHERE observation_id = ?
            """,
            [observation_id],
        ).fetchone()
        view = connection.execute(
            """
            SELECT canonical_symbols_json, key_levels_json, requires_review, user_confirmed, media_id
            FROM intel_trade_views
            WHERE extraction_method = 'vision_provider_v1'
            """
        ).fetchone()
    assert observation == ("openai_responses", True)
    assert view[2:] == (True, False, media_id)
    assert "NQ" in view[0]
    assert "QQQ" in view[0]
    assert "27300" in view[1]


def test_mark_image_observation_reviewed_updates_media_status(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\nreviewed",
        filename="reviewed.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    observation_id = extract_image_observation(config, media_id)
    mark_image_observation_reviewed(config, observation_id)
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT io.requires_review, mi.extraction_status
            FROM intel_image_observations io
            JOIN intel_media_items mi ON mi.media_id = io.media_id
            WHERE io.observation_id = ?
            """,
            [observation_id],
        ).fetchone()
    assert row == (False, "user_confirmed")


def test_chandler_fixture_extraction_expected_context() -> None:
    view = extract_trade_view(
        load_chandler_fixture(),
        source_id="chandler_2026_04_26",
        source_type="fixture",
    )
    assert {"ES", "SPX", "NQ", "NDX", "NVDA", "HIMS", "ASTS", "GC"}.issubset(set(view.raw_symbols))
    assert {"7174", "27300", "4580", "5000"}.issubset(set(view.key_levels))
    assert view.direction in {"bullish", "conditional"}
    assert view.invalidation_condition is not None
    assert "1-2H bullish control" in view.invalidation_condition


def test_chandler_fixture_loads_when_current_directory_has_no_data_folder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    raw = load_chandler_fixture()
    assert "Chandler Weekend Prep" in raw
    assert "NQ demand is around 27300" in raw


def test_seed_chandler_fixture_deduplicates_trade_view(tmp_path: Path) -> None:
    config = _config(tmp_path)
    seed_chandler_fixture(config)
    seed_chandler_fixture(config)
    with connect_database(config.database.path) as connection:
        raw_count = connection.execute("SELECT COUNT(*) FROM intel_raw_items").fetchone()[0]
        view_count = connection.execute("SELECT COUNT(*) FROM intel_trade_views").fetchone()[0]
    assert raw_count == 1
    assert view_count == 1


def test_seed_chandler_fixture_allows_separate_asof_views(tmp_path: Path) -> None:
    config = _config(tmp_path)
    seed_chandler_fixture(config, asof_date=date(2026, 4, 26))
    seed_chandler_fixture(config, asof_date=date(2024, 11, 29))
    with connect_database(config.database.path) as connection:
        raw_count = connection.execute("SELECT COUNT(*) FROM intel_raw_items").fetchone()[0]
        asof_dates = [
            str(row[0])
            for row in connection.execute(
                "SELECT asof_date FROM intel_trade_views ORDER BY asof_date"
            ).fetchall()
        ]
    assert raw_count == 1
    assert asof_dates == ["2024-11-29", "2026-04-26"]


def test_latest_asof_date_ignores_intel_overlay_dates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(symbols=["QQQ"], direction="bullish"),
    )
    assert latest_asof_date(config) is None


def test_daily_report_does_not_auto_seed_chandler_fixture(tmp_path: Path) -> None:
    config = _config(tmp_path)
    report_path = generate_intel_daily_report(config, date(2026, 4, 27), output_dir=tmp_path / "reports")
    with connect_database(config.database.path) as connection:
        raw_count = connection.execute("SELECT COUNT(*) FROM intel_raw_items").fetchone()[0]
        view_count = connection.execute("SELECT COUNT(*) FROM intel_trade_views").fetchone()[0]
    report = report_path.read_text(encoding="utf-8")
    assert raw_count == 0
    assert view_count == 0
    assert "No external views available." in report
    assert "Research Workflow Queue" in report


def test_daily_report_filters_external_views_by_asof_date(tmp_path: Path) -> None:
    config = _config(tmp_path)
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(asof_date="2026-04-27", summary="Included same-date external context."),
    )
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(asof_date="2026-04-28", summary="Excluded next-date external context."),
    )
    report_path = generate_intel_daily_report(config, date(2026, 4, 27), output_dir=tmp_path / "reports")
    report = report_path.read_text(encoding="utf-8")
    assert "Included same-date external context." in report
    assert "Excluded next-date external context." not in report
    assert "- Extracted views: 1" in report
    assert "## Report Inclusion Summary" in report
    assert "- Other-date external views excluded: 1" in report
    summary = build_report_inclusion_summary(config, date(2026, 4, 27))
    assert summary["included"]["external_views"] == 1
    assert summary["excluded"]["future_external_views"] == 1


def test_demo_init_creates_nonblank_local_state(tmp_path: Path) -> None:
    config = SectorScoutConfig.model_validate({"database": {"path": tmp_path / "demo.duckdb"}})
    result = run_demo_init(config, reports_dir=tmp_path / "reports", reset=True)
    assert Path(result.report_path).exists()
    assert result.row_counts["symbols"] > 0
    assert result.row_counts["theme_scores"] > 0
    assert result.row_counts["intel_trade_views"] > 0
    readiness = demo_readiness(config, reports_dir=tmp_path / "reports")
    assert all(item["ok"] for item in readiness)


def test_demo_init_refuses_to_reset_non_demo_database(tmp_path: Path) -> None:
    db_path = tmp_path / "sectorscout.duckdb"
    db_path.write_bytes(b"existing")
    config = SectorScoutConfig.model_validate({"database": {"path": db_path}})
    with pytest.raises(ValueError, match="Refusing to reset non-demo database path"):
        run_demo_init(config, reports_dir=tmp_path / "reports", reset=True)


def test_public_source_registry_loads_only_public_web_sources(tmp_path: Path) -> None:
    sources_file = tmp_path / "public_sources.yaml"
    sources_file.write_text(
        """
sources:
  - id: chandler
    type: public_web
    name: Chandler
    url: https://example.com/public
    enabled: true
    tags: [weekly]
  - id: ignored_discord
    type: discord
    url: https://discord.example/private
""",
        encoding="utf-8",
    )
    sources = load_public_sources(sources_file)
    assert [source.source_id for source in sources] == ["chandler"]
    assert sources[0].rights_scope == "public"
    assert sources[0].tags == ["weekly"]


def test_public_url_collection_skips_non_public_scheme(tmp_path: Path) -> None:
    config = _config(tmp_path)
    result = collect_public_url(config, "file:///tmp/private.md")
    assert result.status == "SKIPPED"
    assert result.raw_item_id is None


def test_public_url_collection_skips_private_and_credentialed_urls(tmp_path: Path) -> None:
    config = _config(tmp_path)
    urls = [
        "http://127.0.0.1/private",
        "http://localhost/private",
        "https://user:pass@example.com/post",
    ]
    for url in urls:
        result = collect_public_url(config, url)
        assert result.status == "SKIPPED"
        assert result.raw_item_id is None


def test_public_url_collection_skips_private_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "sectorscout.intel.public_web._resolve_host_ips",
        lambda _host: [ipaddress.ip_address("93.184.216.34")],
    )

    def fake_fetch(_url: str) -> tuple[str, str, str]:
        return (
            "text/html",
            "<html><title>Redirected</title><body>NQ demand around 27300.</body></html>",
            "http://127.0.0.1/private",
        )

    monkeypatch.setattr("sectorscout.intel.public_web._fetch_url", fake_fetch)
    result = collect_public_url(config, "https://example.com/post")
    assert result.status == "SKIPPED"
    assert result.raw_item_id is None


def test_public_url_collection_checks_redirect_before_requesting_private_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    requested_urls: list[str] = []
    monkeypatch.setattr(
        "sectorscout.intel.public_web._resolve_host_ips",
        lambda _host: [ipaddress.ip_address("93.184.216.34")],
    )

    def fake_open(url: str) -> _HttpFetchResult:
        requested_urls.append(url)
        if url.startswith("http://127.0.0.1"):
            raise AssertionError("private redirect target must not be requested")
        return _HttpFetchResult(
            status_code=302,
            content_type="",
            payload="",
            final_url=url,
            redirect_url="http://127.0.0.1/private",
        )

    monkeypatch.setattr("sectorscout.intel.public_web._open_url_once", fake_open)
    result = collect_public_url(config, "https://example.com/redirect")
    assert result.status == "SKIPPED"
    assert result.raw_item_id is None
    assert requested_urls == ["https://example.com/redirect"]


def test_public_url_collection_checks_dns_resolution_before_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    requested_urls: list[str] = []
    monkeypatch.setattr(
        "sectorscout.intel.public_web._resolve_host_ips",
        lambda _host: [ipaddress.ip_address("127.0.0.1")],
    )

    def fake_open(url: str) -> _HttpFetchResult:
        requested_urls.append(url)
        raise AssertionError("resolved private host must not be requested")

    monkeypatch.setattr("sectorscout.intel.public_web._open_url_once", fake_open)
    result = collect_public_url(config, "https://safe-looking.example/post")
    assert result.status == "SKIPPED"
    assert "non-public IP" in str(result.reason)
    assert requested_urls == []


def test_public_url_collection_follows_safe_redirect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    requested_urls: list[str] = []
    monkeypatch.setattr(
        "sectorscout.intel.public_web._resolve_host_ips",
        lambda _host: [ipaddress.ip_address("93.184.216.34")],
    )

    def fake_open(url: str) -> _HttpFetchResult:
        requested_urls.append(url)
        if url == "https://example.com/start":
            return _HttpFetchResult(
                status_code=302,
                content_type="",
                payload="",
                final_url=url,
                redirect_url="/post",
            )
        return _HttpFetchResult(
            status_code=200,
            content_type="text/html",
            payload="<html><title>Public Post</title><body>NQ demand around 27300.</body></html>",
            final_url=url,
        )

    monkeypatch.setattr("sectorscout.intel.public_web._open_url_once", fake_open)
    result = collect_public_url(config, "https://example.com/start")
    assert result.status == "COLLECTED"
    assert requested_urls == ["https://example.com/start", "https://example.com/post"]


def test_public_source_collection_extracts_rule_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    monkeypatch.setattr(
        "sectorscout.intel.public_web._resolve_host_ips",
        lambda _host: [ipaddress.ip_address("93.184.216.34")],
    )
    sources_file = tmp_path / "public_sources.yaml"
    sources_file.write_text(
        """
sources:
  - id: public_blog
    type: public_web
    name: Public Blog
    url: https://example.com/post
    enabled: true
""",
        encoding="utf-8",
    )

    def fake_fetch(_url: str) -> tuple[str, str, str]:
        return (
            "text/html",
            "<html><title>Public Blog</title><body>NQ demand around 27300. Wait for bullish reaction.</body></html>",
            "https://example.com/post",
        )

    monkeypatch.setattr("sectorscout.intel.public_web._fetch_url", fake_fetch)
    results = collect_public_sources(config, sources_path=sources_file)
    assert [result.status for result in results] == ["COLLECTED"]
    with connect_database(config.database.path) as connection:
        raw_count = connection.execute("SELECT COUNT(*) FROM intel_raw_items").fetchone()[0]
        view = connection.execute(
            "SELECT source_id, canonical_symbols_json, key_levels_json FROM intel_trade_views"
        ).fetchone()
    assert raw_count == 1
    assert view[0] == "public_blog"
    assert "NQ" in view[1]
    assert "27300" in view[2]


def test_x_source_registry_and_query_builder(tmp_path: Path) -> None:
    sources_file = tmp_path / "x_sources.yaml"
    sources_file.write_text(
        """
sources:
  - id: x_optionflys
    type: x_account
    handle: optionflys
    enabled: true
    tags: [spx]
  - id: ignored
    type: website
    handle: not_used
""",
        encoding="utf-8",
    )
    sources = load_x_sources(sources_file)
    assert sources == [XSource(handle="optionflys", source_id="x_optionflys", enabled=True, tags=("spx",))]
    assert [source.source_id for source in load_x_sources(tmp_path / "missing.yaml")] == [
        "x_optionflys",
        "x_rbswingtrader",
        "x_chandler",
    ]
    query = build_recent_search_query(["@optionflys", "rbswingtrader"])
    assert query == "(from:optionflys OR from:rbswingtrader) -is:retweet -is:reply"
    assert "not_a_real_cookie" not in query


def test_x_collection_skips_without_bearer_token(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    config = _config(tmp_path)
    result = collect_x_recent_search(config, handles=["optionflys"])
    assert result.status == "SKIPPED"
    assert "X_BEARER_TOKEN" in str(result.reason)
    assert x_api_status()["status"] == "skipped_missing_X_BEARER_TOKEN"
    with connect_database(config.database.path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM intel_raw_items").fetchone()[0] == 0


def test_x_collection_uses_official_recent_search_and_stores_views(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = _config(tmp_path)
    requested: dict[str, object] = {}

    def fake_request(*, query: str, bearer_token: str, max_results: int) -> dict:
        requested.update({"query": query, "bearer_token": bearer_token, "max_results": max_results})
        return {
            "data": [
                {
                    "id": "123",
                    "author_id": "42",
                    "created_at": "2026-05-22T18:30:00.000Z",
                    "text": "$NVDA reclaim watch. NQ demand around 27300; wait for bullish reaction.",
                    "public_metrics": {"like_count": 10},
                    "lang": "en",
                    "conversation_id": "123",
                }
            ],
            "includes": {"users": [{"id": "42", "username": "optionflys", "name": "OptionFlys"}]},
        }

    monkeypatch.setattr("sectorscout.intel.x_collector._request_recent_search", fake_request)
    result = collect_x_recent_search(config, handles=["optionflys"], bearer_token="test-token", max_results=25)
    assert result.status == "COLLECTED"
    assert result.posts_seen == 1
    assert result.raw_items == 1
    assert result.trade_views == 1
    assert requested["query"] == "(from:optionflys) -is:retweet -is:reply"
    assert requested["bearer_token"] == "test-token"
    with connect_database(config.database.path) as connection:
        raw = connection.execute(
            "SELECT source_id, platform, url, collection_method, rights_scope FROM intel_raw_items"
        ).fetchone()
        view = connection.execute(
            """
            SELECT source_id, canonical_symbols_json, key_levels_json, requires_review, extraction_method
            FROM intel_trade_views
            """
        ).fetchone()
    assert raw == ("x_optionflys", "x", "https://x.com/optionflys/status/123", "x_api_recent_search", "public")
    assert "NVDA" in view[1]
    assert "NQ" in view[1]
    assert "27300" in view[2]
    assert view[3:] == (True, "x_api_recent_search_v1")


def test_symbol_normalization_required_aliases() -> None:
    assert related_symbols("ES") == ["ES", "SPX", "SPY"]
    assert related_symbols("NQ") == ["NQ", "NDX", "QQQ"]
    assert related_symbols("Gold") == ["GC", "GLD"]
    assert normalize_symbols(["ES", "NQ", "Gold"]) == ["ES", "SPX", "SPY", "NQ", "NDX", "QQQ", "GC", "GLD"]


def test_overlap_labels() -> None:
    assert classify_overlap(has_internal=True, external_direction="bullish") == "CONFIRMED"
    assert classify_overlap(has_internal=True, external_direction="bearish") == "CONFLICT"
    assert classify_overlap(has_internal=True, external_direction="unknown") == "NEEDS_REVIEW"
    assert classify_overlap(has_internal=True, external_direction="neutral") == "NEEDS_REVIEW"
    assert classify_overlap(has_internal=False, external_direction="conditional") == "EXTERNAL_ONLY"
    assert classify_overlap(has_internal=True, external_direction=None, requires_review=True) == "NEEDS_REVIEW"


def test_overlap_and_workflow_respect_asof_internal_and_external(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    config = _config(tmp_path)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO stock_scores (
                asof_date, symbol, theme_id, stock_opportunity_score, theme_score,
                rs_percentile, fundamental_acceleration, setup_quality,
                volume_accumulation, risk_reward, liquidity, component_coverage_pct,
                state, signal_generated_at_utc, config_hash, git_commit,
                data_snapshot_id, universe_version, theme_version
            ) VALUES
                ('2026-04-27', 'QQQ', 'indexes', 80, 75, 90, 0, 0, 0, 0, 1, 1,
                 'watch', current_timestamp, 'cfg', 'git', 'snap', 'u', 't'),
                ('2026-04-28', 'NVDA', 'ai', 95, 90, 95, 0, 0, 0, 0, 1, 1,
                 'future_watch', current_timestamp, 'cfg', 'git', 'snap', 'u', 't')
            """
        )
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["QQQ"],
            direction="bullish",
            asof_date="2026-04-27",
            requires_review=False,
            user_confirmed=True,
        ),
    )
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["NVDA"],
            direction="bullish",
            asof_date="2026-04-28",
            requires_review=True,
            user_confirmed=False,
        ),
    )
    rows = {row["symbol"]: row for row in compute_overlap(config, asof_date=date(2026, 4, 27))}
    assert rows["QQQ"]["overlap_label"] == "CONFIRMED"
    assert "NVDA" not in rows
    queue = build_research_queue(config, asof_date=date(2026, 4, 27))
    assert not any(item.get("symbol") and "NVDA" in str(item["symbol"]) for item in queue)


def test_unreviewed_image_view_keeps_overlap_needs_review_with_confirmed_peer(tmp_path: Path) -> None:
    config = _config(tmp_path)
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["QQQ"],
            direction="bullish",
            media_id="media-1",
            requires_review=True,
            user_confirmed=False,
            extraction_method="vision_provider_v1",
        ),
    )
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["QQQ"],
            direction="bullish",
            media_id="media-1",
            requires_review=False,
            user_confirmed=True,
            extraction_method="vision_review_manual",
        ),
    )
    rows = {row["symbol"]: row for row in compute_overlap(config)}
    assert rows["QQQ"]["overlap_label"] == "NEEDS_REVIEW"


def test_superseded_image_trade_views_are_excluded_from_overlap(tmp_path: Path) -> None:
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\nsuperseded",
        filename="superseded.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    draft_view_id = insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["QQQ"],
            direction="bullish",
            media_id=media_id,
            requires_review=True,
            user_confirmed=False,
            extraction_method="vision_provider_v1",
        ),
    )
    confirmed_view_id = insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["QQQ"],
            direction="bullish",
            media_id=media_id,
            requires_review=False,
            user_confirmed=True,
            extraction_method="vision_review_manual",
        ),
    )
    mark_media_trade_views_superseded(config, media_id, confirmed_view_id)
    with connect_database(config.database.path) as connection:
        superseded_by = connection.execute(
            "SELECT superseded_by_view_id FROM intel_trade_views WHERE intel_view_id = ?",
            [draft_view_id],
        ).fetchone()[0]
    rows = {row["symbol"]: row for row in compute_overlap(config)}
    assert superseded_by == confirmed_view_id
    assert rows["QQQ"]["overlap_label"] == "CONFIRMED"


def test_review_marks_do_not_aggregate_status(tmp_path: Path) -> None:
    config = _config(tmp_path)
    review_id = insert_review_mark(
        config,
        object_type="intel_view",
        object_id="view-1",
        review_status="not_triggered",
        notes="Condition never occurred.",
    )
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            "SELECT review_status, notes FROM intel_review_marks WHERE review_id = ?",
            [review_id],
        ).fetchone()
    assert row == ("not_triggered", "Condition never occurred.")


def test_research_queue_prioritizes_review_items_and_follow_ups(tmp_path: Path) -> None:
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\nqueue",
        filename="queue.png",
        raw_item_id=None,
        source_id="manual_image",
        media_root=tmp_path / "media",
    )
    extract_image_observation(config, media_id)
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(
            symbols=["QQQ"],
            direction="unknown",
            asof_date="2026-04-28",
            requires_review=True,
            user_confirmed=False,
        ),
    )
    insert_review_mark(
        config,
        object_type="ticker",
        object_id="QQQ",
        review_status="needs_more_data",
        follow_up_date="2026-04-27",
    )
    queue = build_research_queue(config, asof_date=date(2026, 4, 28))
    buckets = [item["bucket"] for item in queue]
    summary = workflow_summary(config, asof_date=date(2026, 4, 28))
    assert buckets[0] == "due_follow_up"
    assert "image_review" in buckets
    assert "external_view_review" in buckets
    assert "overlap_needs_review" in buckets
    assert summary["urgent"] >= 2
    assert summary["follow_ups_due"] == 1


def test_research_queue_defers_items_until_follow_up_date(tmp_path: Path) -> None:
    config = _config(tmp_path)
    view_id = insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(symbols=["QQQ"], direction="unknown", requires_review=True, user_confirmed=False),
    )
    before_review = build_research_queue(config, asof_date=date(2026, 4, 28))
    assert any(item["object_type"] == "intel_view" and item["object_id"] == view_id for item in before_review)

    insert_review_mark(
        config,
        object_type="intel_view",
        object_id=view_id,
        review_status="needs_more_data",
        follow_up_date="2026-05-05",
    )
    deferred = build_research_queue(config, asof_date=date(2026, 4, 28))
    assert not any(item["object_type"] == "intel_view" and item["object_id"] == view_id for item in deferred)

    due = build_research_queue(config, asof_date=date(2026, 5, 5))
    assert any(item["bucket"] == "due_follow_up" and item["object_id"] == view_id for item in due)
    assert any(item["object_type"] == "intel_view" and item["object_id"] == view_id for item in due)


def test_research_queue_expired_review_dismisses_item(tmp_path: Path) -> None:
    config = _config(tmp_path)
    view_id = insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(symbols=["QQQ"], direction="unknown", requires_review=True, user_confirmed=False),
    )
    insert_review_mark(
        config,
        object_type="intel_view",
        object_id=view_id,
        review_status="expired",
    )
    queue = build_research_queue(config, asof_date=date(2026, 4, 28))
    assert not any(item["object_type"] == "intel_view" and item["object_id"] == view_id for item in queue)


def test_notes_are_saved_without_scoring(tmp_path: Path) -> None:
    config = _config(tmp_path)
    note_id = insert_note(
        config,
        object_type="ticker",
        object_id="NQ",
        symbol="NQ",
        note_text="Review only; condition still needs confirmation.",
    )
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            "SELECT symbol, note_text FROM intel_notes WHERE note_id = ?",
            [note_id],
        ).fetchone()
    assert row == ("NQ", "Review only; condition still needs confirmation.")


def test_workbench_symbol_rows_merge_internal_external_context(tmp_path: Path) -> None:
    config = _config(tmp_path)
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            INSERT INTO stock_scores (
                asof_date, symbol, theme_id, stock_opportunity_score, theme_score,
                rs_percentile, fundamental_acceleration, setup_quality,
                volume_accumulation, risk_reward, liquidity, component_coverage_pct,
                state, signal_generated_at_utc, config_hash, git_commit,
                data_snapshot_id, universe_version, theme_version
            ) VALUES
                ('2026-04-27', 'QQQ', 'indexes', 80, 75, 90, 0, 0, 0, 0, 1, 1,
                 'watch', current_timestamp, 'cfg', 'git', 'snap', 'u', 't')
            """
        )
    insert_trade_view(
        config,
        raw_item_id=None,
        draft=_draft(symbols=["QQQ"], direction="bullish", user_confirmed=True),
    )
    ctx = type("Ctx", (), {"config": config, "asof_date": date(2026, 4, 27)})()
    rows = {row.symbol: row for row in build_symbol_rows(ctx)}
    assert rows["QQQ"].internal_score == 80
    assert rows["QQQ"].external_count == 1
    assert rows["QQQ"].overlap_label == "CONFIRMED"


def test_friendly_research_queue_rows_are_plain_language() -> None:
    queue = [
        {
            "priority": 40,
            "bucket": "overlap_external_only",
            "symbol": "ASTS",
            "object_id": "ASTS",
            "page": "Internal vs External Overlap",
            "reason": "An external source mentioned this symbol, but SectorScout does not currently rank or watch it.",
            "next_step": "Decide whether to add it to your watchlist seed or leave it as outside context.",
        }
    ]
    row = friendly_queue_rows(queue)[0]
    assert friendly_bucket_label("overlap_external_only") == "External mention not in SectorScout"
    assert row["What this means"] == "External mention not in SectorScout"
    assert row["Symbol or item"] == "ASTS"
    assert "internal=" not in row["Why it matters"]
    assert "external=" not in row["Why it matters"]


def test_follow_up_date_validation_helpers() -> None:
    assert workflow_valid_follow_up("") == (True, None)
    assert workflow_valid_follow_up("2026-05-05") == (True, "2026-05-05")
    assert workflow_valid_follow_up("05/05/2026") == (False, None)
    assert notes_valid_follow_up("2026-05-05") == (True, "2026-05-05")
    assert notes_valid_follow_up("not-a-date") == (False, None)


def test_capture_upload_validation_helper() -> None:
    assert _validate_upload_file("note.md", b"# NQ\n") is None
    assert _validate_upload_file("note.md", b"\xff\xfe") == "Markdown uploads must be UTF-8 encoded."
    assert "Unsupported upload type" in str(_validate_upload_file("chart.gif", b"GIF89a"))
    assert _validate_upload_file("chart.png", b"\x89PNG\r\n\x1a\npayload") is None
    assert "does not match" in str(_validate_upload_file("chart.jpg", b"\x89PNG\r\n\x1a\npayload"))
    assert "readable PNG" in str(_validate_upload_file("chart.webp", b"not-webp"))


def test_forbidden_automation_modes_are_not_configured() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/sectorscout/intel").glob("*.py"))
    forbidden = [
        "self_bot_mode",
        "personal_discord_token",
        "discord_cookie",
        "private_channel_crawler",
        "browser_cookie",
        "session_scraper",
    ]
    for term in forbidden:
        assert term not in source


def test_forbidden_dashboard_report_language() -> None:
    files = list(Path("src/sectorscout/ui").rglob("*.py")) + [Path("src/sectorscout/intel/report.py")]
    text = "\n".join(path.read_text(encoding="utf-8").lower() for path in files)
    forbidden = [
        "buy " + "signal",
        "sell " + "signal",
        "order " + "placed",
        "fil" + "led",
        "win " + "rate",
        "shar" + "pe",
        "cag" + "r",
        "expect" + "ancy",
        "profit " + "factor",
        "annual " + "return",
        "strategy " + "edge",
    ]
    for term in forbidden:
        assert term not in text


def test_ui_and_intel_modules_import() -> None:
    import sectorscout.intel.storage  # noqa: F401
    import sectorscout.ui.data  # noqa: F401
    import sectorscout.ui.pages.historical_lab  # noqa: F401


def test_hindsight_default_cases_include_requested_symbols() -> None:
    symbols = {case.symbol for case in default_hindsight_cases()}
    assert {"NVDA", "MU", "SNDK", "LITE"}.issubset(symbols)
    roles = {case.symbol: case.case_role for case in default_hindsight_cases()}
    assert roles["NVDA"] == "anchor"
    assert roles["AMD"] == "peer_control"
    assert roles["INTC"] == "negative_control"
    assert roles["MRVL"] == "peer_control"


def test_hindsight_scan_marks_missing_price_history(tmp_path: Path) -> None:
    config = _config(tmp_path)
    count = seed_hindsight_cases(config, tmp_path / "leader_cases.csv")
    results = scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=False)
    assert count == 7
    assert len(results) == 7
    assert {result.symbol for result in results} == {"NVDA", "MU", "SNDK", "LITE", "AMD", "INTC", "MRVL"}
    assert all(result.data_quality == "missing_price_history" for result in results)


def test_hindsight_scan_scores_loaded_price_path(tmp_path: Path) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "NVDA,NVDA test,2024-01-02,2024-04-30,AI chips,Study test case,AI demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    start = date(2024, 1, 2)
    rows = []
    for offset in range(90):
        current = start + timedelta(days=offset)
        close = 10.0 + (offset * 0.2)
        rows.append(
            [
                "NVDA",
                current,
                close,
                close,
                close,
                close,
                1_000_000 + offset,
                close,
                close,
                close,
                close,
                1_000_000 + offset,
                "fixture",
                True,
                False,
                datetime.now(timezone.utc),
            ]
        )
    with connect_database(config.database.path) as connection:
        connection.executemany(
            """
            INSERT INTO daily_prices (
                symbol, price_date, open, high, low, close, volume,
                adj_open, adj_high, adj_low, adj_close, adj_volume,
                provider, is_adjusted, adjustment_warning, ingested_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    [result] = scan_hindsight_cases(config, path=case_file, persist=False)
    assert result.symbol == "NVDA"
    assert result.price_rows == 90
    assert result.data_quality == "ok"
    assert result.max_gain_pct is not None and result.max_gain_pct > 100
    assert result.hindsight_score > 0


def test_hindsight_pattern_observations_split_industry_technical_and_manual(tmp_path: Path) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "NVDA,NVDA test,2024-01-02,2024-04-30,AI semiconductors,Study test case,AI accelerator demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    start = date(2024, 1, 2)
    rows = []
    for offset in range(90):
        current = start + timedelta(days=offset)
        close = 10.0 + (offset * 0.2)
        volume = 1_000_000 if offset < 50 else 1_500_000
        rows.append(
            [
                "NVDA",
                current,
                close,
                close,
                close,
                close,
                volume,
                close,
                close,
                close,
                close,
                volume,
                "fixture",
                True,
                False,
                datetime.now(timezone.utc),
            ]
        )
    with connect_database(config.database.path) as connection:
        connection.executemany(
            """
            INSERT INTO daily_prices (
                symbol, price_date, open, high, low, close, volume,
                adj_open, adj_high, adj_low, adj_close, adj_volume,
                provider, is_adjusted, adjustment_warning, ingested_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    results = scan_hindsight_cases(config, path=case_file, persist=True)
    observations = build_hindsight_pattern_observations(
        [
            case
            for case in default_hindsight_cases()
            if case.symbol == "NVDA"
        ],
        results,
    )
    assert {"industry", "technical", "manual_or_llm_required"}.issubset(
        {observation.observation_group for observation in observations}
    )
    latest = latest_hindsight_pattern_observations(config)
    assert not latest.empty
    assert "Stage 2 trend proxy" in set(latest["pattern_name"])
    assert "Catalyst narrative" in set(latest["pattern_name"])
    manual = latest[latest["observation_group"] == "manual_or_llm_required"]
    assert manual["requires_review"].all()
    assert "outcome_only_not_predictive" in set(latest["status"])


def test_hindsight_pattern_observations_enter_review_queue(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=True)
    queue = build_research_queue(config)
    hindsight_items = [item for item in queue if item["bucket"] == "hindsight_pattern_review"]
    assert hindsight_items
    assert {item["object_type"] for item in hindsight_items} == {"hindsight_pattern_observation"}
    assert any(item["page"] == "Historical Pattern Discovery" for item in hindsight_items)

    first = hindsight_items[0]
    insert_review_mark(
        config,
        object_type="hindsight_pattern_observation",
        object_id=str(first["object_id"]),
        review_status="confirmed_hypothesis",
    )
    after_review = build_research_queue(config)
    assert not any(
        item["bucket"] == "hindsight_pattern_review" and item["object_id"] == first["object_id"]
        for item in after_review
    )


def test_hindsight_observation_links_connect_patterns_to_evidence_and_gates(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=True)
    built_links = build_hindsight_observation_links(config)
    assert built_links

    links = latest_hindsight_observation_links(config)
    assert not links.empty
    assert {"evidence", "gate", "review_required"}.issubset(set(links["link_type"]))

    evidence_links = links[links["link_type"] == "evidence"]
    assert not evidence_links.empty
    assert set(evidence_links["linked_table"]) == {"hindsight_evidence_items"}
    assert set(evidence_links["link_role"]).issubset({"supports", "context"})

    gate_links = links[links["link_type"] == "gate"]
    assert not gate_links.empty
    assert set(gate_links["linked_table"]) == {"hindsight_replay_gates"}
    blocked_gate_links = gate_links[gate_links["link_status"].isin(["DATA_GAP", "FAIL", "PENDING", "REQUIRES_REVIEW"])]
    assert not blocked_gate_links.empty
    assert set(blocked_gate_links["link_role"]) == {"blocks"}

    review_links = links[links["link_type"] == "review_required"]
    assert not review_links.empty
    assert set(review_links["linked_table"]) == {"hindsight_pattern_observations"}
    assert set(review_links["link_role"]) == {"needs_review"}
    assert set(review_links["link_status"]) == {"REQUIRES_REVIEW"}
    assert "supports" not in set(review_links["link_role"])


def test_hindsight_hypothesis_registry_builds_cross_case_matrix(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=True)
    hypotheses, case_results = build_hindsight_hypothesis_registry(config, path=tmp_path / "leader_cases.csv")

    assert len(hypotheses) == 4
    assert len(case_results) == 28
    assert {hypothesis.hypothesis_name.split(" - ")[0] for hypothesis in hypotheses} == {"H1", "H2", "H3", "H4"}

    latest_hypotheses = latest_hindsight_hypotheses(config)
    latest_results = latest_hindsight_hypothesis_case_results(config)
    assert not latest_hypotheses.empty
    assert not latest_results.empty
    assert "CONFIRMED" not in set(latest_hypotheses["promotion_status"])

    h2 = latest_hypotheses[latest_hypotheses["hypothesis_name"].str.startswith("H2")].iloc[0]
    assert h2["promotion_status"] == "PARTIAL_SUPPORT_TIMING_GAP"
    h2_results = latest_results[latest_results["hypothesis_id"] == h2["hypothesis_id"]].set_index("symbol")
    assert h2_results.loc["NVDA", "result_status"] == "NOT_APPLICABLE"
    assert h2_results.loc["MU", "result_status"] == "SUPPORTS"
    assert h2_results.loc["LITE", "result_status"] == "SUPPORTS"
    assert h2_results.loc["SNDK", "result_status"] in {"TIMING_GAP", "REQUIRES_REVIEW"}
    assert h2_results.loc["SNDK", "reason_code"] == "downstream_demand_not_pit_usable"
    assert h2_results.loc["AMD", "result_status"] == "DATA_GAP"
    assert h2_results.loc["AMD", "reason_code"] == "control_not_evaluable"


def test_hindsight_hypothesis_registry_blocks_data_gaps_and_context_only_support(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=True)
    build_hindsight_hypothesis_registry(config, path=tmp_path / "leader_cases.csv")

    hypotheses = latest_hindsight_hypotheses(config)
    results = latest_hindsight_hypothesis_case_results(config)

    h3 = hypotheses[hypotheses["hypothesis_name"].str.startswith("H3")].iloc[0]
    assert h3["promotion_status"] == "BLOCKED_BY_DATA_GAP"
    h3_results = results[results["hypothesis_id"] == h3["hypothesis_id"]]
    assert "DATA_GAP" in set(h3_results["result_status"])

    h2 = hypotheses[hypotheses["hypothesis_name"].str.startswith("H2")].iloc[0]
    h2_results = results[results["hypothesis_id"] == h2["hypothesis_id"]].set_index("symbol")
    assert h2_results.loc["SNDK", "result_status"] != "SUPPORTS"
    assert "spin-off" not in h2_results.loc["SNDK", "reason_text"].lower() or h2_results.loc[
        "SNDK", "result_status"
    ] == "TIMING_GAP"


def test_hindsight_control_cases_are_not_false_failures(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=True)
    build_hindsight_hypothesis_registry(config, path=tmp_path / "leader_cases.csv")

    results = latest_hindsight_hypothesis_case_results(config)
    controls = results[results["case_role"].isin(["peer_control", "negative_control"])]
    assert not controls.empty
    assert "SUPPORTS" not in set(controls["result_status"])
    assert "BLOCKS" not in set(controls["result_status"])
    assert "control_not_evaluable" in set(controls["reason_code"])
    assert all("not a failed pattern" in reason for reason in controls[controls["reason_code"] == "control_not_evaluable"]["reason_text"])


def test_hindsight_presenter_builds_plain_language_pattern_readout(tmp_path: Path) -> None:
    config = _config(tmp_path)
    scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=True)
    build_hindsight_hypothesis_registry(config, path=tmp_path / "leader_cases.csv")

    guardrails = build_methodology_guardrail_rows()
    assert guardrails
    assert any("Controls expose" in row["Principle"] for row in guardrails)

    hypotheses = latest_hindsight_hypotheses(config)
    case_results = latest_hindsight_hypothesis_case_results(config)
    insights = build_pattern_insight_rows(hypotheses, case_results)
    assert len(insights) == 4
    h2 = next(row for row in insights if str(row["Pattern candidate"]).startswith("H2"))
    assert "MU" in h2["Leader support"]
    assert "LITE" in h2["Leader support"]
    assert "Controls are present" in h2["Control check"]
    assert "timing" in h2["Current read"].lower()

    case_map = build_case_pattern_map_rows(
        latest_hindsight_events(config),
        latest_hindsight_evidence(config),
        latest_hindsight_replay_gates(config),
        hypotheses,
        case_results,
    )
    assert {row["Symbol"] for row in case_map}.issuperset({"NVDA", "MU", "SNDK", "LITE", "AMD", "INTC", "MRVL"})
    mu = next(row for row in case_map if row["Symbol"] == "MU")
    assert mu["Industry evidence"] == "PASS - PIT official demand evidence"
    assert "technical replay evidence is incomplete" in mu["Plain-English read"]
    amd = next(row for row in case_map if row["Symbol"] == "AMD")
    assert amd["Role"] == "Peer control"
    assert "Control case" in amd["Plain-English read"] or "controls" in amd["Plain-English read"]

    summary_cards = build_hindsight_readout_summary_cards(insights, case_map)
    assert {card["Label"] for card in summary_cards} == {
        "Pattern candidates",
        "Industry + technical aligned",
        "Needs data review",
        "Control coverage",
    }
    assert next(card for card in summary_cards if card["Label"] == "Pattern candidates")["Value"] == "4"
    assert int(next(card for card in summary_cards if card["Label"] == "Needs data review")["Value"]) >= 1

    pattern_cards = build_pattern_story_cards(insights)
    assert len(pattern_cards) == 4
    assert pattern_cards[0]["Lane"] == "Industry mechanism"
    assert all(card["Tone"] in {"green", "blue", "amber", "red", "purple"} for card in pattern_cards)

    case_cards = build_case_story_cards(case_map)
    assert len(case_cards) == 7
    assert next(card for card in case_cards if card["Symbol"] == "NVDA")["Technical"]
    assert next(card for card in case_cards if card["Symbol"] == "SNDK")["Tone"] == "amber"


def test_hindsight_event_ledger_blocks_date_only_reaction(tmp_path: Path) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "TEST,Date-only test,2024-09-01,2024-12-31,AI semiconductors,Study test case,AI demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    count = seed_hindsight_events(config, case_file)
    assert count == 1
    events = latest_hindsight_events(config)
    assert not events.empty
    assert set(events["market_session"]) == {"date_only_ambiguous"}
    assert events["first_tradable_date"].isna().all()
    assert set(events["timing_status"]) == {"DATA_GAP"}

    gates = build_hindsight_replay_gates(config, path=case_file, persist=True)
    assert gates
    timing_gate = next(gate for gate in gates if gate.gate_name == "First tradable date resolved")
    assert timing_gate.gate_status == "DATA_GAP"
    assert "Do not compute event reaction" in timing_gate.reason

    latest_gates = latest_hindsight_replay_gates(config)
    assert not latest_gates.empty
    assert "DATA_GAP" in set(latest_gates["gate_status"])


def test_default_hindsight_events_use_official_timing_seeds(tmp_path: Path) -> None:
    config = _config(tmp_path)
    count = seed_hindsight_events(config, tmp_path / "leader_cases.csv")
    assert count == 7

    events = latest_hindsight_events(config)
    assert not events.empty
    assert {"NVDA", "MU", "SNDK", "LITE", "AMD", "INTC", "MRVL"}.issubset(set(events["symbol"]))
    official = events[events["symbol"].isin(["NVDA", "MU", "SNDK", "LITE"])]
    controls = events[events["symbol"].isin(["AMD", "INTC", "MRVL"])]
    assert set(official["timing_status"]) == {"TIMING_RESOLVED"}
    assert set(official["source_quality"]) == {"sec_8k_official"}
    assert official["published_at_utc"].notna().all()
    assert not official["requires_review"].astype(bool).any()
    assert set(controls["timing_status"]) == {"DATA_GAP"}
    assert controls["requires_review"].astype(bool).all()

    by_symbol = official.set_index("symbol")
    assert str(by_symbol.loc["NVDA", "first_tradable_date"])[:10] == "2024-02-22"
    assert str(by_symbol.loc["MU", "first_tradable_date"])[:10] == "2025-09-24"
    assert str(by_symbol.loc["SNDK", "first_tradable_date"])[:10] == "2025-02-24"
    assert str(by_symbol.loc["LITE", "first_tradable_date"])[:10] == "2026-02-04"
    assert "no_wdc_splice" in str(by_symbol.loc["SNDK", "first_tradable_bar_policy"])

    gates = build_hindsight_replay_gates(config, path=tmp_path / "leader_cases.csv", persist=False)
    first_tradable_gates = [gate for gate in gates if gate.gate_name == "First tradable date resolved"]
    assert len(first_tradable_gates) == 7
    status_by_symbol = {gate.symbol: gate.gate_status for gate in first_tradable_gates}
    assert {status_by_symbol[symbol] for symbol in ["NVDA", "MU", "SNDK", "LITE"]} == {"PASS"}
    assert {status_by_symbol[symbol] for symbol in ["AMD", "INTC", "MRVL"]} == {"DATA_GAP"}


def test_hindsight_evidence_ledger_tracks_pit_usability(tmp_path: Path) -> None:
    config = _config(tmp_path)
    seed_hindsight_events(config, tmp_path / "leader_cases.csv")
    count = seed_hindsight_evidence(config, tmp_path / "leader_cases.csv")
    assert count >= 5

    evidence = latest_hindsight_evidence(config)
    assert not evidence.empty
    assert {"NVDA", "MU", "SNDK", "LITE"}.issubset(set(evidence["symbol"]))
    assert "available_at_utc" in evidence.columns
    assert "replay_decision_at" in evidence.columns

    by_symbol = {symbol: group for symbol, group in evidence.groupby("symbol")}
    assert by_symbol["NVDA"]["usable_in_replay"].astype(bool).any()
    assert any("Data Center" in claim for claim in by_symbol["NVDA"]["claim"])
    assert by_symbol["LITE"]["usable_in_replay"].astype(bool).any()
    assert any("> $400M" in value for value in by_symbol["LITE"]["metric_value"])

    sndk = by_symbol["SNDK"]
    future_growth = sndk[sndk["evidence_kind"] == "segment_revenue"].iloc[0]
    assert not bool(future_growth["usable_in_replay"])
    assert future_growth["evidence_status"] == "REQUIRES_REVIEW"
    assert "Future-only" in future_growth["review_note"]


def test_hindsight_evidence_ledger_keeps_custom_narrative_blocked(tmp_path: Path) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "TEST,Date-only test,2024-09-01,2024-12-31,AI semiconductors,Study test case,AI demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    seed_hindsight_events(config, case_file)
    count = seed_hindsight_evidence(config, case_file)
    assert count == 1
    evidence = latest_hindsight_evidence(config)
    row = evidence.iloc[0]
    assert row["symbol"] == "TEST"
    assert row["evidence_status"] == "DATA_GAP"
    assert not bool(row["usable_in_replay"])
    assert bool(row["requires_review"])


def test_hindsight_presenter_translates_gate_data_gaps(tmp_path: Path) -> None:
    config = _config(tmp_path)
    seed_hindsight_events(config, tmp_path / "leader_cases.csv")
    build_hindsight_replay_gates(config, path=tmp_path / "leader_cases.csv", persist=True)

    events = latest_hindsight_events(config)
    gates = latest_hindsight_replay_gates(config)
    review_rows = build_gate_review_rows(gates)
    assert review_rows

    benchmark_gap = next(row for row in review_rows if row["Question"].startswith("Can we compare"))
    assert benchmark_gap["Status"] == "DATA GAP - missing required evidence"
    assert "not a failed pattern" in benchmark_gap["Meaning"]
    assert "do not switch" in benchmark_gap["Next action"]
    assert benchmark_gap["Blocked conclusion"] == "Relative-strength claims are blocked."

    readiness = build_case_readiness_rows(events, gates)
    assert readiness
    assert any(row["Ready to interpret"].startswith("Blocked") for row in readiness)
    assert any("Load pre-event OHLCV" in row["Next action"] for row in readiness)


def test_first_tradable_date_resolver_respects_market_session() -> None:
    trading_days = [date(2024, 2, 21), date(2024, 2, 22), date(2024, 2, 23)]
    assert resolve_first_tradable_date(date(2024, 2, 21), "pre_market", trading_days) == date(2024, 2, 21)
    assert resolve_first_tradable_date(date(2024, 2, 21), "regular", trading_days) == date(2024, 2, 21)
    assert resolve_first_tradable_date(date(2024, 2, 21), "after_close", trading_days) == date(2024, 2, 22)
    assert resolve_first_tradable_date(date(2024, 2, 21), "date_only_ambiguous", trading_days) is None


def test_hindsight_pre_event_technical_gate_uses_loaded_lookback_rows(tmp_path: Path) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "TEST,TEST case,2024-09-01,2024-12-31,AI semiconductors,Study test case,AI demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    rows = []
    start = date(2024, 1, 1)
    for offset in range(240):
        current = start + timedelta(days=offset)
        close = 100.0 + offset
        rows.append(
            [
                "TEST",
                current,
                close,
                close,
                close,
                close,
                1_000_000 + offset,
                close,
                close,
                close,
                close,
                1_000_000 + offset,
                "fixture",
                True,
                False,
                datetime.now(timezone.utc),
            ]
        )
    with connect_database(config.database.path) as connection:
        connection.executemany(
            """
            INSERT INTO daily_prices (
                symbol, price_date, open, high, low, close, volume,
                adj_open, adj_high, adj_low, adj_close, adj_volume,
                provider, is_adjusted, adjustment_warning, ingested_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    seed_hindsight_events(config, path=case_file)
    gates = build_hindsight_replay_gates(config, path=case_file, persist=False, asof_date=date(2024, 12, 31))
    by_name = {gate.gate_name: gate for gate in gates}
    assert by_name["Pre-event price coverage"].gate_status == "PASS"
    assert by_name["Stage 2 trend explain"].gate_status == "PASS"
    assert by_name["Event timestamp available"].gate_status == "DATA_GAP"


def test_historical_pattern_summary_separates_industry_and_technical_patterns(tmp_path: Path) -> None:
    config = _config(tmp_path)
    results = scan_hindsight_cases(config, path=tmp_path / "leader_cases.csv", persist=False)
    summary = historical_pattern_summary(default_hindsight_cases(), results)
    industry_patterns = summary["industry_patterns"]
    technical_patterns = summary["technical_patterns"]
    assert any(row["Industry / theme pattern"] == "AI semiconductors" for row in industry_patterns)
    assert any(row["Technical pattern to test"] == "Stage 2 trend proxy" for row in technical_patterns)
    assert all("Status" in row for row in industry_patterns + technical_patterns)


def test_hindsight_fetch_prices_inserts_public_rows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "NVDA,NVDA test,2024-01-02,2024-01-04,AI chips,Study test case,AI demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            return (
                '{"chart":{"result":[{"timestamp":[1704205800,1704292200],'
                '"indicators":{"quote":[{"open":[10,10.5],"high":[11,12],'
                '"low":[9,10],"close":[10.5,11.5],"volume":[1000,1200]}]}}],'
                '"error":null}}'
            ).encode("utf-8")

    monkeypatch.setattr("sectorscout.hindsight.urlopen", lambda *_args, **_kwargs: FakeResponse())
    summary = fetch_hindsight_prices(config, path=case_file, include_benchmarks=False)
    assert summary[0]["fetch_start"] == "2023-02-16"
    assert summary[0]["lookback_days"] == 320
    assert summary[0]["rows_inserted"] == 2
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            "SELECT symbol, price_date, close, provider, adjustment_warning FROM daily_prices ORDER BY price_date"
        ).fetchall()
    assert rows[0][0] == "NVDA"
    assert str(rows[0][1]).startswith("2024-01-02")
    assert rows[0][2] == 10.5
    assert rows[0][3] == "yahoo_chart_public"
    assert rows[0][4] is True


def test_hindsight_fetch_prices_loads_fixed_benchmark_for_rs_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = _config(tmp_path)
    case_file = tmp_path / "cases.csv"
    case_file.write_text(
        "\n".join(
            [
                "symbol,label,start_date,end_date,theme,hindsight_reason,anchor_event,source_url",
                "NVDA,NVDA test,2024-01-02,2024-01-04,AI chips,Study test case,AI demand,https://example.com",
            ]
        ),
        encoding="utf-8",
    )

    class FakeResponse:
        def __init__(self, symbol: str) -> None:
            self.symbol = symbol

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def read(self) -> bytes:
            start_ts = int(datetime(2023, 10, 2, tzinfo=timezone.utc).timestamp())
            timestamps = [start_ts + index * 86400 for index in range(80)]
            base = 100.0
            step = 2.0 if self.symbol == "NVDA" else 0.2
            closes = [base + index * step for index in range(80)]
            quote = {
                "open": closes,
                "high": [price + 1.0 for price in closes],
                "low": [price - 1.0 for price in closes],
                "close": closes,
                "volume": [1000 + index for index in range(80)],
            }
            payload = {"chart": {"result": [{"timestamp": timestamps, "indicators": {"quote": [quote]}}], "error": None}}
            return json.dumps(payload).encode("utf-8")

    def fake_urlopen(request: object, **_kwargs: object) -> FakeResponse:
        url = getattr(request, "full_url", str(request))
        symbol = "SMH" if "/SMH?" in url else "NVDA"
        return FakeResponse(symbol)

    monkeypatch.setattr("sectorscout.hindsight.urlopen", fake_urlopen)
    summary = fetch_hindsight_prices(config, path=case_file, lookback_days=90)

    assert [row["symbol"] for row in summary] == ["NVDA", "SMH"]
    assert summary[1]["symbol_type"] == "benchmark"
    assert summary[1]["for_cases"] == "NVDA"
    seed_hindsight_events(config, path=case_file)
    gates = build_hindsight_replay_gates(config, path=case_file, persist=False, asof_date=date(2024, 12, 31))
    rs_gate = next(gate for gate in gates if gate.gate_name == "Benchmark RS vs SMH")
    assert rs_gate.gate_status == "PASS"
    assert "benchmark_rows=0" not in rs_gate.computed_value
