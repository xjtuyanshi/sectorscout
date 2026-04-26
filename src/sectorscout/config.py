from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ProjectConfig(BaseModel):
    name: str = "SectorScout"
    schema_version: int = 0


class MarketConfig(BaseModel):
    calendar: str = "XNYS"
    timezone: str = "America/New_York"


class DatabaseConfig(BaseModel):
    path: Path = Path("data/sectorscout.duckdb")


class DataConfig(BaseModel):
    fixture_dir: Path = Path("data/fixtures")


class DataQualityConfig(BaseModel):
    stale_price_days: int = 1


class IndicatorConfig(BaseModel):
    rs_lookback_days: int = 63
    stage2_high_window_days: int = 252
    stage2_min_pct_of_high: float = 0.75


class MarketRegimeConfig(BaseModel):
    risk_on_min_pct_above_50dma: float = 0.50
    risk_on_min_pct_stage2: float = 0.30
    neutral_min_pct_above_200dma: float = 0.40


class ThemeWeightsConfig(BaseModel):
    technical_relative_strength: float = 0.35
    breadth: float = 0.25
    fundamental_acceleration: float = 0.25
    catalyst: float = 0.10
    risk_valuation_penalty: float = 0.05


class StockWeightsConfig(BaseModel):
    theme_score: float = 0.25
    rs_percentile: float = 0.20
    fundamental_acceleration: float = 0.15
    setup_quality: float = 0.15
    volume_accumulation: float = 0.10
    risk_reward: float = 0.10
    liquidity: float = 0.05


class ScoringConfig(BaseModel):
    discover_theme_threshold: float = 65
    watch_rs_percentile: float = 80
    theme_weights: ThemeWeightsConfig = Field(default_factory=ThemeWeightsConfig)
    stock_weights: StockWeightsConfig = Field(default_factory=StockWeightsConfig)


class SetupConfig(BaseModel):
    min_reward_risk: float = 2.0
    vcp_min_window: int = 7
    vcp_max_window: int = 40
    vcp_max_depth: float = 0.25
    vcp_min_rs_percentile: float = 80
    vcp_min_volume_breakout_ratio: float = 1.30
    pullback_min_depth: float = 0.03
    pullback_max_depth: float = 0.12
    pullback_ma_tolerance: float = 0.02
    earnings_gap_min_gap_pct: float = 0.08
    earnings_gap_min_volume_ratio: float = 2.0
    earnings_gap_min_hold_days: int = 5
    earnings_gap_max_hold_days: int = 15


class ProviderConfig(BaseModel):
    prices_primary: str = "FMP"
    prices_fallback: str = "yfinance"
    fundamentals_primary: str = "SEC_EDGAR"
    fundamentals_secondary: str = "FMP"


class MissingDataPolicy(BaseModel):
    technical: str = "exclude"
    stale_price_volume: str = "exclude"
    fundamental: str = "neutral_with_coverage_penalty"
    catalyst: str = "neutral_zero_contribution"


class ReproducibilityConfig(BaseModel):
    data_snapshot_id: str = "phase0-fixtures"
    universe_version: str = "phase0"
    theme_version: str = "phase0"


class PortfolioConfig(BaseModel):
    max_positions: int = 10
    max_single_position_weight: float = 0.10
    max_theme_exposure: float = 0.30
    max_sector_exposure: float = 0.40
    max_total_open_risk: float = 0.05
    max_new_positions_per_day: int = 3


class ExecutionConfig(BaseModel):
    max_entry_extension_pct: float = 0.05
    max_initial_stop_pct: float = 0.12
    require_next_open_above_trigger: bool = False
    max_entry_drop_below_trigger_pct: float = 0.02


class LifecycleConfig(BaseModel):
    time_stop_days: int = 20
    theme_failure_score_threshold: float = 50
    theme_failure_consecutive_days: int = 5


class ValidationConfig(BaseModel):
    default_start_year: int = 2016


class SectorScoutConfig(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    indicators: IndicatorConfig = Field(default_factory=IndicatorConfig)
    market_regime: MarketRegimeConfig = Field(default_factory=MarketRegimeConfig)
    scoring: ScoringConfig = Field(default_factory=ScoringConfig)
    setups: SetupConfig = Field(default_factory=SetupConfig)
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    missing_data_policy: MissingDataPolicy = Field(default_factory=MissingDataPolicy)
    reproducibility: ReproducibilityConfig = Field(default_factory=ReproducibilityConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    lifecycle: LifecycleConfig = Field(default_factory=LifecycleConfig)
    validation: ValidationConfig = Field(default_factory=ValidationConfig)


def load_config(path: str | Path = "config.yaml") -> SectorScoutConfig:
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle) or {}
    return SectorScoutConfig.model_validate(raw)


def _canonicalize(value: Any) -> Any:
    if isinstance(value, Path):
        return value.as_posix()
    if isinstance(value, dict):
        return {str(key): _canonicalize(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonicalize(item) for item in value]
    return value


def canonical_config_payload(config: SectorScoutConfig) -> str:
    raw = config.model_dump(mode="python")
    canonical = _canonicalize(raw)
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def config_hash(config: SectorScoutConfig) -> str:
    payload = canonical_config_payload(config).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()
