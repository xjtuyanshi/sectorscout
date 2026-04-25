from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
from statistics import median

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database
from sectorscout.indicators import compute_technical_indicators
from sectorscout.market_regime import MarketRegimeRow, compute_market_regime
from sectorscout.metadata import build_run_metadata
from sectorscout.pit import available_fundamental_facts, theme_members_asof


@dataclass(frozen=True)
class ThemeScoreRow:
    asof_date: str
    theme_id: str
    theme_score: float
    technical_relative_strength: float
    breadth: float
    fundamental_acceleration: float
    catalyst_score: float
    risk_valuation_penalty: float
    component_coverage_pct: float
    members_count: int
    technical_coverage_pct: float
    theme_fundamental_coverage_pct: float
    members_with_valid_fundamentals: int
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class StockScoreRow:
    asof_date: str
    symbol: str
    theme_id: str
    stock_opportunity_score: float
    theme_score: float
    rs_percentile: float
    fundamental_acceleration: float
    setup_quality: float
    volume_accumulation: float
    risk_reward: float
    liquidity: float
    component_coverage_pct: float
    state: str
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    universe_version: str
    theme_version: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ScoreRunResult:
    asof_date: str
    market_regime: dict
    theme_scores: list[dict]
    stock_scores: list[dict]

    def to_dict(self) -> dict:
        return asdict(self)


def _clamp_score(value: float) -> float:
    return max(0.0, min(100.0, value))


def _load_indicator_map(config: SectorScoutConfig, asof_date: date) -> dict[str, dict]:
    with connect_database(config.database.path) as connection:
        rows = connection.execute(
            """
            SELECT
                symbol, rs_percentile, trend_stage, volume_10d_avg,
                volume_50d_avg
            FROM technical_indicators
            WHERE asof_date = ?
            """,
            [asof_date],
        ).fetchall()
    return {
        symbol: {
            "rs_percentile": rs_percentile,
            "trend_stage": trend_stage,
            "volume_10d_avg": volume_10d_avg,
            "volume_50d_avg": volume_50d_avg,
        }
        for symbol, rs_percentile, trend_stage, volume_10d_avg, volume_50d_avg in rows
    }


def _fundamental_scores(config: SectorScoutConfig, asof_date: date) -> dict[str, tuple[float, bool]]:
    facts = available_fundamental_facts(config, asof_date)
    by_symbol_metric: dict[tuple[str, str], list[dict]] = {}
    for fact in facts:
        key = (fact["symbol"], fact["metric_name"])
        by_symbol_metric.setdefault(key, []).append(fact)

    scores: dict[str, tuple[float, bool]] = {}
    for (symbol, _metric_name), metric_facts in by_symbol_metric.items():
        ordered = sorted(metric_facts, key=lambda row: row["available_at"])
        latest = ordered[-1]["metric_value"]
        if len(ordered) < 2:
            scores[symbol] = (50.0, True)
            continue
        previous = ordered[-2]["metric_value"]
        if previous == 0:
            scores[symbol] = (50.0, True)
            continue
        growth = (latest - previous) / abs(previous)
        scores[symbol] = (_clamp_score(50.0 + growth * 100.0), True)
    return scores


def _volume_accumulation(indicator: dict | None) -> float:
    if not indicator:
        return 0.0
    volume_10 = indicator.get("volume_10d_avg")
    volume_50 = indicator.get("volume_50d_avg")
    if not volume_10 or not volume_50:
        return 0.0
    return _clamp_score(50.0 + ((volume_10 / volume_50) - 1.0) * 100.0)


def _liquidity(indicator: dict | None) -> float:
    if not indicator or not indicator.get("volume_50d_avg"):
        return 0.0
    return _clamp_score(float(indicator["volume_50d_avg"]) / 1_000_000 * 100.0)


