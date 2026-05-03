from __future__ import annotations

from pathlib import Path

import pytest

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database, initialize_database
from sectorscout.intel.chandler_seed import load_chandler_fixture, seed_chandler_fixture
from sectorscout.intel.manual_inbox import parse_manual_markdown
from sectorscout.intel.overlap import classify_overlap
from sectorscout.intel.public_sources import collect_public_sources, load_public_sources
from sectorscout.intel.public_web import collect_public_url
from sectorscout.intel.storage import (
    ensure_intel_tables,
    insert_note,
    insert_review_mark,
    mark_image_observation_reviewed,
    save_media_bytes,
)
from sectorscout.intel.symbol_normalize import normalize_symbols, related_symbols
from sectorscout.intel.text_extract import extract_trade_view
from sectorscout.intel.vision_extract import extract_image_observation


def _config(tmp_path: Path) -> SectorScoutConfig:
    config = SectorScoutConfig.model_validate({"database": {"path": tmp_path / "test.duckdb"}})
    initialize_database(config)
    ensure_intel_tables(config)
    return config


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


def test_vision_provider_creates_review_required_draft_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    config = _config(tmp_path)
    media_id = save_media_bytes(
        config,
        b"\x89PNG\r\n\x1a\nprovider",
        filename="provider.png",
        raw_item_id=None,
        source_id="manual_image",
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


def test_seed_chandler_fixture_deduplicates_trade_view(tmp_path: Path) -> None:
    config = _config(tmp_path)
    seed_chandler_fixture(config)
    seed_chandler_fixture(config)
    with connect_database(config.database.path) as connection:
        raw_count = connection.execute("SELECT COUNT(*) FROM intel_raw_items").fetchone()[0]
        view_count = connection.execute("SELECT COUNT(*) FROM intel_trade_views").fetchone()[0]
    assert raw_count == 1
    assert view_count == 1


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


def test_public_source_collection_extracts_rule_view(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config = _config(tmp_path)
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

    def fake_fetch(_url: str) -> tuple[str, str]:
        return (
            "text/html",
            "<html><title>Public Blog</title><body>NQ demand around 27300. Wait for bullish reaction.</body></html>",
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


def test_symbol_normalization_required_aliases() -> None:
    assert related_symbols("ES") == ["ES", "SPX", "SPY"]
    assert related_symbols("NQ") == ["NQ", "NDX", "QQQ"]
    assert related_symbols("Gold") == ["GC", "GLD"]
    assert normalize_symbols(["ES", "NQ", "Gold"]) == ["ES", "SPX", "SPY", "NQ", "NDX", "QQQ", "GC", "GLD"]


def test_overlap_labels() -> None:
    assert classify_overlap(has_internal=True, external_direction="bullish") == "CONFIRMED"
    assert classify_overlap(has_internal=True, external_direction="bearish") == "CONFLICT"
    assert classify_overlap(has_internal=False, external_direction="conditional") == "EXTERNAL_ONLY"
    assert classify_overlap(has_internal=True, external_direction=None, requires_review=True) == "NEEDS_REVIEW"


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


def test_forbidden_automation_modes_are_not_configured() -> None:
    source = "\n".join(path.read_text(encoding="utf-8") for path in Path("src/sectorscout/intel").glob("*.py"))
    forbidden = ["self_bot_mode", "personal_discord_token", "discord_cookie", "private_channel_crawler"]
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
