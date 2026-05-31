from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.hindsight import (
    latest_hindsight_evidence,
    latest_hindsight_events,
    latest_hindsight_hypotheses,
    latest_hindsight_hypothesis_case_results,
    latest_hindsight_replay_gates,
)
from sectorscout.hindsight_companyfacts import build_hindsight_companyfacts, companyfacts_summary
from sectorscout.hindsight_industry_profile import build_hindsight_industry_profiles, industry_profile_summary
from sectorscout.hindsight_pattern_candidates import (
    build_hindsight_pattern_candidates,
    pattern_candidates_summary,
)
from sectorscout.hindsight_pattern_matrix import (
    build_hindsight_pattern_diagnostics,
    build_hindsight_pattern_matrix,
    pattern_diagnostics_summary,
    pattern_matrix_summary,
)
from sectorscout.hindsight_sec_metadata import build_hindsight_sec_filing_metadata
from sectorscout.hindsight_source_audit import build_hindsight_source_audit
from sectorscout.hindsight_source_snapshot import DEFAULT_SOURCE_SNAPSHOT_DIR
from sectorscout.metadata import get_git_commit
from sectorscout.ui.hindsight_presenter import (
    build_case_pattern_map_rows,
    build_case_story_cards,
    build_hindsight_readout_summary_cards,
    build_methodology_guardrail_rows,
    build_pattern_insight_rows,
    build_pattern_story_cards,
)


