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


@dataclass(frozen=True)
class HindsightEvent:
    event_id: str
    symbol: str
    label: str
    event_type: str
    event_date: date
    published_at_utc: datetime | None
    market_session: str
    source_url: str
    source_quality: str
    evidence_type: str
    evidence_summary: str
    fundamental_evidence_available_at: datetime | None
    first_tradable_date: date | None
    first_tradable_bar_policy: str
    technical_replay_as_of: date | None
    timing_status: str
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["event_date"] = self.event_date.isoformat()
        payload["published_at_utc"] = self.published_at_utc.isoformat() if self.published_at_utc else None
        payload["fundamental_evidence_available_at"] = (
            self.fundamental_evidence_available_at.isoformat()
            if self.fundamental_evidence_available_at
            else None
        )
        payload["first_tradable_date"] = self.first_tradable_date.isoformat() if self.first_tradable_date else None
        payload["technical_replay_as_of"] = self.technical_replay_as_of.isoformat() if self.technical_replay_as_of else None
        return payload


@dataclass(frozen=True)
class HindsightReplayGate:
    gate_id: str
    event_id: str
    symbol: str
    label: str
    gate_group: str
    gate_name: str
    gate_status: str
    formula: str
    computed_value: str
    threshold: str
    data_used: str
    required_rows: int
    available_rows: int
    missing_detail: str
    reason: str
    asof_date: date
    first_tradable_date: date | None
    source: str
    requires_review: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["asof_date"] = self.asof_date.isoformat()
        payload["first_tradable_date"] = self.first_tradable_date.isoformat() if self.first_tradable_date else None
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
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_event_ledger (
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                event_type VARCHAR NOT NULL,
                event_date DATE NOT NULL,
                published_at_utc TIMESTAMPTZ,
                market_session VARCHAR NOT NULL,
                source_url VARCHAR NOT NULL,
                source_quality VARCHAR NOT NULL,
                evidence_type VARCHAR NOT NULL,
                evidence_summary VARCHAR NOT NULL,
                fundamental_evidence_available_at TIMESTAMPTZ,
                first_tradable_date DATE,
                first_tradable_bar_policy VARCHAR NOT NULL,
                technical_replay_as_of DATE,
                timing_status VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (event_id)
            )
            """
        )
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS hindsight_replay_gates (
                gate_id VARCHAR NOT NULL,
                event_id VARCHAR NOT NULL,
                symbol VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                gate_group VARCHAR NOT NULL,
                gate_name VARCHAR NOT NULL,
                gate_status VARCHAR NOT NULL,
                formula VARCHAR NOT NULL,
                computed_value VARCHAR NOT NULL,
                threshold VARCHAR NOT NULL,
                data_used VARCHAR NOT NULL,
                required_rows INTEGER NOT NULL,
                available_rows INTEGER NOT NULL,
                missing_detail VARCHAR NOT NULL,
                reason VARCHAR NOT NULL,
                asof_date DATE NOT NULL,
                first_tradable_date DATE,
                source VARCHAR NOT NULL,
                requires_review BOOLEAN NOT NULL,
                generated_at_utc TIMESTAMPTZ NOT NULL,
                config_hash VARCHAR NOT NULL,
                git_commit VARCHAR NOT NULL,
                data_snapshot_id VARCHAR NOT NULL,
                universe_version VARCHAR NOT NULL,
                theme_version VARCHAR NOT NULL,
                PRIMARY KEY (gate_id)
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


def build_hindsight_events_from_cases(cases: list[HindsightCase]) -> list[HindsightEvent]:
    events: list[HindsightEvent] = []
    for case in cases:
        event_id = str(uuid.uuid5(uuid.NAMESPACE_URL, "|".join(["hindsight_event", case.symbol, case.label])))
        events.append(
            HindsightEvent(
                event_id=event_id,
                symbol=case.symbol,
                label=case.label,
                event_type=_event_type_from_theme(case.theme),
                event_date=case.start_date,
                published_at_utc=None,
                market_session="date_only_ambiguous",
                source_url=case.source_url,
                source_quality="case_metadata_needs_source_review",
                evidence_type="theme_or_catalyst_seed",
                evidence_summary=case.anchor_event,
                fundamental_evidence_available_at=None,
                first_tradable_date=None,
                first_tradable_bar_policy="unresolved_until_timestamp_reviewed",
                technical_replay_as_of=case.start_date,
                timing_status="DATA_GAP",
                requires_review=True,
            )
        )
    return events


def seed_hindsight_events(config: SectorScoutConfig, path: Path = DEFAULT_HINDSIGHT_CASES_PATH) -> int:
    ensure_hindsight_tables(config)
    events = build_hindsight_events_from_cases(load_hindsight_cases(path))
    _persist_hindsight_events(config, events)
    return len(events)


def latest_hindsight_events(config: SectorScoutConfig, *, limit: int = 100) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_event_ledger
            ORDER BY symbol, event_date, event_type
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


def build_hindsight_replay_gates(
    config: SectorScoutConfig,
    *,
    path: Path = DEFAULT_HINDSIGHT_CASES_PATH,
    persist: bool = True,
    asof_date: date | None = None,
) -> list[HindsightReplayGate]:
    ensure_hindsight_tables(config)
    cases = load_hindsight_cases(path)
    case_by_symbol = {case.symbol: case for case in cases}
    events = latest_hindsight_events(config)
    if events.empty:
        seed_hindsight_events(config, path)
        events = latest_hindsight_events(config)
    gates: list[HindsightReplayGate] = []
    effective_asof = asof_date or date.today()
    for _, event_row in events.iterrows():
        symbol = str(event_row["symbol"]).upper()
        case = case_by_symbol.get(symbol)
        if case is None:
            continue
        event = _event_from_row(event_row)
        gates.extend(_event_timing_gates(event, case, asof_date=effective_asof))
        gates.extend(_pre_event_technical_gates(config, event, case, asof_date=effective_asof))
        gates.extend(_first_tradable_gates(config, event, case, asof_date=effective_asof))
    if persist:
        _persist_hindsight_replay_gates(config, gates)
    return gates


def latest_hindsight_replay_gates(config: SectorScoutConfig, *, limit: int = 500) -> pd.DataFrame:
    ensure_hindsight_tables(config)
    with connect_database(config.database.path) as connection:
        return connection.execute(
            """
            SELECT *
            FROM hindsight_replay_gates
            QUALIFY dense_rank() OVER (ORDER BY generated_at_utc DESC) = 1
            ORDER BY symbol, gate_group, gate_name
            LIMIT ?
            """,
            [limit],
        ).fetchdf()


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
        seed_hindsight_events(config, path)
        build_hindsight_replay_gates(config, path=path, persist=True)
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


def _event_type_from_theme(theme: str) -> str:
    lowered = theme.lower()
    if any(word in lowered for word in ["memory", "hbm", "storage", "nand", "optical", "semiconductor", "ai"]):
        return "theme_catalyst"
    return "other"


def _event_from_row(row: pd.Series) -> HindsightEvent:
    published = row.get("published_at_utc")
    fundamental_available = row.get("fundamental_evidence_available_at")
    return HindsightEvent(
        event_id=str(row["event_id"]),
        symbol=str(row["symbol"]).upper(),
        label=str(row["label"]),
        event_type=str(row["event_type"]),
        event_date=_coerce_date(row["event_date"]),
        published_at_utc=_coerce_datetime(published),
        market_session=str(row["market_session"]),
        source_url=str(row.get("source_url") or ""),
        source_quality=str(row.get("source_quality") or ""),
        evidence_type=str(row.get("evidence_type") or ""),
        evidence_summary=str(row.get("evidence_summary") or ""),
        fundamental_evidence_available_at=_coerce_datetime(fundamental_available),
        first_tradable_date=_coerce_optional_date(row.get("first_tradable_date")),
        first_tradable_bar_policy=str(row.get("first_tradable_bar_policy") or ""),
        technical_replay_as_of=_coerce_optional_date(row.get("technical_replay_as_of")),
        timing_status=str(row.get("timing_status") or "DATA_GAP"),
        requires_review=bool(row.get("requires_review")),
    )


def _coerce_date(value: object) -> date:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if hasattr(value, "date"):
        return value.date()
    return date.fromisoformat(str(value)[:10])


def _coerce_optional_date(value: object) -> date | None:
    if value is None or pd.isna(value):
        return None
    return _coerce_date(value)


def _coerce_datetime(value: object) -> datetime | None:
    if value is None or pd.isna(value):
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def resolve_first_tradable_date(event_date: date, market_session: str, trading_dates: list[date]) -> date | None:
    trading_dates = sorted({day for day in trading_dates if day >= event_date})
    if market_session == "date_only_ambiguous" or not trading_dates:
        return None
    if market_session in {"pre_market", "regular"} and trading_dates[0] == event_date:
        return event_date
    for trading_day in trading_dates:
        if trading_day > event_date:
            return trading_day
    return None


def _event_timing_gates(
    event: HindsightEvent,
    case: HindsightCase,
    *,
    asof_date: date,
) -> list[HindsightReplayGate]:
    gates = [
        _gate(
            event=event,
            case=case,
            gate_group="event_timing",
            gate_name="Event timestamp available",
            gate_status="PASS" if event.published_at_utc else "DATA_GAP",
            formula="published_at_utc must be known before event reaction can be interpreted",
            computed_value=event.published_at_utc.isoformat() if event.published_at_utc else "missing",
            threshold="non-null timestamp",
            data_used="hindsight_event_ledger.published_at_utc",
            required_rows=1,
            available_rows=1 if event.published_at_utc else 0,
            missing_detail="Published timestamp is not captured." if not event.published_at_utc else "",
            reason=(
                "Event timing is auditable."
                if event.published_at_utc
                else "Only a date-level seed is available; do not infer intraday availability."
            ),
            asof_date=asof_date,
            source=event.source_url,
            requires_review=not bool(event.published_at_utc),
        ),
        _gate(
            event=event,
            case=case,
            gate_group="event_timing",
            gate_name="First tradable date resolved",
            gate_status="PASS" if event.first_tradable_date else "DATA_GAP",
            formula="first_tradable_date must follow the event market session policy",
            computed_value=event.first_tradable_date.isoformat() if event.first_tradable_date else "unresolved",
            threshold="non-null first tradable date",
            data_used="hindsight_event_ledger.market_session + market calendar",
            required_rows=1,
            available_rows=1 if event.first_tradable_date else 0,
            missing_detail=(
                "Market session is date_only_ambiguous or the trading calendar has not resolved the event."
                if not event.first_tradable_date
                else ""
            ),
            reason=(
                "Reaction and first-tradable checks can be separated."
                if event.first_tradable_date
                else "Do not compute event reaction until the first tradable date is known."
            ),
            asof_date=asof_date,
            source=event.source_url,
            requires_review=not bool(event.first_tradable_date),
        ),
    ]
    return gates


def _pre_event_technical_gates(
    config: SectorScoutConfig,
    event: HindsightEvent,
    case: HindsightCase,
    *,
    asof_date: date,
) -> list[HindsightReplayGate]:
    prices = _price_rows_before_event(config, event.symbol, event.event_date)
    benchmark = _primary_benchmark(case)
    gates: list[HindsightReplayGate] = []
    gates.append(
        _gate(
            event=event,
            case=case,
            gate_group="pre_event",
            gate_name="Pre-event price coverage",
            gate_status="PASS" if len(prices) >= 60 else "DATA_GAP",
            formula="daily price rows before event_date >= 60",
            computed_value=f"{len(prices)} rows",
            threshold=">= 60 rows",
            data_used="daily_prices before event_date",
            required_rows=60,
            available_rows=len(prices),
            missing_detail="" if len(prices) >= 60 else "Not enough pre-event daily bars loaded.",
            reason=(
                "Enough pre-event bars exist for basic trend and liquidity context."
                if len(prices) >= 60
                else "Pre-event context is incomplete; avoid treating the case as if setup evidence was visible."
            ),
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    )
    gates.append(_stage2_gate(event, case, prices, asof_date=asof_date))
    gates.append(_benchmark_rs_gate(config, event, case, benchmark=benchmark, asof_date=asof_date))
    return gates


def _stage2_gate(
    event: HindsightEvent,
    case: HindsightCase,
    prices: pd.DataFrame,
    *,
    asof_date: date,
) -> HindsightReplayGate:
    required = 220
    if len(prices) < required:
        return _gate(
            event=event,
            case=case,
            gate_group="pre_event",
            gate_name="Stage 2 trend explain",
            gate_status="DATA_GAP",
            formula="close > SMA50 and close > SMA200 and SMA200[t] > SMA200[t-20]",
            computed_value=f"{len(prices)} rows available",
            threshold=f">= {required} pre-event rows",
            data_used="daily_prices.adj_close",
            required_rows=required,
            available_rows=len(prices),
            missing_detail="Need enough pre-event rows for SMA200 and slope.",
            reason="Trend proxy cannot be evaluated without sufficient pre-event history.",
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    close = prices["adj_close"].astype(float).reset_index(drop=True)
    sma50 = close.rolling(50).mean()
    sma200 = close.rolling(200).mean()
    latest_close = float(close.iloc[-1])
    latest_sma50 = float(sma50.iloc[-1])
    latest_sma200 = float(sma200.iloc[-1])
    prior_sma200 = float(sma200.iloc[-21])
    passed = latest_close > latest_sma50 and latest_close > latest_sma200 and latest_sma200 > prior_sma200
    return _gate(
        event=event,
        case=case,
        gate_group="pre_event",
        gate_name="Stage 2 trend explain",
        gate_status="PASS" if passed else "FAIL",
        formula="close > SMA50 and close > SMA200 and SMA200[t] > SMA200[t-20]",
        computed_value=(
            f"close={latest_close:.2f}; sma50={latest_sma50:.2f}; "
            f"sma200={latest_sma200:.2f}; sma200_20d_prior={prior_sma200:.2f}"
        ),
        threshold="all conditions true",
        data_used="daily_prices.adj_close",
        required_rows=required,
        available_rows=len(prices),
        missing_detail="",
        reason="Pre-event trend proxy met." if passed else "Pre-event trend proxy did not meet all conditions.",
        asof_date=asof_date,
        source="daily_prices",
        requires_review=False,
    )


def _benchmark_rs_gate(
    config: SectorScoutConfig,
    event: HindsightEvent,
    case: HindsightCase,
    *,
    benchmark: str,
    asof_date: date,
) -> HindsightReplayGate:
    symbol_prices = _price_rows_before_event(config, event.symbol, event.event_date).tail(63)
    benchmark_prices = _price_rows_before_event(config, benchmark, event.event_date).tail(63)
    required = 63
    if len(symbol_prices) < required or len(benchmark_prices) < required:
        return _gate(
            event=event,
            case=case,
            gate_group="pre_event",
            gate_name=f"Benchmark RS vs {benchmark}",
            gate_status="DATA_GAP",
            formula="63-session symbol return minus fixed primary benchmark return",
            computed_value=f"symbol_rows={len(symbol_prices)}; benchmark_rows={len(benchmark_prices)}",
            threshold=f"{required} rows for both symbol and {benchmark}",
            data_used=f"daily_prices for {event.symbol} and fixed benchmark {benchmark}",
            required_rows=required * 2,
            available_rows=len(symbol_prices) + len(benchmark_prices),
            missing_detail="Primary benchmark coverage is incomplete; no benchmark fallback is applied.",
            reason="Relative strength cannot be evaluated without the fixed primary benchmark.",
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    symbol_return = float(symbol_prices["adj_close"].iloc[-1] / symbol_prices["adj_close"].iloc[0] - 1)
    benchmark_return = float(benchmark_prices["adj_close"].iloc[-1] / benchmark_prices["adj_close"].iloc[0] - 1)
    spread = symbol_return - benchmark_return
    return _gate(
        event=event,
        case=case,
        gate_group="pre_event",
        gate_name=f"Benchmark RS vs {benchmark}",
        gate_status="PASS" if spread > 0 else "FAIL",
        formula="63-session symbol return minus fixed primary benchmark return",
        computed_value=f"symbol={symbol_return:.2%}; {benchmark}={benchmark_return:.2%}; spread={spread:.2%}",
        threshold="spread > 0",
        data_used=f"daily_prices for {event.symbol} and fixed benchmark {benchmark}",
        required_rows=required * 2,
        available_rows=len(symbol_prices) + len(benchmark_prices),
        missing_detail="",
        reason="Symbol led the fixed benchmark." if spread > 0 else "Symbol did not lead the fixed benchmark.",
        asof_date=asof_date,
        source="daily_prices",
        requires_review=False,
    )


def _first_tradable_gates(
    config: SectorScoutConfig,
    event: HindsightEvent,
    case: HindsightCase,
    *,
    asof_date: date,
) -> list[HindsightReplayGate]:
    if event.first_tradable_date is None:
        return [
            _gate(
                event=event,
                case=case,
                gate_group="first_tradable",
                gate_name="First-tradable reaction explain",
                gate_status="DATA_GAP",
                formula="first_tradable_date is required before reaction checks",
                computed_value="unresolved",
                threshold="first_tradable_date resolved",
                data_used="hindsight_event_ledger",
                required_rows=1,
                available_rows=0,
                missing_detail="First tradable date is unresolved.",
                reason="Reaction checks are blocked to avoid using an unavailable or ambiguous bar.",
                asof_date=asof_date,
                source=event.source_url,
                requires_review=True,
            )
        ]
    prices = _price_rows_on_or_after(config, event.symbol, event.first_tradable_date)
    first_row = prices.iloc[0].to_dict() if not prices.empty else None
    gates = [
        _gate(
            event=event,
            case=case,
            gate_group="first_tradable",
            gate_name="First-tradable price row",
            gate_status="PASS" if first_row else "DATA_GAP",
            formula="daily_prices row must exist for first_tradable_date",
            computed_value=str(first_row.get("price_date")) if first_row else "missing",
            threshold=event.first_tradable_date.isoformat(),
            data_used="daily_prices",
            required_rows=1,
            available_rows=1 if first_row else 0,
            missing_detail="" if first_row else "No price row exists for first_tradable_date.",
            reason="First tradable bar is available." if first_row else "First tradable bar is missing.",
            asof_date=asof_date,
            source="daily_prices",
            requires_review=False,
        )
    ]
    return gates


def _gate(
    *,
    event: HindsightEvent,
    case: HindsightCase,
    gate_group: str,
    gate_name: str,
    gate_status: str,
    formula: str,
    computed_value: str,
    threshold: str,
    data_used: str,
    required_rows: int,
    available_rows: int,
    missing_detail: str,
    reason: str,
    asof_date: date,
    source: str,
    requires_review: bool,
) -> HindsightReplayGate:
    gate_key = "|".join([event.event_id, gate_group, gate_name, asof_date.isoformat()])
    return HindsightReplayGate(
        gate_id=str(uuid.uuid5(uuid.NAMESPACE_URL, gate_key)),
        event_id=event.event_id,
        symbol=event.symbol,
        label=case.label,
        gate_group=gate_group,
        gate_name=gate_name,
        gate_status=gate_status,
        formula=formula,
        computed_value=computed_value,
        threshold=threshold,
        data_used=data_used,
        required_rows=required_rows,
        available_rows=available_rows,
        missing_detail=missing_detail,
        reason=reason,
        asof_date=asof_date,
        first_tradable_date=event.first_tradable_date,
        source=source,
        requires_review=requires_review,
    )


def _primary_benchmark(case: HindsightCase) -> str:
    lowered = case.theme.lower()
    if any(word in lowered for word in ["semiconductor", "memory", "hbm", "nand", "storage", "optical", "ai"]):
        return "SMH"
    return "QQQ"


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


def _price_rows_before_event(config: SectorScoutConfig, symbol: str, event_date: date) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        try:
            return connection.execute(
                """
                SELECT price_date, adj_open, adj_high, adj_low, adj_close, adj_volume, provider
                FROM daily_prices
                WHERE symbol = ?
                  AND price_date < ?
                QUALIFY row_number() OVER (PARTITION BY price_date ORDER BY provider) = 1
                ORDER BY price_date
                """,
                [symbol.upper(), event_date],
            ).fetchdf()
        except Exception:
            return pd.DataFrame()


def _price_rows_on_or_after(config: SectorScoutConfig, symbol: str, start_date: date) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        try:
            return connection.execute(
                """
                SELECT price_date, adj_open, adj_high, adj_low, adj_close, adj_volume, provider
                FROM daily_prices
                WHERE symbol = ?
                  AND price_date >= ?
                QUALIFY row_number() OVER (PARTITION BY price_date ORDER BY provider) = 1
                ORDER BY price_date
                """,
                [symbol.upper(), start_date],
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


def _persist_hindsight_events(config: SectorScoutConfig, events: list[HindsightEvent]) -> None:
    if not events:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for event in events:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_event_ledger (
                    event_id, symbol, label, event_type, event_date, published_at_utc,
                    market_session, source_url, source_quality, evidence_type,
                    evidence_summary, fundamental_evidence_available_at,
                    first_tradable_date, first_tradable_bar_policy,
                    technical_replay_as_of, timing_status, requires_review,
                    generated_at_utc, config_hash, git_commit, data_snapshot_id,
                    universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    event.event_id,
                    event.symbol,
                    event.label,
                    event.event_type,
                    event.event_date,
                    event.published_at_utc,
                    event.market_session,
                    event.source_url,
                    event.source_quality,
                    event.evidence_type,
                    event.evidence_summary,
                    event.fundamental_evidence_available_at,
                    event.first_tradable_date,
                    event.first_tradable_bar_policy,
                    event.technical_replay_as_of,
                    event.timing_status,
                    event.requires_review,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


def _persist_hindsight_replay_gates(config: SectorScoutConfig, gates: list[HindsightReplayGate]) -> None:
    if not gates:
        return
    now = datetime.now(timezone.utc)
    git_commit = get_git_commit()
    cfg_hash = config_hash(config)
    with connect_database(config.database.path) as connection:
        for gate in gates:
            connection.execute(
                """
                INSERT OR REPLACE INTO hindsight_replay_gates (
                    gate_id, event_id, symbol, label, gate_group, gate_name,
                    gate_status, formula, computed_value, threshold, data_used,
                    required_rows, available_rows, missing_detail, reason,
                    asof_date, first_tradable_date, source, requires_review,
                    generated_at_utc, config_hash, git_commit, data_snapshot_id,
                    universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    gate.gate_id,
                    gate.event_id,
                    gate.symbol,
                    gate.label,
                    gate.gate_group,
                    gate.gate_name,
                    gate.gate_status,
                    gate.formula,
                    gate.computed_value,
                    gate.threshold,
                    gate.data_used,
                    gate.required_rows,
                    gate.available_rows,
                    gate.missing_detail,
                    gate.reason,
                    gate.asof_date,
                    gate.first_tradable_date,
                    gate.source,
                    gate.requires_review,
                    now,
                    cfg_hash,
                    git_commit,
                    config.reproducibility.data_snapshot_id,
                    config.reproducibility.universe_version,
                    config.reproducibility.theme_version,
                ],
            )


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
