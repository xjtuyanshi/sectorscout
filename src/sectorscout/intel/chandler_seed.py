from __future__ import annotations

from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig
from sectorscout.intel.storage import insert_raw_item, insert_trade_view, trade_view_exists
from sectorscout.intel.text_extract import extract_trade_view, normalize_text


CHANDLER_FIXTURE_PATH = Path("data/intel/fixtures/chandler_2026_04_26.md")
PACKAGE_CHANDLER_FIXTURE_PATH = Path(__file__).with_name("fixtures") / "chandler_2026_04_26.md"
CHANDLER_URL = "https://chandlertrades.com/2026/04/26/weekend-prep-april-26-2/"


def load_chandler_fixture(path: str | Path | None = None) -> str:
    candidates = [Path(path)] if path is not None else [CHANDLER_FIXTURE_PATH, PACKAGE_CHANDLER_FIXTURE_PATH]
    for candidate in candidates:
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    raise FileNotFoundError(f"Chandler fixture not found in: {', '.join(str(candidate) for candidate in candidates)}")


def seed_chandler_fixture(config: SectorScoutConfig, *, asof_date: date | None = None) -> str:
    raw = load_chandler_fixture()
    raw_item_id = insert_raw_item(
        config,
        source_id="chandler_2026_04_26",
        source_type="public_web_fixture",
        title="Weekend Prep - April 26",
        author="Chandler Trades",
        platform="website",
        url=CHANDLER_URL,
        published_at="2026-04-26T00:00:00+00:00",
        asof_date=asof_date or date(2026, 4, 26),
        raw_text=raw,
        normalized_text=normalize_text(raw),
        rights_scope="public",
        collection_method="fixture_seed",
        metadata={"fixture": str(CHANDLER_FIXTURE_PATH)},
    )
    if not trade_view_exists(config, raw_item_id, "rule_text_v1"):
        draft = extract_trade_view(
            raw,
            source_id="chandler_2026_04_26",
            source_type="public_web_fixture",
            source_title="Weekend Prep - April 26",
            author="Chandler Trades",
            platform="website",
            url=CHANDLER_URL,
            asof_date=asof_date or date(2026, 4, 26),
            published_at="2026-04-26T00:00:00+00:00",
            rights_scope="public",
            requires_review=False,
        )
        insert_trade_view(config, raw_item_id=raw_item_id, draft=draft)
    return raw_item_id
