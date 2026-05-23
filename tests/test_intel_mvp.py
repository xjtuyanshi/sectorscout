from __future__ import annotations

import ipaddress
from datetime import date
from pathlib import Path

import pytest

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.demo import demo_readiness, run_demo_init
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
