from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import latest_hindsight_evidence, latest_hindsight_events


@dataclass(frozen=True)
class HindsightSourceAuditItem:
    source_url: str
    symbols: list[str]
    source_roles: list[str]
    source_qualities: list[str]
    source_type: str
    pit_status: str
    remote_status: str
    remote_status_code: int | None
    check_method: str
    reviewer_note: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def build_hindsight_source_audit(
    config: SectorScoutConfig,
    *,
    check_remote: bool = False,
    timeout: int = 10,
) -> list[HindsightSourceAuditItem]:
    events = latest_hindsight_events(config)
    evidence = latest_hindsight_evidence(config)
    rows = _source_rows(events, evidence)
    items: list[HindsightSourceAuditItem] = []
    for url, url_rows in sorted(rows.items()):
        remote = _remote_source_status(url, timeout=timeout) if check_remote else _unchecked_remote_status()
        item = HindsightSourceAuditItem(
            source_url=url,
            symbols=sorted({str(row["symbol"]) for row in url_rows if row.get("symbol")}),
            source_roles=sorted({str(row["source_role"]) for row in url_rows if row.get("source_role")}),
            source_qualities=sorted({str(row["source_quality"]) for row in url_rows if row.get("source_quality")}),
            source_type=_source_type(url, url_rows),
            pit_status=_pit_status(url_rows),
            remote_status=str(remote["remote_status"]),
            remote_status_code=remote["remote_status_code"],
            check_method=str(remote["check_method"]),
            reviewer_note=_reviewer_note(url_rows),
        )
        items.append(item)
    return items


def build_hindsight_source_audit_rows(
    config: SectorScoutConfig,
    *,
    check_remote: bool = False,
    timeout: int = 10,
) -> list[dict[str, object]]:
    return [item.to_dict() for item in build_hindsight_source_audit(config, check_remote=check_remote, timeout=timeout)]


def _source_rows(events: pd.DataFrame, evidence: pd.DataFrame) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    if not events.empty:
        for _, row in events.iterrows():
            url = str(row.get("source_url") or "").strip()
            if not url:
                continue
            grouped.setdefault(url, []).append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "source_role": "event_timing",
                    "source_quality": str(row.get("source_quality") or ""),
                    "requires_review": bool(row.get("requires_review")),
                    "pit_status": str(row.get("timing_status") or ""),
                    "published_at_utc": row.get("published_at_utc"),
                    "available_at_utc": row.get("fundamental_evidence_available_at"),
                    "review_note": str(row.get("evidence_summary") or ""),
                }
            )
    if not evidence.empty:
        for _, row in evidence.iterrows():
            url = str(row.get("source_url") or "").strip()
            if not url:
                continue
            grouped.setdefault(url, []).append(
                {
                    "symbol": str(row.get("symbol") or ""),
                    "source_role": f"evidence:{row.get('evidence_lane')}",
                    "source_quality": str(row.get("source_quality") or ""),
                    "requires_review": bool(row.get("requires_review")),
                    "pit_status": str(row.get("evidence_status") or ""),
                    "usable_in_replay": bool(row.get("usable_in_replay")),
                    "published_at_utc": row.get("published_at_utc"),
                    "available_at_utc": row.get("available_at_utc"),
                    "review_note": str(row.get("review_note") or ""),
                }
            )
    return grouped


def _source_type(url: str, rows: list[dict[str, Any]]) -> str:
    qualities = {str(row.get("source_quality") or "") for row in rows}
    lowered = url.lower()
    if "sec.gov" in lowered or any(quality.startswith("sec_") for quality in qualities):
        return "sec_filing"
    if any(quality == "official_company_release" for quality in qualities):
        if lowered.endswith(".pdf"):
            return "official_company_pdf"
        return "official_company_release"
    if any("needs_source_review" in quality for quality in qualities):
        return "needs_source_review"
    return "public_source"


def _pit_status(rows: list[dict[str, Any]]) -> str:
    statuses = {str(row.get("pit_status") or "") for row in rows}
    if any(bool(row.get("usable_in_replay")) for row in rows) or "TIMING_RESOLVED" in statuses or "PASS" in statuses:
        if any(bool(row.get("requires_review")) for row in rows):
            return "PIT_USABLE_WITH_REVIEW_ITEMS"
        return "PIT_USABLE"
    if "REQUIRES_REVIEW" in statuses:
        return "REQUIRES_REVIEW"
    if "DATA_GAP" in statuses:
        return "DATA_GAP"
    return "UNKNOWN"


def _reviewer_note(rows: list[dict[str, Any]]) -> str:
    notes = [str(row.get("review_note") or "").strip() for row in rows if str(row.get("review_note") or "").strip()]
    if not notes:
        return "No source note is available yet."
    if any("future-only" in note.lower() for note in notes):
        return "Contains future-only evidence; do not use it at the initial replay point."
    if any(bool(row.get("requires_review")) for row in rows):
        return "Review required before this source can support a replay hypothesis."
    return notes[0]


def _unchecked_remote_status() -> dict[str, Any]:
    return {"remote_status": "NOT_CHECKED", "remote_status_code": None, "check_method": "offline_metadata_only"}


def _remote_source_status(url: str, *, timeout: int) -> dict[str, Any]:
    if not url.lower().startswith(("https://", "http://")):
        return {"remote_status": "INVALID_URL", "remote_status_code": None, "check_method": "public_url_check"}
    try:
        request = Request(url, headers={"User-Agent": "SectorScout/0.1 research source audit"})
        with urlopen(request, timeout=timeout) as response:  # nosec B310 - public source availability check, no credentials.
            status = int(response.status)
        return {
            "remote_status": "REMOTE_OK" if 200 <= status < 400 else "REMOTE_WARNING",
            "remote_status_code": status,
            "check_method": "public_url_check",
        }
    except HTTPError as exc:
        status = int(exc.code)
        if status in {401, 403}:
            remote_status = "REMOTE_FORBIDDEN_OR_LOGIN_REQUIRED"
        elif status == 404:
            remote_status = "REMOTE_NOT_FOUND"
        else:
            remote_status = "REMOTE_HTTP_ERROR"
        return {"remote_status": remote_status, "remote_status_code": status, "check_method": "public_url_check"}
    except (TimeoutError, URLError) as exc:
        return {
            "remote_status": "REMOTE_UNAVAILABLE",
            "remote_status_code": None,
            "check_method": f"public_url_check:{type(exc).__name__}",
        }


def source_audit_summary(items: list[HindsightSourceAuditItem]) -> dict[str, object]:
    return {
        "sources": len(items),
        "pit_usable_sources": sum(1 for item in items if item.pit_status.startswith("PIT_USABLE")),
        "review_required_sources": sum(
            1 for item in items if item.pit_status in {"REQUIRES_REVIEW", "PIT_USABLE_WITH_REVIEW_ITEMS"}
        ),
        "data_gap_sources": sum(1 for item in items if item.pit_status == "DATA_GAP"),
        "remote_checked_sources": sum(1 for item in items if item.remote_status != "NOT_CHECKED"),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }
