from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from sectorscout.config import SectorScoutConfig
from sectorscout.db import connect_database


OVERLAP_LABELS = {
    "CONFIRMED",
    "CONFLICT",
    "EXTERNAL_ONLY",
    "INTERNAL_ONLY",
    "WATCH_ONLY",
    "NEEDS_REVIEW",
}


@dataclass(frozen=True)
class OverlapRow:
    symbol: str
    internal_status: str
    internal_score: float | None
    theme: str | None
    setup_status: str | None
    external_status: str
    external_sources: str
    external_bias: str
    overlap_label: str
    notes: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "internal_status": self.internal_status,
            "internal_score": self.internal_score,
            "theme": self.theme,
            "setup_status": self.setup_status,
            "external_status": self.external_status,
            "external_sources": self.external_sources,
            "external_bias": self.external_bias,
            "overlap_label": self.overlap_label,
            "notes": self.notes,
        }


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


def _safe_df(config: SectorScoutConfig, sql: str, params: list | None = None) -> pd.DataFrame:
    with connect_database(config.database.path) as connection:
        return connection.execute(sql, params or []).fetchdf()


def load_internal_symbols(config: SectorScoutConfig) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if _table_exists(config, "stock_scores"):
        frames.append(
            _safe_df(
                config,
                """
                SELECT symbol, 'stock_score' AS source, state AS internal_status,
                       stock_opportunity_score AS internal_score, theme_id AS theme,
                       NULL AS setup_status
                FROM stock_scores
                QUALIFY row_number() OVER (PARTITION BY symbol ORDER BY asof_date DESC, stock_opportunity_score DESC) = 1
                """,
            )
        )
    if _table_exists(config, "signals"):
        frames.append(
            _safe_df(
                config,
                """
                SELECT symbol, 'setup_candidate' AS source, action_category AS internal_status,
                       NULL AS internal_score, theme_id AS theme, state AS setup_status
                FROM signals
                QUALIFY row_number() OVER (PARTITION BY symbol, setup_type ORDER BY asof_date DESC) = 1
                """,
            )
        )
    if _table_exists(config, "theme_members"):
        frames.append(
            _safe_df(
                config,
                """
                SELECT symbol, 'theme_member' AS source, 'theme_member' AS internal_status,
                       NULL AS internal_score, theme_id AS theme, NULL AS setup_status
                FROM theme_members
                QUALIFY row_number() OVER (PARTITION BY symbol, theme_id ORDER BY valid_from DESC) = 1
                """,
            )
        )
    if _table_exists(config, "trade_ledger"):
        frames.append(
            _safe_df(
                config,
                """
                SELECT symbol, 'trade_ledger_qa' AS source, qa_status AS internal_status,
                       NULL AS internal_score, theme_id AS theme, setup_type AS setup_status
                FROM trade_ledger
                QUALIFY row_number() OVER (PARTITION BY symbol, theme_id, setup_type ORDER BY entry_date DESC) = 1
                """,
            )
        )
    seed = Path("data/intel/sectorscout_watchlist_seed.csv")
    if seed.exists():
        frames.append(pd.read_csv(seed))
    if not frames:
        return pd.DataFrame(
            columns=["symbol", "source", "internal_status", "internal_score", "theme", "setup_status"]
        )
    combined = pd.concat(frames, ignore_index=True, sort=False)
    if combined.empty:
        return combined
    combined["symbol"] = combined["symbol"].astype(str).str.upper()
    return combined.drop_duplicates(["symbol", "source", "theme", "setup_status"])


def load_external_views(config: SectorScoutConfig, *, asof_date: Any | None = None) -> pd.DataFrame:
    if not _table_exists(config, "intel_trade_views"):
        return pd.DataFrame()
    where = "WHERE superseded_by_view_id IS NULL"
    params: list[Any] = []
    if asof_date is not None:
        where += " AND asof_date = ?"
        params.append(asof_date)
    return _safe_df(
        config,
        f"""
        SELECT intel_view_id, source_id, source_title, platform, direction,
               canonical_symbols_json, extraction_confidence, requires_review,
               user_confirmed, risk_notes, invalidation_condition
        FROM intel_trade_views
        {where}
        ORDER BY created_at DESC
        """,
        params,
    )


