from __future__ import annotations

import csv
import json
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.db import connect_database
from sectorscout.metadata import get_git_commit


DEFAULT_HINDSIGHT_CASES_PATH = Path("data/hindsight/leader_cases.csv")


@dataclass(frozen=True)
class HindsightCase:
    symbol: str
    label: str
    start_date: date
    end_date: date
    theme: str
    hindsight_reason: str
    anchor_event: str
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["start_date"] = self.start_date.isoformat()
        payload["end_date"] = self.end_date.isoformat()
        return payload


@dataclass(frozen=True)
class HindsightResult:
    scan_id: str
    symbol: str
    label: str
    scan_start: date
    scan_end: date
    price_rows: int
    start_close: float | None
    end_close: float | None
    max_close: float | None
    peak_date: date | None
    max_gain_pct: float | None
    max_drawdown_pct: float | None
    rs_percentile_start: float | None
    hindsight_score: float
    flags: dict[str, Any]
    data_quality: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["scan_start"] = self.scan_start.isoformat()
        payload["scan_end"] = self.scan_end.isoformat()
        payload["peak_date"] = self.peak_date.isoformat() if self.peak_date else None
        payload["flags_json"] = json.dumps(self.flags, sort_keys=True)
        return payload


def default_hindsight_cases() -> list[HindsightCase]:
    return [
        HindsightCase(
            symbol="NVDA",
            label="NVDA 2024 AI infrastructure leader",
            start_date=date(2024, 1, 2),
            end_date=date(2024, 12, 31),
            theme="AI semiconductors",
            hindsight_reason="Study whether SectorScout-style theme strength, relative strength, and setup structure would have surfaced the 2024 leader early enough.",
            anchor_event="AI accelerator demand and repeated earnings revisions.",
            source_url="https://www.nvidia.com/en-us/data-center/",
        ),
        HindsightCase(
            symbol="MU",
            label="MU 2025-2026 AI memory cycle",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 5, 29),
            theme="AI memory and HBM",
            hindsight_reason="Study whether a memory-cycle leader would be caught by theme rotation, relative strength, and fundamental acceleration signals.",
            anchor_event="AI server memory demand and high-bandwidth memory cycle.",
            source_url="https://www.micron.com/products/memory/hbm",
        ),
        HindsightCase(
            symbol="SNDK",
            label="SNDK 2025-2026 storage cycle",
            start_date=date(2025, 2, 24),
            end_date=date(2026, 5, 29),
            theme="Storage and NAND cycle",
            hindsight_reason="Study whether a storage-cycle case would appear as external-only context first, then graduate into internal ranking after price and theme evidence improved.",
            anchor_event="Standalone SanDisk trading history and NAND/storage cycle.",
            source_url="https://www.sandisk.com/",
        ),
        HindsightCase(
            symbol="LITE",
            label="LITE 2025-2026 optical AI infrastructure",
            start_date=date(2025, 1, 2),
            end_date=date(2026, 5, 29),
            theme="Optical networking and AI infrastructure",
            hindsight_reason="Study whether optical networking beneficiaries show up through relative strength, theme breadth, and setup candidates.",
            anchor_event="AI data-center optical component demand.",
            source_url="https://www.lumentum.com/en/markets/cloud-data-center",
        ),
    ]


