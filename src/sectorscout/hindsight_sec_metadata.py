from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight import latest_hindsight_events


SEC_SUBMISSIONS_URL_TEMPLATE = "https://data.sec.gov/submissions/CIK{cik:010d}.json"


@dataclass(frozen=True)
class SecArchiveReference:
    cik: int
    accession_number: str
    accession_number_nodashes: str
    document: str
    source_url: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HindsightSecFilingMetadata:
    symbol: str
    event_type: str
    source_url: str
    cik: int | None
    accession_number: str
    document: str
    metadata_status: str
    company_name: str
    form: str
    filing_date: str
    report_date: str
    acceptance_datetime: str
    primary_document: str
    seed_published_at_utc: str
    checked_at_utc: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def parse_sec_archive_url(url: str) -> SecArchiveReference | None:
    match = re.search(
        r"/Archives/edgar/data/(?P<cik>\d+)/(?P<accession>[0-9-]+)/(?P<document>[^/?#]+)",
        url,
        flags=re.I,
    )
    if not match:
        return None
    accession_raw = match.group("accession")
    accession = _normalize_accession(accession_raw)
    return SecArchiveReference(
        cik=int(match.group("cik")),
        accession_number=accession,
        accession_number_nodashes=accession_raw.replace("-", ""),
        document=match.group("document"),
        source_url=url,
    )


def sec_submissions_url(cik: int) -> str:
    return SEC_SUBMISSIONS_URL_TEMPLATE.format(cik=cik)


def _normalize_accession(value: str) -> str:
    compact = value.replace("-", "")
    if len(compact) == 18 and compact.isdigit():
        return f"{compact[:10]}-{compact[10:12]}-{compact[12:]}"
    return value


def build_hindsight_sec_filing_metadata(
    config: SectorScoutConfig,
    *,
    fetch_remote: bool = False,
    timeout: int = 15,
) -> list[HindsightSecFilingMetadata]:
    events = latest_hindsight_events(config)
    if events.empty:
        return []
    rows: list[HindsightSecFilingMetadata] = []
    for _, event in events.iterrows():
        url = str(event.get("source_url") or "")
        if "sec.gov" not in url.lower():
            continue
        rows.append(_metadata_for_event(event, fetch_remote=fetch_remote, timeout=timeout))
    return rows


def sec_filing_metadata_summary(items: list[HindsightSecFilingMetadata]) -> dict[str, object]:
    return {
        "sec_sources": len(items),
        "remote_matched_filings": sum(1 for item in items if item.metadata_status == "ACCESSION_MATCHED"),
        "parsed_only_filings": sum(1 for item in items if item.metadata_status == "PARSED_FROM_URL"),
        "metadata_errors": sum(1 for item in items if item.metadata_status.endswith("ERROR")),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def _metadata_for_event(
    event: pd.Series,
    *,
    fetch_remote: bool,
    timeout: int,
) -> HindsightSecFilingMetadata:
    checked_at = datetime.now(timezone.utc).isoformat()
    symbol = str(event.get("symbol") or "")
    event_type = str(event.get("event_type") or "")
    source_url = str(event.get("source_url") or "")
    seed_published_at = _iso_or_empty(event.get("published_at_utc"))
    reference = parse_sec_archive_url(source_url)
    if reference is None:
        return HindsightSecFilingMetadata(
            symbol=symbol,
            event_type=event_type,
            source_url=source_url,
            cik=None,
            accession_number="",
            document="",
            metadata_status="PARSE_ERROR",
            company_name="",
            form="",
            filing_date="",
            report_date="",
            acceptance_datetime="",
            primary_document="",
            seed_published_at_utc=seed_published_at,
            checked_at_utc=checked_at,
            error="SEC archive URL did not match expected /Archives/edgar/data/{cik}/{accession}/{document} shape.",
        )
    if not fetch_remote:
        return HindsightSecFilingMetadata(
            symbol=symbol,
            event_type=event_type,
            source_url=source_url,
            cik=reference.cik,
            accession_number=reference.accession_number,
            document=reference.document,
            metadata_status="PARSED_FROM_URL",
            company_name="",
            form="",
            filing_date="",
            report_date="",
            acceptance_datetime="",
            primary_document="",
            seed_published_at_utc=seed_published_at,
            checked_at_utc=checked_at,
        )
    try:
        submission = _fetch_sec_submission_json(reference.cik, timeout=timeout)
    except (HTTPError, TimeoutError, URLError, ValueError, json.JSONDecodeError) as exc:
        return HindsightSecFilingMetadata(
            symbol=symbol,
            event_type=event_type,
            source_url=source_url,
            cik=reference.cik,
            accession_number=reference.accession_number,
            document=reference.document,
            metadata_status="REMOTE_ERROR",
            company_name="",
            form="",
            filing_date="",
            report_date="",
            acceptance_datetime="",
            primary_document="",
            seed_published_at_utc=seed_published_at,
            checked_at_utc=checked_at,
            error=f"{type(exc).__name__}: {exc}",
        )
    match = _find_recent_filing(submission, reference.accession_number)
    if match is None:
        return HindsightSecFilingMetadata(
            symbol=symbol,
            event_type=event_type,
            source_url=source_url,
            cik=reference.cik,
            accession_number=reference.accession_number,
            document=reference.document,
            metadata_status="ACCESSION_NOT_FOUND",
            company_name=str(submission.get("name") or ""),
            form="",
            filing_date="",
            report_date="",
            acceptance_datetime="",
            primary_document="",
            seed_published_at_utc=seed_published_at,
            checked_at_utc=checked_at,
        )
    return HindsightSecFilingMetadata(
        symbol=symbol,
        event_type=event_type,
        source_url=source_url,
        cik=reference.cik,
        accession_number=reference.accession_number,
        document=reference.document,
        metadata_status="ACCESSION_MATCHED",
        company_name=str(submission.get("name") or ""),
        form=str(match.get("form") or ""),
        filing_date=str(match.get("filingDate") or ""),
        report_date=str(match.get("reportDate") or ""),
        acceptance_datetime=str(match.get("acceptanceDateTime") or ""),
        primary_document=str(match.get("primaryDocument") or ""),
        seed_published_at_utc=seed_published_at,
        checked_at_utc=checked_at,
    )


def _fetch_sec_submission_json(cik: int, *, timeout: int) -> dict[str, Any]:
    user_agent = os.environ.get("SECTORSCOUT_SOURCE_USER_AGENT", "SectorScout/0.1 sec-metadata")
    request = Request(sec_submissions_url(cik), headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - public SEC submissions API, no credentials.
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _find_recent_filing(submission: dict[str, Any], accession_number: str) -> dict[str, Any] | None:
    recent = submission.get("filings", {}).get("recent", {})
    accessions = recent.get("accessionNumber") or []
    try:
        index = list(accessions).index(accession_number)
    except ValueError:
        return None
    return {
        "accessionNumber": _array_value(recent, "accessionNumber", index),
        "form": _array_value(recent, "form", index),
        "filingDate": _array_value(recent, "filingDate", index),
        "reportDate": _array_value(recent, "reportDate", index),
        "acceptanceDateTime": _array_value(recent, "acceptanceDateTime", index),
        "primaryDocument": _array_value(recent, "primaryDocument", index),
    }


def _array_value(mapping: dict[str, Any], key: str, index: int) -> Any:
    values = mapping.get(key) or []
    try:
        return values[index]
    except IndexError:
        return ""


def _iso_or_empty(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.isoformat()
    text = str(value)
    return "" if text in {"NaT", "None"} else text
