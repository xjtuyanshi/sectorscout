from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sectorscout.config import SectorScoutConfig, config_hash


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def get_git_commit(cwd: str | Path | None = None) -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "NO_COMMIT"
    return result.stdout.strip()


def provider_versions() -> dict[str, str]:
    return {}


@dataclass(frozen=True)
class RunMetadata:
    command: str
    asof_date: str | None
    signal_generated_at: str
    config_hash: str
    git_commit: str
    data_snapshot_id: str
    provider_versions: dict[str, str]
    universe_version: str
    theme_version: str
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def build_run_metadata(
    config: SectorScoutConfig,
    command: str,
    *,
    asof_date: date | str | None = None,
    generated_at: datetime | None = None,
    cwd: str | Path | None = None,
) -> RunMetadata:
    timestamp = generated_at or utc_now()
    if timestamp.tzinfo is None:
        raise ValueError("generated_at must be timezone-aware")
    timestamp_utc = timestamp.astimezone(timezone.utc)
    asof_value = asof_date.isoformat() if isinstance(asof_date, date) else asof_date
    return RunMetadata(
        command=command,
        asof_date=asof_value,
        signal_generated_at=timestamp_utc.isoformat(),
        config_hash=config_hash(config),
        git_commit=get_git_commit(cwd),
        data_snapshot_id=config.reproducibility.data_snapshot_id,
        provider_versions=provider_versions(),
        universe_version=config.reproducibility.universe_version,
        theme_version=config.reproducibility.theme_version,
        created_at=timestamp_utc.isoformat(),
    )

