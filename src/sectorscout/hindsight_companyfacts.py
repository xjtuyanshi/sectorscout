from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from sectorscout.config import SectorScoutConfig
from sectorscout.hindsight_sec_metadata import build_hindsight_sec_filing_metadata


SEC_COMPANYFACTS_URL_TEMPLATE = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
DEFAULT_COMPANYFACT_CONCEPTS = (
    ("us-gaap", "Revenues"),
    ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ("us-gaap", "SalesRevenueNet"),
)
DEFAULT_COMPANYFACT_FORMS = {"10-K", "10-Q", "8-K", "20-F", "40-F", "6-K"}


@dataclass(frozen=True)
class HindsightCompanyFactCandidate:
    symbol: str
    cik: int
    taxonomy: str
    fact_name: str
    label: str
    unit: str
    value: str
    period_end_date: str
    filed_at: str
    form: str
    fiscal_year: str
    fiscal_period: str
    frame: str
    accession_number: str
    companyfacts_url: str
    source_filing_url: str
    anchor_published_at_utc: str
    extraction_status: str
    pit_status: str
    review_note: str
    checked_at_utc: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class HindsightCompanyFactStatus:
    symbol: str
    cik: int
    companyfacts_url: str
    fetch_status: str
    candidates: int
    checked_at_utc: str
    error: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def sec_companyfacts_url(cik: int) -> str:
    return SEC_COMPANYFACTS_URL_TEMPLATE.format(cik=cik)