def compute_theme_scores(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    persist: bool = True,
) -> list[ThemeScoreRow]:
    indicators = _load_indicator_map(config, asof_date)
    if not indicators:
        compute_technical_indicators(config, asof_date, persist=True)
        indicators = _load_indicator_map(config, asof_date)

    members = theme_members_asof(config, asof_date)
    fundamentals = _fundamental_scores(config, asof_date)
    grouped: dict[str, list[str]] = {}
    for member in members:
        grouped.setdefault(member["theme_id"], []).append(member["symbol"])

    metadata = build_run_metadata(config, "theme-score", asof_date=asof_date)
    weights = config.scoring.theme_weights
    rows: list[ThemeScoreRow] = []
    for theme_id, symbols in sorted(grouped.items()):
        member_count = len(symbols)
        technical_values = [
            float(indicators[symbol]["rs_percentile"])
            for symbol in symbols
            if symbol in indicators and indicators[symbol]["rs_percentile"] is not None
        ]
        technical_coverage = len(technical_values) / member_count if member_count else 0.0
        technical_rs = median(technical_values) if technical_values else 0.0
        breadth = (
            sum(indicators.get(symbol, {}).get("trend_stage") == "Stage 2" for symbol in symbols)
            / member_count
            * 100.0
            if member_count
            else 0.0
        )

        fundamental_values = [
            fundamentals[symbol][0]
            for symbol in symbols
            if symbol in fundamentals and fundamentals[symbol][1]
        ]
        fundamental_coverage = len(fundamental_values) / member_count if member_count else 0.0
        fundamental_score = median(fundamental_values) if fundamental_values else 50.0

        catalyst_score = 0.0
        risk_penalty = 0.0
        theme_score = (
            technical_rs * weights.technical_relative_strength
            + breadth * weights.breadth
            + fundamental_score * weights.fundamental_acceleration
            + catalyst_score * weights.catalyst
            + (100.0 - risk_penalty) * weights.risk_valuation_penalty
        )
        component_coverage = (
            technical_coverage * weights.technical_relative_strength
            + technical_coverage * weights.breadth
            + fundamental_coverage * weights.fundamental_acceleration
            + 0.0 * weights.catalyst
            + 1.0 * weights.risk_valuation_penalty
        )

        rows.append(
            ThemeScoreRow(
                asof_date=asof_date.isoformat(),
                theme_id=theme_id,
                theme_score=_clamp_score(theme_score),
                technical_relative_strength=_clamp_score(technical_rs),
                breadth=_clamp_score(breadth),
                fundamental_acceleration=_clamp_score(fundamental_score),
                catalyst_score=catalyst_score,
                risk_valuation_penalty=risk_penalty,
                component_coverage_pct=component_coverage * 100.0,
                members_count=member_count,
                technical_coverage_pct=technical_coverage * 100.0,
                theme_fundamental_coverage_pct=fundamental_coverage * 100.0,
                members_with_valid_fundamentals=len(fundamental_values),
                signal_generated_at=metadata.signal_generated_at,
                config_hash=metadata.config_hash,
                git_commit=metadata.git_commit,
                data_snapshot_id=metadata.data_snapshot_id,
                universe_version=metadata.universe_version,
                theme_version=metadata.theme_version,
            )
        )

    if persist:
        persist_theme_scores(config, rows)
    return rows


def compute_stock_scores(
    config: SectorScoutConfig,
    asof_date: date,
    regime: MarketRegimeRow,
    theme_scores: list[ThemeScoreRow],
    *,
    persist: bool = True,
) -> list[StockScoreRow]:
    indicators = _load_indicator_map(config, asof_date)
    fundamentals = _fundamental_scores(config, asof_date)
    members = theme_members_asof(config, asof_date)
    themes_by_id = {row.theme_id: row for row in theme_scores}
    weights = config.scoring.stock_weights
    metadata = build_run_metadata(config, "stock-score", asof_date=asof_date)
    rows: list[StockScoreRow] = []

    for member in members:
        symbol = member["symbol"]
        theme_id = member["theme_id"]
        theme = themes_by_id.get(theme_id)
        indicator = indicators.get(symbol)
        if theme is None or indicator is None or indicator["rs_percentile"] is None:
            continue

        rs_percentile = float(indicator["rs_percentile"])
        fundamental_score, has_fundamental = fundamentals.get(symbol, (50.0, False))
        setup_quality = 0.0
        risk_reward = 0.0
        volume_accumulation = _volume_accumulation(indicator)
        liquidity = _liquidity(indicator)
        score = (
            theme.theme_score * weights.theme_score
            + rs_percentile * weights.rs_percentile
            + fundamental_score * weights.fundamental_acceleration
            + setup_quality * weights.setup_quality
            + volume_accumulation * weights.volume_accumulation
            + risk_reward * weights.risk_reward
            + liquidity * weights.liquidity
        )
        component_coverage = (
            weights.theme_score
            + weights.rs_percentile
            + (weights.fundamental_acceleration if has_fundamental else 0.0)
            + 0.0 * weights.setup_quality
            + (weights.volume_accumulation if volume_accumulation > 0 else 0.0)
            + 0.0 * weights.risk_reward
            + (weights.liquidity if liquidity > 0 else 0.0)
        )

        if regime.risk_state == "RISK_OFF":
            state = "RISK_OFF"
        elif (
            theme.theme_score >= config.scoring.discover_theme_threshold
            and rs_percentile >= config.scoring.watch_rs_percentile
        ):
            state = "WATCH"
        else:
            state = "DISCOVER"

        rows.append(
            StockScoreRow(
                asof_date=asof_date.isoformat(),
                symbol=symbol,
                theme_id=theme_id,
                stock_opportunity_score=_clamp_score(score),
                theme_score=theme.theme_score,
                rs_percentile=rs_percentile,
                fundamental_acceleration=fundamental_score,
                setup_quality=setup_quality,
                volume_accumulation=volume_accumulation,
                risk_reward=risk_reward,
                liquidity=liquidity,
                component_coverage_pct=component_coverage * 100.0,
                state=state,
                signal_generated_at=metadata.signal_generated_at,
                config_hash=metadata.config_hash,
                git_commit=metadata.git_commit,
                data_snapshot_id=metadata.data_snapshot_id,
                universe_version=metadata.universe_version,
                theme_version=metadata.theme_version,
            )
        )

    if persist:
        persist_stock_scores(config, rows)
        persist_watchlist(config, rows)
    return rows


