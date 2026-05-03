from __future__ import annotations

from pathlib import Path

import yaml

from sectorscout.config import SectorScoutConfig
from sectorscout.intel.models import PublicSource
from sectorscout.intel.public_web import PublicWebResult, collect_public_url


DEFAULT_PUBLIC_SOURCES_PATH = Path("data/intel/public_sources.yaml")


def load_public_sources(path: str | Path = DEFAULT_PUBLIC_SOURCES_PATH) -> list[PublicSource]:
    source_path = Path(path)
    if not source_path.exists():
        return []
    payload = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    rows = payload.get("sources", [])
    sources: list[PublicSource] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        source_type = str(row.get("type") or row.get("source_type") or "public_web")
        if source_type != "public_web":
            continue
        source_id = str(row.get("id") or row.get("source_id") or "").strip()
        url = str(row.get("url") or "").strip()
        if not source_id or not url:
            continue
        sources.append(
            PublicSource(
                source_id=source_id,
                name=str(row.get("name") or source_id),
                url=url,
                enabled=bool(row.get("enabled", True)),
                source_type=source_type,
                tags=[str(item) for item in row.get("tags", [])],
                rights_scope=str(row.get("rights_scope") or "public"),
                notes=row.get("notes"),
            )
        )
    return sources


def collect_public_sources(
    config: SectorScoutConfig,
    *,
    sources_path: str | Path = DEFAULT_PUBLIC_SOURCES_PATH,
    source_id: str = "all",
) -> list[PublicWebResult]:
    results: list[PublicWebResult] = []
    for source in load_public_sources(sources_path):
        if not source.enabled:
            continue
        if source_id != "all" and source.source_id != source_id:
            continue
        results.append(collect_public_url(config, source.url, source_id=source.source_id))
    return results