def build_hindsight_companyfacts(
    config: SectorScoutConfig,
    *,
    fetch_remote: bool = False,
    timeout: int = 20,
    concepts: tuple[tuple[str, str], ...] = DEFAULT_COMPANYFACT_CONCEPTS,
) -> tuple[list[HindsightCompanyFactCandidate], list[HindsightCompanyFactStatus]]:
    """Build SEC companyfacts candidates without writing into live fundamentals."""

    sec_rows = build_hindsight_sec_filing_metadata(config, fetch_remote=False)
    sec_rows = [row for row in sec_rows if row.cik is not None]
    checked_at = datetime.now(timezone.utc).isoformat()
    if not fetch_remote:
        statuses = [
            HindsightCompanyFactStatus(
                symbol=row.symbol,
                cik=int(row.cik or 0),
                companyfacts_url=sec_companyfacts_url(int(row.cik or 0)),
                fetch_status="REMOTE_NOT_REQUESTED",
                candidates=0,
                checked_at_utc=checked_at,
            )
            for row in sec_rows
        ]
        return [], statuses

    candidates: list[HindsightCompanyFactCandidate] = []
    statuses: list[HindsightCompanyFactStatus] = []
    seen_ciks: set[int] = set()
    for row in sec_rows:
        cik = int(row.cik or 0)
        if not cik or cik in seen_ciks:
            continue
        seen_ciks.add(cik)
        try:
            payload = _fetch_sec_companyfacts_json(cik, timeout=timeout)
        except (HTTPError, TimeoutError, URLError, ValueError, json.JSONDecodeError) as exc:
            statuses.append(
                HindsightCompanyFactStatus(
                    symbol=row.symbol,
                    cik=cik,
                    companyfacts_url=sec_companyfacts_url(cik),
                    fetch_status="REMOTE_ERROR",
                    candidates=0,
                    checked_at_utc=datetime.now(timezone.utc).isoformat(),
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
            continue
        extracted = extract_companyfacts_candidates(
            symbol=row.symbol,
            cik=cik,
            payload=payload,
            anchor_published_at_utc=row.seed_published_at_utc,
            concepts=concepts,
        )
        candidates.extend(extracted)
        statuses.append(
            HindsightCompanyFactStatus(
                symbol=row.symbol,
                cik=cik,
                companyfacts_url=sec_companyfacts_url(cik),
                fetch_status="FETCHED",
                candidates=len(extracted),
                checked_at_utc=datetime.now(timezone.utc).isoformat(),
            )
        )
    return candidates, statuses


def companyfacts_summary(
    candidates: list[HindsightCompanyFactCandidate],
    statuses: list[HindsightCompanyFactStatus],
) -> dict[str, object]:
    return {
        "eligible_ciks": len(statuses),
        "fetched_ciks": sum(1 for status in statuses if status.fetch_status == "FETCHED"),
        "candidate_facts": len(candidates),
        "remote_not_requested": sum(1 for status in statuses if status.fetch_status == "REMOTE_NOT_REQUESTED"),
        "remote_errors": sum(1 for status in statuses if status.fetch_status == "REMOTE_ERROR"),
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def extract_companyfacts_candidates(
    *,
    symbol: str,
    cik: int,
    payload: dict[str, Any],
    anchor_published_at_utc: str = "",
    concepts: tuple[tuple[str, str], ...] = DEFAULT_COMPANYFACT_CONCEPTS,
    max_rows_per_concept: int = 4,
) -> list[HindsightCompanyFactCandidate]:
    facts_root = payload.get("facts") or {}
    checked_at = datetime.now(timezone.utc).isoformat()
    anchor_date = _date_from_iso(anchor_published_at_utc)
    candidates: list[HindsightCompanyFactCandidate] = []
    for taxonomy, fact_name in concepts:
        fact = (facts_root.get(taxonomy) or {}).get(fact_name)
        if not isinstance(fact, dict):
            continue
        concept_rows: list[HindsightCompanyFactCandidate] = []
        units = fact.get("units") or {}
        for unit, unit_rows in units.items():
            if not isinstance(unit_rows, list):
                continue
            for unit_row in unit_rows:
                if not isinstance(unit_row, dict) or "val" not in unit_row:
                    continue
                if str(unit_row.get("form") or "").upper() not in DEFAULT_COMPANYFACT_FORMS:
                    continue
                filed_date = _date_from_iso(unit_row.get("filed"))
                if anchor_date is not None and filed_date is not None and filed_date > anchor_date + timedelta(days=1):
                    continue
                if anchor_date is not None and filed_date is not None and filed_date < anchor_date - timedelta(days=900):
                    continue
                concept_rows.append(
                    _candidate_from_companyfact(
                        symbol=symbol,
                        cik=cik,
                        taxonomy=taxonomy,
                        fact_name=fact_name,
                        fact=fact,
                        unit=str(unit),
                        unit_row=unit_row,
                        anchor_published_at_utc=anchor_published_at_utc,
                        checked_at=checked_at,
                    )
                )
        concept_rows.sort(key=_candidate_sort_key, reverse=True)
        candidates.extend(concept_rows[:max_rows_per_concept])
    return candidates


def _candidate_from_companyfact(
    *,
    symbol: str,
    cik: int,
    taxonomy: str,
    fact_name: str,
    fact: dict[str, Any],
    unit: str,
    unit_row: dict[str, Any],
    anchor_published_at_utc: str,
    checked_at: str,
) -> HindsightCompanyFactCandidate:
    accession = str(unit_row.get("accn") or "")
    return HindsightCompanyFactCandidate(
        symbol=symbol,
        cik=cik,
        taxonomy=taxonomy,
        fact_name=fact_name,
        label=str(fact.get("label") or fact_name),
        unit=unit,
        value=str(unit_row.get("val") or ""),
        period_end_date=str(unit_row.get("end") or ""),
        filed_at=str(unit_row.get("filed") or ""),
        form=str(unit_row.get("form") or ""),
        fiscal_year=str(unit_row.get("fy") or ""),
        fiscal_period=str(unit_row.get("fp") or ""),
        frame=str(unit_row.get("frame") or ""),
        accession_number=accession,
        companyfacts_url=sec_companyfacts_url(cik),
        source_filing_url=_source_filing_url(cik, accession),
        anchor_published_at_utc=anchor_published_at_utc,
        extraction_status="CANDIDATE_FACT",
        pit_status="PIT_CANDIDATE_REQUIRES_ACCEPTANCE_REVIEW" if unit_row.get("filed") else "REQUIRES_REVIEW",
        review_note=(
            "Generic SEC XBRL fact candidate. Review taxonomy, unit, and filing acceptance timing before using it "
            "as evidence for an industry mechanism."
        ),
        checked_at_utc=checked_at,
    )


def _fetch_sec_companyfacts_json(cik: int, *, timeout: int) -> dict[str, Any]:
    user_agent = os.environ.get("SECTORSCOUT_SOURCE_USER_AGENT", "SectorScout/0.1 companyfacts")
    request = Request(sec_companyfacts_url(cik), headers={"User-Agent": user_agent, "Accept": "application/json"})
    with urlopen(request, timeout=timeout) as response:  # nosec B310 - public SEC companyfacts API, no credentials.
        payload = response.read().decode("utf-8")
    return json.loads(payload)


def _source_filing_url(cik: int, accession_number: str) -> str:
    if not accession_number:
        return ""
    compact = accession_number.replace("-", "")
    return f"https://www.sec.gov/Archives/edgar/data/{cik}/{compact}/"


def _candidate_sort_key(candidate: HindsightCompanyFactCandidate) -> tuple[str, str, str]:
    return (candidate.filed_at, candidate.period_end_date, candidate.accession_number)


def _date_from_iso(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    except ValueError:
        try:
            return date.fromisoformat(text[:10])
        except ValueError:
            return None
