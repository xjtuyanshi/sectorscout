from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from sectorscout.intel.models import ManualCapture


FRONTMATTER_DELIMITER = "---"


def parse_manual_markdown(raw: str) -> ManualCapture:
    metadata: dict[str, Any] = {}
    body = raw
    if raw.startswith(FRONTMATTER_DELIMITER):
        parts = raw.split(FRONTMATTER_DELIMITER, 2)
        if len(parts) == 3:
            metadata = yaml.safe_load(parts[1]) or {}
            body = parts[2].lstrip("\n")

    tags = metadata.get("tags") or []
    if isinstance(tags, str):
        tags = [tags]

    return ManualCapture(
        source_id=str(metadata.get("source_id") or "manual_capture"),
        author=metadata.get("author"),
        platform=str(metadata.get("platform") or "other"),
        guild_name=metadata.get("guild_name"),
        channel=metadata.get("channel"),
        published_at=metadata.get("published_at"),
        captured_at=metadata.get("captured_at"),
        url=metadata.get("url"),
        tags=[str(tag) for tag in tags],
        rights_scope=str(metadata.get("rights_scope") or "manual_private"),
        raw_text=body,
        metadata=metadata,
    )


def parse_manual_file(path: str | Path) -> ManualCapture:
    return parse_manual_markdown(Path(path).read_text(encoding="utf-8"))


def list_manual_files(root: str | Path = "data/intel/manual") -> list[Path]:
    manual_root = Path(root)
    if not manual_root.exists():
        return []
    return sorted(manual_root.glob("*.md"))
