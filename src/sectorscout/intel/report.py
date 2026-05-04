from __future__ import annotations

from datetime import date
from pathlib import Path

from sectorscout.config import SectorScoutConfig, config_hash
from sectorscout.db import connect_database
from sectorscout.intel.overlap import compute_overlap
from sectorscout.intel.storage import ensure_intel_tables
from sectorscout.intel.workflow import build_research_queue, workflow_summary
from sectorscout.metadata import get_git_commit


BOUNDARY_NOTE = (
    "SectorScout is a post-market daily/weekly research and QA system. "
    "This report is for research workflow only and does not provide investment advice, "
    "does not automate trading, and does not report strategy performance."
)


def _table_exists(config: SectorScoutConfig, table: str) -> bool:
    with connect_database(config.database.path) as connection:
        row = connection.execute(
            """
            SELECT table_name
            FROM information_schema.tables
            WHERE table_schema = 'main' AND table_name = ?
            """,
            [table],
        ).fetchone()
    return row is not None


def _count(config: SectorScoutConfig, table: str) -> int:
    if not _table_exists(config, table):
        return 0
    with connect_database(config.database.path) as connection:
        return int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])


def _top_rows(config: SectorScoutConfig, table: str, columns: str, order: str, limit: int = 8) -> list[tuple]:
    if not _table_exists(config, table):
        return []
    with connect_database(config.database.path) as connection:
        return connection.execute(
            f"SELECT {columns} FROM {table} ORDER BY {order} LIMIT {limit}"
        ).fetchall()


def _query_rows(config: SectorScoutConfig, sql: str, limit: int = 10) -> list[tuple]:
    with connect_database(config.database.path) as connection:
        return connection.execute(f"{sql} LIMIT {limit}").fetchall()


def generate_intel_daily_report(
    config: SectorScoutConfig,
    asof_date: date,
    *,
    output_dir: str | Path = "data/intel/reports",
) -> Path:
    ensure_intel_tables(config)
    overlap = compute_overlap(config)
    queue = build_research_queue(config, asof_date=asof_date)
    queue_summary = workflow_summary(config, asof_date=asof_date)
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    path = output / f"intel_daily_{asof_date.isoformat()}.md"
    top_themes = _top_rows(
        config,
        "theme_scores",
        "theme_id, theme_score, breadth, members_count",
        "theme_score DESC",
    )
    top_stocks = _top_rows(
        config,
        "stock_scores",
        "symbol, theme_id, stock_opportunity_score, state",
        "stock_opportunity_score DESC",
    )
    setup_rows = _top_rows(config, "signals", "symbol, theme_id, setup_type, action_category", "symbol")
    external_views = _top_rows(
        config,
        "intel_trade_views",
        "source_id, direction, timeframe, summary, requires_review",
        "created_at DESC",
        limit=10,
    )
    image_observations = _top_rows(
        config,
        "intel_image_observations",
        "source_id, timeframe, extraction_provider, extraction_confidence, requires_review",
        "created_at DESC",
        limit=10,
    )
    needs_review = _query_rows(
        config,
        """
        SELECT 'intel_view' AS item_type, intel_view_id AS item_id, source_id, summary
        FROM intel_trade_views
        WHERE requires_review = true
        UNION ALL
        SELECT 'image_observation' AS item_type, observation_id AS item_id, source_id,
               coalesce(inferred_context, 'Image observation pending review') AS summary
        FROM intel_image_observations
        WHERE requires_review = true
        """,
        limit=15,
    )
    notes = _top_rows(
        config,
        "intel_notes",
        "coalesce(symbol, object_id), note_text, updated_at",
        "updated_at DESC",
        limit=10,
    )
    lines = [
        f"# SectorScout Intel Daily Report - {asof_date.isoformat()}",
        "",
        "## System Boundary",
        BOUNDARY_NOTE,
        "",
        "## Internal SectorScout Summary",
        f"- Research workflow queue: {queue_summary['total']} items",
        f"- Urgent review items: {queue_summary['urgent']}",
        f"- Themes: {_count(config, 'themes')}",
        f"- Theme score rows: {_count(config, 'theme_scores')}",
        f"- Stock score rows: {_count(config, 'stock_scores')}",
        f"- Setup candidate rows: {_count(config, 'signals')}",
        f"- Execution QA rows: {_count(config, 'execution_decisions')}",
        f"- Lifecycle QA rows: {_count(config, 'lifecycle_qa')}",
        "",
        "## Top Themes / Sectors",
    ]
    lines.extend([f"- {row[0]} score={row[1]} breadth={row[2]} members={row[3]}" for row in top_themes] or ["- No theme rows available."])
    lines.extend(["", "## Top Stock Candidates"])
    lines.extend([f"- {row[0]} theme={row[1]} score={row[2]} state={row[3]}" for row in top_stocks] or ["- No stock candidate rows available."])
    lines.extend(["", "## Setup Candidates"])
    lines.extend([f"- {row[0]} theme={row[1]} setup={row[2]} category={row[3]}" for row in setup_rows] or ["- No setup candidate rows available."])
    lines.extend(
        [
            "",
            "## External Intel Summary",
            f"- Raw captured items: {_count(config, 'intel_raw_items')}",
            f"- Media items: {_count(config, 'intel_media_items')}",
            f"- Extracted views: {_count(config, 'intel_trade_views')}",
        ]
    )
    lines.extend(
        [
            f"- {row[0]} direction={row[1]} timeframe={row[2]} needs_review={row[4]}: {str(row[3])[:180]}"
            for row in external_views
        ]
        or ["- No external views available."]
    )
    lines.extend(
        [
            "",
            "## Image / Chart Observations",
            f"- Observations: {_count(config, 'intel_image_observations')}",
        ]
    )
    lines.extend(
        [
            f"- {row[0]} timeframe={row[1]} provider={row[2]} clarity={row[3]} needs_review={row[4]}"
            for row in image_observations
        ]
        or ["- No image observations available."]
    )
    lines.extend(["", "## Internal vs External Overlap"])
    lines.extend(
        [
            f"- {row['symbol']}: {row['overlap_label']} internal={row['internal_status']} external={row['external_bias']}"
            for row in overlap[:20]
        ]
        or ["- No overlap rows available."]
    )
    lines.extend(
        [
            "",
            "## Research Workflow Queue",
            f"- Total queue items: {queue_summary['total']}",
            f"- Conflicts: {queue_summary['conflicts']}",
            f"- Follow-ups due: {queue_summary['follow_ups_due']}",
        ]
    )
    lines.extend(
        [
            f"- P{row['priority']} {row['bucket']} {row.get('symbol') or ''} page={row['page']}: {row['reason']}"
            for row in queue[:20]
        ]
        or ["- No open workflow items."]
    )
    lines.extend(
        [
            "",
            "## Needs-Review Items",
            f"- Saved review marks: {_count(config, 'intel_review_marks')}",
        ]
    )
    lines.extend(
        [
            f"- {row[0]} {row[1]} source={row[2]}: {str(row[3])[:180]}"
            for row in needs_review
        ]
        or ["- No open needs-review items."]
    )
    lines.extend(
        [
            "",
            "## Manual Notes",
        ]
    )
    lines.extend(
        [
            f"- {row[0] or 'general'}: {str(row[1])[:180]}"
            for row in notes
        ]
        or ["- No manual notes saved."]
    )
    lines.extend(
        [
            "",
            "## Data Quality / Provenance",
            f"- DuckDB path: {config.database.path}",
            f"- Config hash: {config_hash(config)}",
            f"- Git commit: {get_git_commit()}",
        ]
    )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path
