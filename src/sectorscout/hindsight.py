from __future__ import annotations

import csv
import json
import os
import uuid
from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

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


@dataclass(frozen=True)
class HindsightPatternObservation:
    observation_id: str
    scan_id: str
    symbol: str
    label: str
    observation_group: str
    pattern_name: str
    observation_value: str
    status: str
    evidence: str
    source: str
    extraction_method: str
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_pattern_observations (
                observation_id VARCHAR NOT NULL,
                scan_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                observation_group VARCHAR NOT NULL,
                pattern_name VARCHAR NOT NULL,
                observation_value VARCHAR NOT NULL,
                status VARCHAR NOT NULL,
                evidence VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                extraction_method VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (observation_id)
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


def fetch_hindsight_prices(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    provider: str = "yahoo_chart_public",
    timeout: int = 20,
) -> list[dict[str, Any]]:
    cases = load_hindsight_cases(path)
    summaries: list[dict[str, Any]] = []
    for case in cases:
        if provider == "stooq_public":
            rows = fetch_stooq_daily_rows(case.symbol, case.start_date, case.end_date, timeout=timeout)
        elif provider == "yahoo_chart_public":
            rows = fetch_yahoo_chart_daily_rows(case.symbol, case.start_date, case.end_date, timeout=timeout)
        else:
            raise ValueError(f"Unsupported hindsight price provider: {provider}")
        inserted = _insert_price_rows(config, case.symbol, rows, provider=provider)
        summaries.append(
            {
                "symbol": case.symbol,
                "label": case.label,
                "start_date": case.start_date.isoformat(),
                "end_date": case.end_date.isoformat(),
                "provider": provider,
                "rows_fetched": len(rows),
                "rows_inserted": inserted,
                "data_quality_note": (
                    "Public daily price rows loaded; corporate-action adjustment status is provider-dependent."
                    if inserted
                    else "No public daily price rows returned for this case window."
                ),
            }
        )
    return summaries


def fetch_stooq_daily_rows(symbol: str, start: date, end: date, *, timeout: int = 20) -> list[dict[str, Any]]:
    params = {
        "s": f"{symbol.lower()}.us",
        "d1": start.strftime("%Y%m%d"),
        "d2": end.strftime("%Y%m%d"),
        "i": "d",
    }
    if api_key := os.environ.get("STOOQ_API_KEY"):
        params["apikey"] = api_key
    query = urlencode(params)
    url = f"https://stooq.com/q/d/l/?{query}"
    with urlopen(url, timeout=timeout) as response:  # nosec B310 - public CSV endpoint, no credentials.
        text = response.read().decode("utf-8")
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2 or lines[0].lower().startswith("no data"):
        return []
    parsed: list[dict[str, Any]] = []
    for row in csv.DictReader(lines):
        if not row.get("Date"):
            continue
        parsed.append(
            {
                "price_date": date.fromisoformat(row["Date"]),
                "open": float(row["Open"]),
                "high": float(row["High"]),
                "low": float(row["Low"]),
                "close": float(row["Close"]),
                "volume": int(float(row["Volume"])),
            }
        )
    return parsed


def fetch_yahoo_chart_daily_rows(symbol: str, start: date, end: date, *, timeout: int = 20) -> list[dict[str, Any]]:
    period1 = int(datetime.combine(start, time.min, tzinfo=timezone.utc).timestamp())
    period2 = int(datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc).timestamp())
    query = urlencode({"period1": period1, "period2": period2, "interval": "1d", "events": "history"})
    url = f"https://query2.finance.yahoo.com/v8/finance/chart/{symbol.upper()}?{query}"
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 SectorScout/0.1"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - public chart JSON endpoint, no credentials.
        payload = json.loads(response.read().decode("utf-8"))
    result = (payload.get("chart", {}).get("result") or [None])[0]
    if not result:
        return []
    timestamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    parsed: list[dict[str, Any]] = []
    for index, timestamp in enumerate(timestamps):
        try:
            open_price = quote.get("open", [])[index]
            high = quote.get("high", [])[index]
            low = quote.get("low", [])[index]
            close = quote.get("close", [])[index]
            volume = quote.get("volume", [])[index]
        except IndexError:
            continue
        if any(value is None for value in [open_price, high, low, close, volume]):
            continue
        parsed.append(
            {
                "price_date": datetime.fromtimestamp(int(timestamp), tz=timezone.utc).date(),
                "open": float(open_price),
                "high": float(high),
                "low": float(low),
                "close": float(close),
                "volume": int(volume),
            }
        )
    return parsed


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
        _persist_pattern_observations(config, build_hindsight_pattern_observations(cases, results))
    return results