def _external_symbol_rows(views: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    if views.empty:
        return rows
    for _, view in views.iterrows():
        try:
            symbols = json.loads(view.get("canonical_symbols_json") or "[]")
        except Exception:
            symbols = []
        for symbol in symbols:
            rows.append(
                {
                    "symbol": str(symbol).upper(),
                    "source_id": view.get("source_id"),
                    "source_title": view.get("source_title"),
                    "platform": view.get("platform"),
                    "direction": view.get("direction") or "unknown",
                    "requires_review": bool(view.get("requires_review")),
                    "user_confirmed": bool(view.get("user_confirmed")),
                    "risk_notes": view.get("risk_notes"),
                    "invalidation_condition": view.get("invalidation_condition"),
                }
            )
    return rows


def classify_overlap(
    *,
    has_internal: bool,
    external_direction: str | None,
    requires_review: bool = False,
    user_confirmed: bool = False,
) -> str:
    direction = (external_direction or "unknown").lower()
    if requires_review and not user_confirmed:
        return "NEEDS_REVIEW"
    if has_internal and direction in {"bearish", "mixed"}:
        return "CONFLICT"
    if has_internal and direction in {"bullish", "conditional"}:
        return "CONFIRMED"
    if has_internal and direction in {"neutral", "unknown"}:
        return "NEEDS_REVIEW"
    if not has_internal and direction in {"unknown", "neutral"}:
        return "WATCH_ONLY"
    if not has_internal:
        return "EXTERNAL_ONLY"
    return "INTERNAL_ONLY"


def compute_overlap(config: SectorScoutConfig, *, asof_date: Any | None = None) -> list[dict[str, Any]]:
    internal = load_internal_symbols(config)
    external_rows = _external_symbol_rows(load_external_views(config, asof_date=asof_date))
    internal_by_symbol: dict[str, dict] = {}
    for _, row in internal.iterrows():
        symbol = str(row["symbol"]).upper()
        current = internal_by_symbol.get(symbol)
        score = row.get("internal_score")
        if current is None or (score is not None and pd.notna(score)):
            internal_by_symbol[symbol] = row.to_dict()
    external_by_symbol: dict[str, list[dict]] = {}
    for row in external_rows:
        external_by_symbol.setdefault(row["symbol"], []).append(row)

    symbols = sorted(set(internal_by_symbol) | set(external_by_symbol))
    rows: list[OverlapRow] = []
    for symbol in symbols:
        internal_row = internal_by_symbol.get(symbol)
        external_items = external_by_symbol.get(symbol, [])
        if external_items:
            directions = sorted({str(item["direction"]) for item in external_items})
            sources = sorted({str(item["source_id"]) for item in external_items})
            unresolved_review = any(item["requires_review"] and not item["user_confirmed"] for item in external_items)
            label = classify_overlap(
                has_internal=internal_row is not None,
                external_direction="mixed" if "bearish" in directions and len(directions) > 1 else directions[0],
                requires_review=unresolved_review,
                user_confirmed=False,
            )
            external_status = "mentioned"
            external_bias = ", ".join(directions)
            external_sources = ", ".join(sources)
        else:
            label = "INTERNAL_ONLY"
            external_status = "not_seen"
            external_bias = "none"
            external_sources = ""
        rows.append(
            OverlapRow(
                symbol=symbol,
                internal_status=str(internal_row.get("internal_status")) if internal_row else "none",
                internal_score=(
                    float(internal_row["internal_score"])
                    if internal_row and internal_row.get("internal_score") is not None and pd.notna(internal_row.get("internal_score"))
                    else None
                ),
                theme=str(internal_row.get("theme")) if internal_row and internal_row.get("theme") is not None else None,
                setup_status=str(internal_row.get("setup_status")) if internal_row and internal_row.get("setup_status") is not None else None,
                external_status=external_status,
                external_sources=external_sources,
                external_bias=external_bias,
                overlap_label=label,
                notes="Overlay only; base SectorScout scores are unchanged.",
            )
        )
    return [row.to_dict() for row in rows]