def run_scoring(config: SectorScoutConfig, asof_date: date) -> ScoreRunResult:
    compute_technical_indicators(config, asof_date, persist=True)
    regime = compute_market_regime(config, asof_date, persist=True)
    theme_scores = compute_theme_scores(config, asof_date, persist=True)
    stock_scores = compute_stock_scores(config, asof_date, regime, theme_scores, persist=True)
    return ScoreRunResult(
        asof_date=asof_date.isoformat(),
        market_regime=regime.to_dict(),
        theme_scores=[row.to_dict() for row in theme_scores],
        stock_scores=[row.to_dict() for row in stock_scores],
    )


def persist_theme_scores(config: SectorScoutConfig, rows: list[ThemeScoreRow]) -> None:
    if not rows:
        return
    asof_date = date.fromisoformat(rows[0].asof_date)
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM theme_scores WHERE asof_date = ?", [asof_date])
        for row in rows:
            connection.execute(
                """
                INSERT INTO theme_scores (
                    asof_date, theme_id, theme_score, technical_relative_strength,
                    breadth, fundamental_acceleration, catalyst_score,
                    risk_valuation_penalty, component_coverage_pct, members_count,
                    technical_coverage_pct, theme_fundamental_coverage_pct,
                    members_with_valid_fundamentals, signal_generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    date.fromisoformat(row.asof_date),
                    row.theme_id,
                    row.theme_score,
                    row.technical_relative_strength,
                    row.breadth,
                    row.fundamental_acceleration,
                    row.catalyst_score,
                    row.risk_valuation_penalty,
                    row.component_coverage_pct,
                    row.members_count,
                    row.technical_coverage_pct,
                    row.theme_fundamental_coverage_pct,
                    row.members_with_valid_fundamentals,
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )


def persist_stock_scores(config: SectorScoutConfig, rows: list[StockScoreRow]) -> None:
    if not rows:
        return
    asof_date = date.fromisoformat(rows[0].asof_date)
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM stock_scores WHERE asof_date = ?", [asof_date])
        for row in rows:
            connection.execute(
                """
                INSERT INTO stock_scores (
                    asof_date, symbol, theme_id, stock_opportunity_score,
                    theme_score, rs_percentile, fundamental_acceleration,
                    setup_quality, volume_accumulation, risk_reward, liquidity,
                    component_coverage_pct, state, signal_generated_at_utc,
                    config_hash, git_commit, data_snapshot_id, universe_version,
                    theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    date.fromisoformat(row.asof_date),
                    row.symbol,
                    row.theme_id,
                    row.stock_opportunity_score,
                    row.theme_score,
                    row.rs_percentile,
                    row.fundamental_acceleration,
                    row.setup_quality,
                    row.volume_accumulation,
                    row.risk_reward,
                    row.liquidity,
                    row.component_coverage_pct,
                    row.state,
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )


def persist_watchlist(config: SectorScoutConfig, rows: list[StockScoreRow]) -> None:
    if not rows:
        return
    asof_date = date.fromisoformat(rows[0].asof_date)
    with connect_database(config.database.path) as connection:
        connection.execute("DELETE FROM watchlist WHERE asof_date = ?", [asof_date])
        for row in rows:
            if row.state == "WATCH":
                reason = "Strong theme and RS; setup detection is Phase 4."
            elif row.state == "RISK_OFF":
                reason = "Market regime blocks new long entries."
            else:
                reason = "Theme/member visible as of date; no setup trigger."
            connection.execute(
                """
                INSERT INTO watchlist (
                    asof_date, symbol, theme_id, state, reason,
                    signal_generated_at_utc, config_hash, git_commit,
                    data_snapshot_id, universe_version, theme_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    date.fromisoformat(row.asof_date),
                    row.symbol,
                    row.theme_id,
                    row.state,
                    reason,
                    row.signal_generated_at,
                    row.config_hash,
                    row.git_commit,
                    row.data_snapshot_id,
                    row.universe_version,
                    row.theme_version,
                ],
            )