def _insert_price_rows(
    config: SectorScoutConfig,
    symbol: str,
    rows: list[dict[str, Any]],
    *,
    provider: str,
) -> int:
    if not rows:
        return 0
    now = datetime.now(timezone.utc)
    with connect_database(config.database.path) as connection:
        for row in rows:
            connection.execute(
                """
                INSERT OR REPLACE INTO daily_prices (
                    symbol, price_date, open, high, low, close, volume,
                    adj_open, adj_high, adj_low, adj_close, adj_volume,
                    provider, is_adjusted, adjustment_warning, ingested_at_utc
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    symbol.upper(),
                    row["price_date"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    row["volume"],
                    provider,
                    False,
                    True,
                    now,
                ],
            )
    return len(rows)


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


def latest_hindsight_pattern_observations(config: SectorScoutConfig, *, limit: int = 500) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_pattern_observations
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC, scan_id DESC) = 1
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_pattern_observations(
    cases: list[HindsightCase],
    results: list[HindsightResult] | pd.DataFrame,
) -> list[HindsightPatternObservation]:
    result_rows = _normalize_result_rows(results)
    result_by_symbol = {str(row["symbol"]).upper(): row for row in result_rows}
    observations: list[HindsightPatternObservation] = []
    for case in cases:
        row = result_by_symbol.get(case.symbol)
        scan_id = str(row.get("scan_id") if row else "unscanned")
        observations.extend(_industry_observations(case, scan_id=scan_id))
        observations.extend(_manual_required_observations(case, scan_id=scan_id))
        observations.extend(_technical_observations(case, row, scan_id=scan_id))
    return observations


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


def _industry_observations(case: HindsightCase, *, scan_id: str) -> list[HindsightPatternObservation]:
    cluster = _industry_cluster(case.theme)
    return [
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="industry",
            pattern_name="Theme taxonomy seed",
            observation_value=case.theme,
            status="hypothesis_seed",
            evidence="Case metadata theme. Review whether this theme label was knowable at the study date.",
            source=case.source_url,
            extraction_method="case_metadata_manual",
            requires_review=True,
        ),
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="industry",
            pattern_name="Industry cluster",
            observation_value=cluster,
            status="hypothesis_seed",
            evidence=f"Theme '{case.theme}' mapped to cluster '{cluster}'.",
            source=case.source_url,
            extraction_method="keyword_theme_mapping_v1",
            requires_review=True,
        ),
    ]


def _manual_required_observations(case: HindsightCase, *, scan_id: str) -> list[HindsightPatternObservation]:
    return [
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="manual_or_llm_required",
            pattern_name="Catalyst narrative",
            observation_value=case.anchor_event,
            status="needs_manual_review",
            evidence="Catalyst context is narrative and source-dependent; it should not be inferred from OHLCV alone.",
            source=case.source_url,
            extraction_method="case_metadata_manual",
            requires_review=True,
        ),
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="manual_or_llm_required",
            pattern_name="Point-in-time theme discoverability",
            observation_value="unknown_until_reviewed",
            status="needs_manual_review",
            evidence="Reviewer must decide whether the theme was discoverable from public information before or during the case window.",
            source=case.source_url,
            extraction_method="review_required",
            requires_review=True,
        ),
    ]


def _technical_observations(
    case: HindsightCase,
    row: dict[str, Any] | None,
    *,
    scan_id: str,
) -> list[HindsightPatternObservation]:
    flags = _flags_from_row(row or {})
    if not row or row.get("data_quality") == "missing_price_history":
        return [
            _observation(
                scan_id=scan_id,
                case=case,
                observation_group="technical",
                pattern_name="OHLCV coverage",
                observation_value="missing",
                status="needs_historical_data",
                evidence="No price rows are loaded for the case window.",
                source="daily_prices",
                extraction_method="ohlcv_coverage_check",
                requires_review=False,
            )
        ]
    enough_history = bool(flags.get("enough_history_60d"))
    return [
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="Relative strength near case start",
            flag_name="rs_80_at_start",
            observed_value=_format_metric(row.get("rs_percentile_start"), suffix=" percentile"),
            observed_evidence="Latest technical indicator before or at the case start has RS percentile >= 80.",
            missing_evidence="RS percentile is missing or below the study threshold near case start.",
            requires_review=row.get("rs_percentile_start") is None,
        ),
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="Stage 2 trend proxy",
            flag_name="stage2_proxy",
            observed_value=str(flags.get("stage2_days_last_30", 0)) + " of last 30 sessions",
            observed_evidence="Close was above both 50-day and 200-day moving averages during the final 30 sessions of the case window.",
            missing_evidence="Trend proxy was not observed, or price history is too thin for this check.",
            force_needs_data=not enough_history,
        ),
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="New-high / breakout proxy",
            flag_name="breakout_proxy",
            observed_value=str(flags.get("breakout_days", 0)) + " breakout proxy days",
            observed_evidence="Close exceeded the prior 20-session high at least once after the initial 20 sessions.",
            missing_evidence="No prior-20-session closing high proxy was observed in the loaded window.",
            force_needs_data=not flags.get("has_price_history"),
        ),
        _technical_flag_observation(
            case,
            row,
            scan_id=scan_id,
            pattern_name="Volume expansion proxy",
            flag_name="volume_expansion_proxy",
            observed_value=str(flags.get("volume_expansion_days", 0)) + " expansion proxy days",
            observed_evidence="Volume exceeded 1.3x its 50-session average at least once after the initial window.",
            missing_evidence="No volume expansion proxy was observed in the loaded window.",
            force_needs_data=not enough_history,
        ),
        _observation(
            scan_id=scan_id,
            case=case,
            observation_group="technical",
            pattern_name="Case-window outcome descriptor",
            observation_value=_format_metric(row.get("max_gain_pct"), suffix="% largest advance"),
            status="outcome_only_not_predictive",
            evidence="This describes what happened inside the selected case window. It is not a screening rule.",
            source="daily_prices",
            extraction_method="case_window_path_summary",
            requires_review=True,
        ),
    ]


def _technical_flag_observation(
    case: HindsightCase,
    row: dict[str, Any],
    *,
    scan_id: str,
    pattern_name: str,
    flag_name: str,
    observed_value: str,
    observed_evidence: str,
    missing_evidence: str,
    force_needs_data: bool = False,
    requires_review: bool = False,
) -> HindsightPatternObservation:
    flags = _flags_from_row(row)
    if force_needs_data:
        status = "needs_historical_data"
        value = "insufficient_history"
        evidence = missing_evidence
    elif flags.get(flag_name):
        status = "observed_hypothesis_feature"
        value = observed_value
        evidence = observed_evidence
    else:
        status = "not_observed_in_case_window"
        value = "not_observed"
        evidence = missing_evidence
    return _observation(
        scan_id=scan_id,
        case=case,
        observation_group="technical",
        pattern_name=pattern_name,
        observation_value=value,
        status=status,
        evidence=evidence,
        source="daily_prices",
        extraction_method="daily_ohlcv_rule_v1",
        requires_review=requires_review,
    )


def _observation(
    *,
    scan_id: str,
    case: HindsightCase,
    observation_group: str,
    pattern_name: str,
    observation_value: str,
    status: str,
    evidence: str,
    source: str,
    extraction_method: str,
    requires_review: bool,
) -> HindsightPatternObservation:
    observation_key = "|".join([scan_id, case.symbol, observation_group, pattern_name])
    return HindsightPatternObservation(
        observation_id=str(uuid.uuid5(uuid.NAMESPACE_URL, observation_key)),
        scan_id=scan_id,
        symbol=case.symbol,
        label=case.label,
        observation_group=observation_group,
        pattern_name=pattern_name,
        observation_value=observation_value,
        status=status,
        evidence=evidence,
        source=source,
        extraction_method=extraction_method,
        requires_review=requires_review,
    )


def _industry_cluster(theme: str) -> str:
    lowered = theme.lower()
    if "memory" in lowered or "hbm" in lowered:
        return "AI infrastructure / memory and HBM"
    if "storage" in lowered or "nand" in lowered:
        return "AI infrastructure / storage and NAND"
    if "optical" in lowered or "network" in lowered:
        return "AI infrastructure / optical networking"
    if "semiconductor" in lowered or "chip" in lowered:
        return "AI infrastructure / semiconductors"
    if "ai" in lowered:
        return "AI infrastructure / other"
    return "Other industry theme"


def _format_metric(value: object, *, suffix: str = "") -> str:
    if value is None or pd.isna(value):
        return "missing"
    try:
        text = f"{float(value):.2f}"
    except Exception:
        text = str(value)
    return text + suffix


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
    stage2_series = (close > sma50) & (close > sma200)
    breakout_series = close > high20.shift(1)
    volume_expansion_series = volume > volume.rolling(50, min_periods=20).mean() * 1.3
    stage2_days_last_30 = int(stage2_series.tail(30).sum()) if len(prices) >= 80 else 0
    breakout_days = int(breakout_series.tail(max(len(close) - 20, 1)).sum()) if len(prices) >= 20 else 0
    volume_expansion_days = int(volume_expansion_series.tail(max(len(volume) - 20, 1)).sum()) if len(prices) >= 20 else 0
    stage2_proxy = bool(stage2_days_last_30 > 0)
    breakout_proxy = bool(breakout_days > 0)
    volume_expansion_proxy = bool(volume_expansion_days > 0)
    above_50d_pct = float(stage2_series.fillna(False).sum() / len(stage2_series) * 100) if len(prices) else 0.0
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
        "stage2_days_last_30": stage2_days_last_30,
        "breakout_days": breakout_days,
        "volume_expansion_days": volume_expansion_days,
        "above_50d_and_200d_pct": round(above_50d_pct, 2),
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


def _persist_pattern_observations(config: SectorScoutConfig, observations: list[HindsightPatternObservation]) -> None:
    if not observations:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for observation in observations:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_pattern_observations (
                    observation_id, scan_id, symbol, label, observation_group,
                    pattern_name, observation_value, status, evidence, source,
                    extraction_method, requires_review, generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    observation.observation_id,
                    observation.scan_id,
                    observation.symbol,
                    observation.label,
                    observation.observation_group,
                    observation.pattern_name,
                    observation.observation_value,
                    observation.status,
                    observation.evidence,
                    observation.source,
                    observation.extraction_method,
                    observation.requires_review,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )
