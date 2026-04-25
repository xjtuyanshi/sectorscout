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


class ValidationConfig(BaseModel):
    default_start_year: int = 2016


class SectorScoutConfig(BaseModel):
    project: ProjectConfig = Field(default_factory=ProjectConfig)
    market: MarketConfig = Field(default_factory=MarketConfig)
    database: DatabaseConfig = Field(default_factory=DatabaseConfig)
    data: DataConfig = Field(default_factory=DataConfig)
    data_quality: DataQualityConfig = Field(default_factory=DataQualityConfig)
    providers: ProviderConfig = Field(default_factory=ProviderConfig)
    missing_data_policy: MissingDataPolicy = Field(default_factory=MissingDataPolicy)
    reproducibility: ReproducibilityConfig = Field(default_factory=ReproducibilityConfig)
    portfolio: PortfolioConfig = Field(default_factory=PortfolioConfig)
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