def ensure_hindsight_tables(config: SectorScoutConfig) -> None:
    with connect_database(config.database.path) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_case_studies (
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                start_date DATE NOT NULL,
                end_date DATE NOT NULL,
                theme VARCHAR NOT NULL,
                hindsight_reason VARCHAR NOT NULL,
                anchor_event VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                created_at_utc TIMESTAMPTZ NOT NULL,
                PRIMARY KEY (symbol, label, start_date)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_scan_results (
                scan_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                scan_start DATE NOT NULL,
                scan_end DATE NOT NULL,
                price_rows INTEGER NOT NULL,
                start_close DOUBLE,
                end_close DOUBLE,
                max_close DOUBLE,
                peak_date DATE,
                max_gain_pct DOUBLE,
                max_drawdown_pct DOUBLE,
                rs_percentile_start DOUBLE,
                hindsight_score DOUBLE NOT NULL,
                flags_json VARCHAR NOT NULL,
                data_quality VARCHAR NOT NULL,
                notes VARCHAR NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (scan_id, symbol, label)
            )
            """
        )


def load_hindsight_cases(path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> list[HindsightCase]:
    if not path.exists():
        return default_hindsight_cases()
    cases: list[HindsightCase] = []
    with path.open("r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            cases.append(
                HindsightCase(
                    symbol=str(row["symbol"]).upper(),
                    label=str(row["label"]),
                    start_date=date.fromisoformat(str(row["start_date"])),
                    end_date=date.fromisoformat(str(row["end_date"])),
                    theme=str(row["theme"]),
                    hindsight_reason=str(row["hindsight_reason"]),
                    anchor_event=str(row["anchor_event"]),
                    source_url=str(row.get("source_url") or ""),
                )
            )
    return cases


def write_default_hindsight_cases(path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    cases = default_hindsight_cases()
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(cases[0].to_dict()))
        writer.writeheader()
        for case in cases:
            writer.writerow(case.to_dict())
    return path


def seed_hindsight_cases(config: SectorScoutConfig, path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> int:
    ensure_hindsight_tables(config)
    if not path.exists():
        write_default_hindsight_cases(path)
    cases = load_hindsight_cases(path)
    now = datetime.now(timezone.utc)
    with connect_database(config.database.path) as connection:
        for case in cases:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_case_studies (
                    symbol, label, start_date, end_date, theme, hindsight_reason,
                    anchor_event, source_url, created_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    case.symbol,
                    case.label,
                    case.start_date,
                    case.end_date,
                    case.theme,
                    case.hindsight_reason,
                    case.anchor_event,
                    case.source_url,
                    now,
                ],
            )
    return len(cases)


def scan_hindsight_cases(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    persist: bool = True,
) -> list[HindsightResult]:
    ensure_hindsight_tables(config)
    cases = load_hindsight_cases(path)
    scan_id = str(uuid.uuid4())
    results = [_scan_case(config, case, scan_id=scan_id) for case in cases]
    if persist:
        _persist_results(config, results)
    return results


def latest_hindsight_results(config: SectorScoutConfig, *, limit: int = 100) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_scan_results
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC, scan_id DESC) = 1
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def historical_pattern_summary(
    cases: list[HindsightCase],
    results: list[HindsightResult] | pd.DataFrame,
) -> dict[str, list[dict[str, Any]]]:
    result_rows = _normalize_result_rows(results)
    result_by_symbol = {str(row["symbol"]).upper(): row for row in result_rows}
    industry_rows: list[dict[str, Any]] = []
    by_theme: dict[str, list[HindsightCase]] = {}
    for case in cases:
        by_theme.setdefault(case.theme, []).append(case)
    for theme, theme_cases in sorted(by_theme.items()):
        matched_results = [result_by_symbol.get(case.symbol) for case in theme_cases]
        usable_results = [row for row in matched_results if row and row.get("data_quality") != "missing_price_history"]
        industry_rows.append(
            {
                "Industry / theme pattern": theme,
                "Symbols": ", ".join(case.symbol for case in theme_cases),
                "What to study": "; ".join(sorted({case.anchor_event for case in theme_cases})),
                "Cases with price data": len(usable_results),
                "Status": "ready_to_analyze" if usable_results else "needs_historical_data",
            }
        )
    technical_checks = {
        "High relative strength near start": "rs_80_at_start",
        "Stage 2 trend proxy": "stage2_proxy",
        "Breakout / new-high proxy": "breakout_proxy",
        "Volume expansion proxy": "volume_expansion_proxy",
        "Large post-start advance": "gain_100pct",
    }
    technical_rows: list[dict[str, Any]] = []
    usable = [row for row in result_rows if row.get("data_quality") != "missing_price_history"]
    for label, flag in technical_checks.items():
        true_count = sum(1 for row in usable if _flags_from_row(row).get(flag))
        technical_rows.append(
            {
                "Technical pattern to test": label,
                "Cases matching": true_count,
                "Cases with price data": len(usable),
                "Status": "needs_historical_data" if not usable else "ready_to_compare",
            }
        )
    return {"industry_patterns": industry_rows, "technical_patterns": technical_rows}


def _scan_case(config: SectorScoutConfig, case: HindsightCase, *, scan_id: str) -> HindsightResult:
    prices = _price_rows(config, case)
    rs_start = _rs_percentile_near_start(config, case)
    if prices.empty:
        return HindsightResult(
            scan_id=scan_id,
            symbol=case.symbol,
            label=case.label,
            scan_start=case.start_date,
            scan_end=case.end_date,
            price_rows=0,
            start_close=None,
            end_close=None,
            max_close=None,
            peak_date=None,
            max_gain_pct=None,
            max_drawdown_pct=None,
            rs_percentile_start=rs_start,
            hindsight_score=0.0,
            flags={"has_price_history": False, "requires_data_load": True},
            data_quality="missing_price_history",
            notes="No price rows found for this case window. Load historical prices before interpreting the case.",
        )
    prices = prices.sort_values("price_date").reset_index(drop=True)
    start_close = float(prices.iloc[0]["adj_close"])
    end_close = float(prices.iloc[-1]["adj_close"])
    peak_index = int(prices["adj_close"].idxmax())
    max_close = float(prices.loc[peak_index, "adj_close"])
    peak_date = prices.loc[peak_index, "price_date"]
    peak_date = peak_date.date() if hasattr(peak_date, "date") else date.fromisoformat(str(peak_date)[:10])
    max_gain_pct = ((max_close / start_close) - 1.0) * 100 if start_close else None
    max_drawdown_pct = _max_drawdown_pct(prices["adj_close"])
    flags = _case_flags(prices, max_gain_pct=max_gain_pct, rs_percentile_start=rs_start)
    score = _hindsight_score(flags)
    return HindsightResult(
        scan_id=scan_id,
        symbol=case.symbol,
        label=case.label,
        scan_start=case.start_date,
        scan_end=case.end_date,
        price_rows=len(prices),
        start_close=start_close,
        end_close=end_close,
        max_close=max_close,
        peak_date=peak_date,
        max_gain_pct=max_gain_pct,
        max_drawdown_pct=max_drawdown_pct,
        rs_percentile_start=rs_start,
        hindsight_score=score,
        flags=flags,
        data_quality="ok" if len(prices) >= 60 else "thin_price_history",
        notes=_case_notes(flags),
    )


def _normalize_result_rows(results: list[HindsightResult] | pd.DataFrame) -> list[dict[str, Any]]:
    if isinstance(results, pd.DataFrame):
        return [row.to_dict() for _, row in results.iterrows()]
    return [result.to_dict() for result in results]


def _flags_from_row(row: dict[str, Any]) -> dict[str, Any]:
    raw = row.get("flags") or row.get("flags_json") or "{}"
    if isinstance(raw, dict):
        return raw
    try:
        parsed = json.loads(str(raw))
    except Exception:
        parsed = {}
    return parsed if isinstance(parsed, dict) else {}


def _price_rows(config: SectorScoutConfig, case: HindsightCase) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        try:
            return connection.execute(
                """
                SELECT price_date, adj_close, adj_volume, provider
                FROM daily_prices
                WHERE symbol = ?
                  AND price_date BETWEEN ? AND ?
                QUALIFY row_number() OVER (PARTITION BY price_date ORDER BY provider) = 1
                ORDER BY price_date
                """,
                [case.symbol, case.start_date, case.end_date],
            ).fetchdf()
        except Exception:
            return pd.DataFrame()


def _rs_percentile_near_start(config: SectorScoutConfig, case: HindsightCase) -> float | None:
    with connect_database(config.database.path) as connection:
        try:
            row = connection.execute(
                """
                SELECT rs_percentile
                FROM technical_indicators
                WHERE symbol = ? AND asof_date <= ?
                ORDER BY asof_date DESC
                LIMIT 1
                """,
                [case.symbol, case.start_date],
            ).fetchone()
        except Exception:
            row = None
    return float(row[0]) if row and row[0] is not None else None


def _case_flags(prices: pd.DataFrame, *, max_gain_pct: float | None, rs_percentile_start: float | None) -> dict[str, Any]:
    close = prices["adj_close"].astype(float)
    volume = prices["adj_volume"].astype(float)
    sma50 = close.rolling(50, min_periods=20).mean()
    sma200 = close.rolling(200, min_periods=80).mean()
    high20 = close.rolling(20, min_periods=10).max()
    stage2_proxy = bool(((close > sma50) & (close > sma200)).tail(30).any()) if len(prices) >= 80 else False
    breakout_proxy = bool((close > high20.shift(1)).tail(max(len(close) - 20, 1)).any()) if len(prices) >= 20 else False
    volume_expansion_proxy = bool((volume > volume.rolling(50, min_periods=20).mean() * 1.3).tail(max(len(volume) - 20, 1)).any()) if len(prices) >= 20 else False
    return {
        "has_price_history": True,
        "enough_history_60d": len(prices) >= 60,
        "gain_100pct": bool(max_gain_pct is not None and max_gain_pct >= 100),
        "gain_200pct": bool(max_gain_pct is not None and max_gain_pct >= 200),
        "gain_300pct": bool(max_gain_pct is not None and max_gain_pct >= 300),
        "rs_80_at_start": bool(rs_percentile_start is not None and rs_percentile_start >= 80),
        "stage2_proxy": stage2_proxy,
        "breakout_proxy": breakout_proxy,
        "volume_expansion_proxy": volume_expansion_proxy,
    }


def _hindsight_score(flags: dict[str, Any]) -> float:
    weights = {
        "has_price_history": 10,
        "enough_history_60d": 10,
        "gain_100pct": 20,
        "gain_200pct": 15,
        "gain_300pct": 10,
        "rs_80_at_start": 15,
        "stage2_proxy": 15,
        "breakout_proxy": 10,
        "volume_expansion_proxy": 5,
    }
    return float(sum(weight for flag, weight in weights.items() if flags.get(flag)))


def _case_notes(flags: dict[str, Any]) -> str:
    missing = [flag for flag, value in flags.items() if not value]
    if not missing:
        return "All current hindsight diagnostics are present. Treat this as a case-study candidate, not a strategy result."
    return "Missing or unconfirmed diagnostics: " + ", ".join(missing)


def _max_drawdown_pct(close: pd.Series) -> float | None:
    if close.empty:
        return None
    running_max = close.cummax()
    drawdowns = (close / running_max - 1.0) * 100
    return float(drawdowns.min())


def _persist_results(config: SectorScoutConfig, results: list[HindsightResult]) -> None:
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for result in results:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_scan_results (
                    scan_id, symbol, label, scan_start, scan_end, price_rows,
                    start_close, end_close, max_close, peak_date, max_gain_pct,
                    max_drawdown_pct, rs_percentile_start, hindsight_score,
                    flags_json, data_quality, notes, generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    result.scan_id,
                    result.symbol,
                    result.label,
                    result.scan_start,
                    result.scan_end,
                    result.price_rows,
                    result.start_close,
                    result.end_close,
                    result.max_close,
                    result.peak_date,
                    result.max_gain_pct,
                    result.max_drawdown_pct,
                    result.rs_percentile_start,
                    result.hindsight_score,
                    json.dumps(result.flags, sort_keys=True),
                    result.data_quality,
                    result.notes,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )
