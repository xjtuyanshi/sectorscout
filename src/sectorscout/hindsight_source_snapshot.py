from __future__ import annotations

import hashlib
import html
import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight_source_audit import HindsightSourceAuditItem, build_hindsight_source_audit


DEFAULT_SOURCE_SNAPSHOT_DIR = Path("data/hindsight/sources")
DEFAULT_MAX_BYTES = 750_000


@dataclass(frozen=True)
class HindsightSourceSnapshot:
    source_url: str
    symbols: list[str]
    source_type: str
    pit_status: str
    fetch_status: str
    http_status: int | None
    content_type: str
    title: str
    excerpt: str
    content_hash: str
    local_path: str | None
    fetched_at_utc: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def fetch_hindsight_source_snapshots(
    config: SectorScoutConfig,
    *,
    output_dir: Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
    include_review_required: bool = True,
    max_bytes: int = DEFAULT_MAX_BYTES,
    timeout: int = 15,
) -> list[HindsightSourceSnapshot]:
    output_dir.mkdir(parents=True, exist_ok=True)
    audit_items = build_hindsight_source_audit(config)
    snapshots: list[HindsightSourceSnapshot] = []
    for item in audit_items:
        if not include_review_required and item.pit_status in {"DATA_GAP", "REQUIRES_REVIEW"}:
            continue
        snapshots.append(_snapshot_source(item, output_dir=output_dir, max_bytes=max_bytes, timeout=timeout))
    _write_manifest(snapshots, output_dir)
    return snapshots


def _snapshot_source(
    item: HindsightSourceAuditItem,
    *,
    output_dir: Path,
    max_bytes: int,
    timeout: int,
) -> HindsightSourceSnapshot:
    fetched_at = datetime.now(timezone.utc).isoformat()
    try:
        response = _read_public_url(item.source_url, max_bytes=max_bytes, timeout=timeout)
    except HTTPError as exc:
        return _error_snapshot(item, fetched_at, "LOGIN_REQUIRED_OR_FORBIDDEN" if exc.code in {401, 403} else "HTTP_ERROR", exc.code, str(exc))
    except (TimeoutError, URLError, ValueError) as exc:
        return _error_snapshot(item, fetched_at, "FETCH_ERROR", None, f"{type(exc).__name__}: {exc}")

    raw_bytes = response["body"]
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    text = _decode_body(raw_bytes, str(response["content_type"]))
    title = _extract_title(text)
    normalized_text = _normalize_source_text(text)
    excerpt = _excerpt(normalized_text)
    local_path = output_dir / f"{_safe_symbol_prefix(item.symbols)}_{content_hash[:16]}.txt"
    if not local_path.exists():
        local_path.write_text(
            _snapshot_text(item, title=title, excerpt=excerpt, normalized_text=normalized_text),
            encoding="utf-8",
        )
    return HindsightSourceSnapshot(
        source_url=item.source_url,
        symbols=item.symbols,
        source_type=item.source_type,
        pit_status=item.pit_status,
        fetch_status="FETCHED",
        http_status=response["status"],
        content_type=str(response["content_type"]),
        title=title,
        excerpt=excerpt,
        content_hash=content_hash,
        local_path=str(local_path),
        fetched_at_utc=fetched_at,
    )


def _read_public_url(url: str, *, max_bytes: int, timeout: int) -> dict[str, Any]:
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("Only public http(s) URLs can be snapshotted.")
    headers = {
        "User-Agent": os.environ.get(
            "SECTORSCOUT_SOURCE_USER_AGENT",
            "SectorScout/0.1 research-source-snapshot",
        )
    }
    request = Request(url, headers=headers)
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - public source snapshot, no credentials.
        body = response.read(max_bytes + 1)
        if len(body) > max_bytes:
            body = body[:max_bytes]
        return {
            "status": int(response.status),
            "content_type": response.headers.get("Content-Type", ""),
            "body": body,
        }


def _error_snapshot(
    item: HindsightSourceAuditItem,
    fetched_at: str,
    fetch_status: str,
    http_status: int | None,
    error: str,
) -> HindsightSourceSnapshot:
    return HindsightSourceSnapshot(
        source_url=item.source_url,
        symbols=item.symbols,
        source_type=item.source_type,
        pit_status=item.pit_status,
        fetch_status=fetch_status,
        http_status=http_status,
        content_type="",
        title="",
        excerpt="",
        content_hash="",
        local_path=None,
        fetched_at_utc=fetched_at,
        error=error,
    )


def _decode_body(raw: bytes, content_type: str) -> str:
    charset_match = re.search(r"charset=([^;]+)", content_type, flags=re.I)
    encodings = [charset_match.group(1).strip()] if charset_match else []
    encodings.extend(["utf-8", "latin-1"])
    for encoding in encodings:
        try:
            return raw.decode(encoding, errors="replace")
        except LookupError:
            continue
    return raw.decode("utf-8", errors="replace")


def _extract_title(text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.I | re.S)
    if not match:
        return ""
    return _collapse_ws(html.unescape(match.group(1)))


def _normalize_source_text(text: str) -> str:
    without_scripts = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", text, flags=re.I | re.S)
    without_tags = re.sub(r"<[^>]+>", " ", without_scripts)
    return _collapse_ws(html.unescape(without_tags))


def _excerpt(text: str, *, limit: int = 700) -> str:
    return text[:limit].rstrip()


def _snapshot_text(item: HindsightSourceAuditItem, *, title: str, excerpt: str, normalized_text: str) -> str:
    return "\n".join(
        [
            f"source_url: {item.source_url}",
            f"symbols: {', '.join(item.symbols)}",
            f"source_type: {item.source_type}",
            f"pit_status: {item.pit_status}",
            f"title: {title or '-'}",
            "",
            "excerpt:",
            excerpt or "-",
            "",
            "normalized_text:",
            normalized_text,
            "",
        ]
    )


def _write_manifest(snapshots: list[HindsightSourceSnapshot], output_dir: Path) -> Path:
    manifest_path = output_dir / "source_snapshots_manifest.json"
    manifest_path.write_text(
        json.dumps([snapshot.to_dict() for snapshot in snapshots], indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return manifest_path


def _safe_symbol_prefix(symbols: list[str]) -> str:
    raw = "_".join(symbols) or "source"
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", raw).strip("._")
    return safe or "source"


def _collapse_ws(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
