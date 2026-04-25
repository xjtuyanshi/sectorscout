from datetime import datetime, timezone

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.metadata import build_run_metadata


def test_run_metadata_uses_utc_and_config_hash() -> None:
    config = SectorScoutConfig()
    generated_at = datetime(2026, 4, 25, 20, 30, tzinfo=timezone.utc)
    metadata = build_run_metadata(
        config,
        command="metadata",
        asof_date="2024-11-29",
        generated_at=generated_at,
    )
    assert metadata.signal_generated_at == "2026-04-25T20:30:00+00:00"
    assert metadata.created_at == "2026-04-25T20:30:00+00:00"
    assert metadata.config_hash == config_hash(config)
    assert metadata.data_snapshot_id == "phase0-fixtures"
    assert metadata.universe_version == "phase0"
    assert metadata.theme_version == "phase0"

