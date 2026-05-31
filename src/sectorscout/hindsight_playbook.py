from __future__ import annotations

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
from sectorscout.hindsight_source_audit import build_hindsight_source_audit
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
) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_for = asof_date or date.today()
    path = output_dir / f"hindsight_pattern_playbook_{generated_for.isoformat()}.md"
    path.write_text(build_hindsight_pattern_playbook_markdown(config, asof_date=generated_for), encoding="utf-8")
    return path


def build_hindsight_pattern_playbook_markdown(
    config: SectorScoutConfig,
    *,
    asof_date: date | None = None,
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
        lines.extend(["## Official Source Audit", ""])
        lines.extend(_source_audit_lines(source_audit))
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