def generate_hindsight_pattern_playbook(
    config: SectorScoutConfig,
    *,
    output_dir: Path = Path("data/hindsight/reports"),
    asof_date: date | None = None,
    source_snapshot_dir: Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_for = asof_date or date.today()
    path = output_dir / f"hindsight_pattern_playbook_{generated_for.isoformat()}.md"
    path.write_text(
        build_hindsight_pattern_playbook_markdown(
            config,
            asof_date=generated_for,
            source_snapshot_dir=source_snapshot_dir,
        ),
        encoding="utf-8",
    )
    return path


def build_hindsight_pattern_playbook_markdown(
    config: SectorScoutConfig,
    *,
    asof_date: date | None = None,
    source_snapshot_dir: Path = DEFAULT_SOURCE_SNAPSHOT_DIR,
) -> str:
    generated_for = asof_date or date.today()
    generated_at = datetime.now(timezone.utc).isoformat()
    events = latest_hindsight_events(config)
    evidence = latest_hindsight_evidence(config)
    gates = latest_hindsight_replay_gates(config)
    hypotheses = latest_hindsight_hypotheses(config)
    case_results = latest_hindsight_hypothesis_case_results(config)
    pattern_rows = build_pattern_insight_rows(hypotheses, case_results)
    case_rows = build_case_pattern_map_rows(events, evidence, gates, hypotheses, case_results)
    source_audit = build_hindsight_source_audit(config)
    industry_profiles = build_hindsight_industry_profiles(config)
    pattern_matrix = build_hindsight_pattern_matrix(config)
    pattern_diagnostics = build_hindsight_pattern_diagnostics(pattern_matrix)
    discovery_candidates = build_hindsight_pattern_candidates(config)
    sec_metadata = build_hindsight_sec_filing_metadata(config)
    companyfacts, companyfacts_status = build_hindsight_companyfacts(config)
    summary_cards = build_hindsight_readout_summary_cards(pattern_rows, case_rows)
    pattern_cards = build_pattern_story_cards(pattern_rows)
    case_cards = build_case_story_cards(case_rows)

    lines: list[str] = [
        "# SectorScout Hindsight Pattern Playbook",
        "",
        "SectorScout is a post-market research and QA system. This playbook is not investment advice, "
        "does not automate trading, and does not report strategy performance.",
        "",
        f"- Generated for: {generated_for.isoformat()}",
        f"- Generated at UTC: {generated_at}",
        f"- Config hash: {config_hash(config)}",
        f"- Git commit: {get_git_commit() or 'unknown'}",
        "",
        "## Methodology Guardrails",
        "",
    ]
    for row in build_methodology_guardrail_rows():
        lines.extend(
            [
                f"### {row['Principle']}",
                "",
                f"- How SectorScout applies it: {row['How SectorScout applies it']}",
                f"- Reviewer check: {row['Reviewer check']}",
                "",
            ]
        )

    lines.extend(["## Current Readout", ""])
    if not pattern_rows or not case_rows:
        lines.extend(
            [
                "No replay hypothesis registry is available yet.",
                "",
                "Prepare the lab with:",
                "",
                "```bash",
                ".venv/bin/sectorscout hindsight seed-events",
                ".venv/bin/sectorscout hindsight seed-evidence",
                ".venv/bin/sectorscout hindsight build-gates",
                ".venv/bin/sectorscout hindsight build-observation-links",
                ".venv/bin/sectorscout hindsight build-hypotheses",
                "```",
                "",
            ]
        )
    else:
        lines.extend(_summary_lines(summary_cards))
        lines.extend(["", "## Pattern Candidates", ""])
        for card in pattern_cards:
            lines.extend(_pattern_card_lines(card))
        lines.extend(["## Case Map", ""])
        for card in case_cards:
            lines.extend(_case_card_lines(card))
        lines.extend(["## Industry Evidence Profiles", ""])
        lines.extend(_industry_profile_lines(industry_profiles))
        lines.extend(["## Pattern Candidate Cards", ""])
        lines.extend(_pattern_candidate_lines(discovery_candidates))
        lines.extend(["## Industry + Technical Matrix", ""])
        lines.extend(_pattern_matrix_lines(pattern_matrix))
        lines.extend(["## Matrix Diagnostics", ""])
        lines.extend(_pattern_diagnostic_lines(pattern_diagnostics))
        lines.extend(["## Official Source Audit", ""])
        lines.extend(_source_audit_lines(source_audit))
        lines.extend(["## SEC Filing Metadata", ""])
        lines.extend(_sec_metadata_lines(sec_metadata))
        lines.extend(["## SEC Company Facts Candidates", ""])
        lines.extend(_companyfacts_lines(companyfacts, companyfacts_status))
        lines.extend(["## Source Snapshots", ""])
        lines.extend(_source_snapshot_lines(source_snapshot_dir))
        lines.extend(["## Research Queue", ""])
        for item in _playbook_research_queue(pattern_cards, case_cards):
            lines.append(f"- {item}")
        lines.append("")

    lines.extend(
        [
            "## Promotion Boundary",
            "",
            "- Treat support as a research hypothesis only.",
            "- Do not modify SectorScout base scores from this playbook.",
            "- Do not treat DATA GAP as either support or rejection.",
            "- If a control case supports the same mechanism, narrow or reject the mechanism before replay design.",
            "- Formal validation, if added later, must remain separate from this case-study playbook.",
            "",
        ]
    )
    return "\n".join(lines)


def _summary_lines(cards: list[dict[str, str]]) -> list[str]:
    lines = ["| Lens | Current value | Review meaning |", "| --- | --- | --- |"]
    for card in cards:
        lines.append(f"| {_md(card.get('Label'))} | {_md(card.get('Value'))} | {_md(card.get('Detail'))} |")
    return lines


def _pattern_card_lines(card: dict[str, str]) -> list[str]:
    return [
        f"### {_md(card.get('Title'))}",
        "",
        f"- Lane: {_md(card.get('Lane'))}",
        f"- Current read: {_md(card.get('Status'))}",
        f"- Leader support: {_md(card.get('Support'))}",
        f"- Leader gaps: {_md(card.get('Gaps'))}",
        f"- Control check: {_md(card.get('Control'))}",
        f"- What this teaches: {_md(card.get('Takeaway'))}",
        f"- Next research action: {_md(card.get('Next'))}",
        "",
    ]


def _case_card_lines(card: dict[str, str]) -> list[str]:
    return [
        f"### {_md(card.get('Symbol'))} - {_md(card.get('Role'))}",
        "",
        f"- Industry lane: {_md(card.get('Industry'))}",
        f"- Technical lane: {_md(card.get('Technical'))}",
        f"- Timing lane: {_md(card.get('Timing'))}",
        f"- Read: {_md(card.get('Read'))}",
        f"- Next data task: {_md(card.get('Next'))}",
        "",
    ]


def _source_audit_lines(items: list[Any]) -> list[str]:
    if not items:
        return ["No source audit rows are available yet.", ""]
    lines = [
        "| Symbols | Source type | Roles | PIT status | Remote status | Source URL | Review note |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in items:
        lines.append(
            " | ".join(
                [
                    f"| {_md(', '.join(item.symbols))}",
                    _md(item.source_type),
                    _md(", ".join(item.source_roles)),
                    _md(item.pit_status),
                    _md(item.remote_status),
                    _md(item.source_url),
                    f"{_md(item.reviewer_note)} |",
                ]
            )
        )
    lines.append("")
    return lines


def _industry_profile_lines(profiles: list[Any]) -> list[str]:
    summary = industry_profile_summary(profiles)
    lines = [
        f"- Profiles: {_md(summary.get('profiles'))}",
        f"- PIT-ready profiles: {_md(summary.get('pit_ready_profiles'))}",
        f"- Data-gap profiles: {_md(summary.get('data_gap_profiles'))}",
        f"- Future-context profiles: {_md(summary.get('future_context_profiles'))}",
        "",
    ]
    if not profiles:
        return lines + ["No industry evidence profiles are available yet.", ""]
    lines.extend(
        [
            "| Symbol | Industry node | Demand driver | Demand stage | Status | Mechanism tags | Review note |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for profile in profiles:
        lines.append(
            " | ".join(
                [
                    f"| {_md(profile.symbol)}",
                    _md(profile.industry_chain_node),
                    _md(profile.demand_driver),
                    _md(profile.demand_stage),
                    _md(profile.profile_status),
                    _md(", ".join(profile.mechanism_tags) or "-"),
                    f"{_md(profile.review_note)} |",
                ]
            )
        )
    lines.append("")
    return lines


def _pattern_matrix_lines(rows: list[Any]) -> list[str]:
    summary = pattern_matrix_summary(rows)
    lines = [
        f"- Matrix rows: {_md(summary.get('rows'))}",
        f"- Aligned rows: {_md(summary.get('aligned_rows'))}",
        f"- Industry-ready but technical-gap rows: {_md(summary.get('industry_ready_technical_gap_rows'))}",
        f"- Control review rows: {_md(summary.get('control_review_rows'))}",
        "",
    ]
    if not rows:
        return lines + ["No industry plus technical matrix rows are available yet.", ""]
    lines.extend(
        [
            "| Symbol | Demand driver | Industry status | Stage 2 | Benchmark RS | Technical status | Matrix label | Review note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for row in rows:
        lines.append(
            " | ".join(
                [
                    f"| {_md(row.symbol)}",
                    _md(row.demand_driver),
                    _md(row.industry_status),
                    _md(row.stage2_status),
                    _md(row.benchmark_rs_status),
                    _md(row.technical_status),
                    _md(row.alignment_label),
                    f"{_md(row.review_note)} |",
                ]
            )
        )
    lines.append("")
    return lines


def _pattern_diagnostic_lines(items: list[Any]) -> list[str]:
    summary = pattern_diagnostics_summary(items)
    lines = [
        f"- Diagnostics: {_md(summary.get('diagnostics'))}",
        f"- High-review items: {_md(summary.get('high_review_items'))}",
        f"- Data tasks: {_md(summary.get('data_tasks'))}",
        "",
    ]
    if not items:
        return lines + ["No matrix diagnostics are available yet.", ""]
    lines.extend(
        [
            "| Diagnostic | Status | Symbols | Interpretation | Next action | Guardrail |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in items:
        lines.append(
            " | ".join(
                [
                    f"| {_md(item.title)}",
                    _md(item.status),
                    _md(", ".join(item.symbols) or "-"),
                    _md(item.interpretation),
                    _md(item.next_action),
                    f"{_md(item.guardrail)} |",
                ]
            )
        )
    lines.append("")
    return lines


def _pattern_candidate_lines(items: list[Any]) -> list[str]:
    summary = pattern_candidates_summary(items)
    lines = [
        f"- Cards: {_md(summary.get('candidates'))}",
        f"- Ready for replay-design review: {_md(summary.get('review_ready'))}",
        f"- Technical data tasks: {_md(summary.get('technical_data_tasks'))}",
        f"- Guardrail items: {_md(summary.get('guardrail_items'))}",
        f"- Control checks: {_md(summary.get('control_checks'))}",
        "",
    ]
    if not items:
        return lines + ["No pattern candidate cards are available yet.", ""]
    for item in items:
        lines.extend(
            [
                f"### {_md(item.title)}",
                "",
                f"- Status: {_md(item.readiness_status)}",
                f"- Mechanism: {_md(item.mechanism)}",
                f"- Current read: {_md(item.reviewer_readout)}",
                f"- Supporting symbols: {_md(', '.join(item.supporting_symbols) or '-')}",
                f"- Blocked symbols: {_md(', '.join(item.blocked_symbols) or '-')}",
                f"- Controls to compare: {_md(', '.join(item.control_symbols) or '-')}",
                f"- Industry evidence required: {_md('; '.join(item.industry_evidence_required))}",
                f"- Technical confirmation required: {_md('; '.join(item.technical_confirmation_required))}",
                f"- Guardrail: {_md(item.anti_hindsight_guardrail)}",
                f"- Next research step: {_md(item.next_research_step)}",
                "",
            ]
        )
    return lines


def _source_snapshot_lines(source_snapshot_dir: Path) -> list[str]:
    manifest_path = source_snapshot_dir / "source_snapshots_manifest.json"
    if not manifest_path.exists():
        return [
            f"No source snapshot manifest found at `{_md(manifest_path)}`.",
            "Run `.venv/bin/sectorscout hindsight snapshot-sources` after reviewing the source list.",
            "",
        ]
    try:
        snapshots = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return [f"Source snapshot manifest at `{_md(manifest_path)}` is not valid JSON.", ""]
    lines = [
        "| Symbols | Source type | Fetch status | Title | Local path | Excerpt |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for snapshot in snapshots:
        lines.append(
            " | ".join(
                [
                    f"| {_md(', '.join(snapshot.get('symbols') or []))}",
                    _md(snapshot.get("source_type")),
                    _md(snapshot.get("fetch_status")),
                    _md(snapshot.get("title") or "-"),
                    _md(snapshot.get("local_path") or "-"),
                    f"{_md(snapshot.get('excerpt') or snapshot.get('error') or '-')} |",
                ]
            )
        )
    lines.append("")
    return lines


def _sec_metadata_lines(items: list[Any]) -> list[str]:
    if not items:
        return ["No SEC filing metadata rows are available yet.", ""]
    lines = [
        "| Symbol | Event type | CIK | Accession | Status | Form | Filing date | Accepted at | Document |",
        "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for item in items:
        lines.append(
            " | ".join(
                [
                    f"| {_md(item.symbol)}",
                    _md(item.event_type),
                    _md(item.cik),
                    _md(item.accession_number),
                    _md(item.metadata_status),
                    _md(item.form or "-"),
                    _md(item.filing_date or "-"),
                    _md(item.acceptance_datetime or item.seed_published_at_utc or "-"),
                    f"{_md(item.primary_document or item.document or '-')} |",
                ]
            )
        )
    lines.append("")
    return lines


def _companyfacts_lines(candidates: list[Any], statuses: list[Any]) -> list[str]:
    summary = companyfacts_summary(candidates, statuses)
    lines = [
        f"- Eligible SEC CIKs: {_md(summary.get('eligible_ciks'))}",
        f"- Fetched CIKs: {_md(summary.get('fetched_ciks'))}",
        f"- Candidate facts: {_md(summary.get('candidate_facts'))}",
        "",
    ]
    if not candidates:
        lines.extend(
            [
                "No SEC companyfacts candidates are loaded yet.",
                "Run `.venv/bin/sectorscout hindsight companyfacts --fetch-remote` or `hindsight refresh --fetch-companyfacts` after reviewing SEC fair-access settings.",
                "",
            ]
        )
        if statuses:
            lines.extend(
                [
                    "| Symbol | CIK | Fetch status | Companyfacts URL |",
                    "| --- | --- | --- | --- |",
                ]
            )
            for status in statuses:
                lines.append(
                    " | ".join(
                        [
                            f"| {_md(status.symbol)}",
                            _md(status.cik),
                            _md(status.fetch_status),
                            f"{_md(status.companyfacts_url)} |",
                        ]
                    )
                )
            lines.append("")
        return lines
    lines.extend(
        [
            "| Symbol | Fact | Value | Unit | Period end | Filed | Form | PIT status | Review note |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )
    for item in candidates:
        lines.append(
            " | ".join(
                [
                    f"| {_md(item.symbol)}",
                    _md(f"{item.taxonomy}:{item.fact_name}"),
                    _md(item.value),
                    _md(item.unit),
                    _md(item.period_end_date),
                    _md(item.filed_at),
                    _md(item.form),
                    _md(item.pit_status),
                    f"{_md(item.review_note)} |",
                ]
            )
        )
    lines.append("")
    return lines


def _playbook_research_queue(pattern_cards: list[dict[str, str]], case_cards: list[dict[str, str]]) -> list[str]:
    tasks: list[str] = []
    for card in pattern_cards:
        if "data" in str(card.get("Status") or "").lower() or "timing" in str(card.get("Status") or "").lower():
            tasks.append(f"{card['Title']}: {card['Next']}")
    for card in case_cards:
        if card.get("Tone") in {"amber", "red"}:
            tasks.append(f"{card['Symbol']}: {card['Next']}")
    return tasks or ["No immediate data task from the current readout."]


def _md(value: Any) -> str:
    text = str(value if value is not None else "-").replace("\n", " ").strip()
    return text.replace("|", "\\|") or "-"
