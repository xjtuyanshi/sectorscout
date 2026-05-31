from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st

from sectorscout.intel.overlap import compute_overlap
from sectorscout.ui.data import UIContext, data_freshness_status, latest_rows, parse_json_list, table_df


SELECTED_SYMBOL_KEY = "sectorscout_selected_symbol"


@dataclass(frozen=True)
class SymbolRow:
    symbol: str
    internal_score: float | None
    internal_status: str
    external_count: int
    overlap_label: str
    theme: str | None
    setup_status: str | None = None


def inject_tradingview_styles() -> None:
    st.markdown(
        """
        <style>
        :root {
          --ss-bg: #f5f7fb;
          --ss-panel: #ffffff;
          --ss-panel-soft: #f9fafc;
          --ss-border: #d9dee8;
          --ss-border-soft: #e9edf4;
          --ss-text: #151923;
          --ss-muted: #697386;
          --ss-blue: #1f6feb;
          --ss-green: #1a7f37;
          --ss-amber: #9a6700;
          --ss-red: #cf222e;
          --ss-purple: #8250df;
          --ss-shadow: 0 12px 28px rgba(21, 25, 35, .06);
        }
        .stApp, [data-testid="stAppViewContainer"] {
          background: var(--ss-bg);
          color: var(--ss-text);
        }
        .block-container {
          max-width: 1440px;
          padding-top: 2.25rem;
          padding-bottom: 2rem;
        }
        [data-testid="stSidebar"] {
          border-right: 1px solid var(--ss-border);
          background: #fbfcfe;
        }
        [data-testid="stSidebar"] h1,
        [data-testid="stSidebar"] p,
        [data-testid="stSidebar"] span,
        [data-testid="stSidebar"] label {
          color: var(--ss-text) !important;
        }
        [data-testid="stSidebar"] [data-baseweb="radio"] {
          color: var(--ss-text) !important;
        }
        div[role="radiogroup"] label,
        div[role="radiogroup"] label span,
        div[role="radiogroup"] label p,
        button[role="tab"],
        button[role="tab"] span,
        button[role="tab"] p {
          color: var(--ss-text) !important;
        }
        button[role="tab"][aria-selected="true"] span,
        button[role="tab"][aria-selected="true"] p {
          color: #b42335 !important;
          font-weight: 700 !important;
        }
        [data-testid="stSidebarNav"],
        [data-testid="stSidebarNavItems"] {
          display: none !important;
          height: 0 !important;
          overflow: hidden !important;
        }
        h1, h2, h3 {
          letter-spacing: 0;
          color: var(--ss-text);
        }
        h1 {
          font-size: 1.45rem;
          line-height: 1.2;
          margin-bottom: .25rem;
        }
        h2, h3 {
          font-size: 1rem;
          line-height: 1.25;
        }
        div[data-testid="stMetric"] {
          border: 1px solid var(--ss-border-soft);
          padding: .68rem .75rem;
          border-radius: 8px;
          background: var(--ss-panel);
          box-shadow: none;
        }
        div[data-testid="stMetric"] label {
          color: var(--ss-muted) !important;
          font-size: .72rem !important;
        }
        div[data-testid="stMetric"] [data-testid="stMetricValue"] {
          color: var(--ss-text);
          font-size: 1.18rem;
        }
        .ss-topbar {
          display: flex;
          align-items: center;
          justify-content: space-between;
          flex-wrap: wrap;
          gap: 16px;
          border: 1px solid var(--ss-border);
          background: rgba(255,255,255,.92);
          border-radius: 8px;
          padding: 10px 12px;
          box-shadow: var(--ss-shadow);
          margin-bottom: 14px;
        }
        .ss-brand {
          display: flex;
          align-items: center;
          gap: 10px;
          min-width: 220px;
          flex: 1 1 260px;
        }
        .ss-mark {
          width: 28px;
          height: 28px;
          border-radius: 7px;
          background: linear-gradient(135deg, #1f6feb 0%, #29a56c 100%);
          color: white;
          display: inline-flex;
          align-items: center;
          justify-content: center;
          font-weight: 700;
          font-size: .82rem;
        }
        .ss-brand-title {
          font-size: .95rem;
          font-weight: 700;
          line-height: 1.1;
        }
        .ss-brand-subtitle {
          color: var(--ss-muted);
          font-size: .73rem;
          line-height: 1.25;
        }
        .ss-topbar-meta {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 6px;
          flex: 1 1 260px;
        }
        .ss-pill {
          display: inline-flex;
          align-items: center;
          gap: 6px;
          border: 1px solid var(--ss-border);
          background: var(--ss-panel-soft);
          border-radius: 999px;
          padding: 4px 8px;
          color: var(--ss-muted);
          font-size: .72rem;
          line-height: 1.1;
          white-space: nowrap;
        }
        .ss-pill-blue { color: #174ea6; border-color: #bfdbfe; background: #eff6ff; }
        .ss-pill-green { color: #17633a; border-color: #bbf7d0; background: #f0fdf4; }
        .ss-pill-amber { color: #7c4a03; border-color: #fde68a; background: #fffbeb; }
        .ss-pill-red { color: #991b1b; border-color: #fecaca; background: #fef2f2; }
        .ss-pill-purple { color: #5b21b6; border-color: #ddd6fe; background: #f5f3ff; }
        .ss-section-title {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 8px;
          margin: 4px 0 8px;
        }
        .ss-section-title span:first-child {
          font-size: .86rem;
          font-weight: 700;
          color: var(--ss-text);
        }
        .ss-section-title span:last-child {
          font-size: .72rem;
          color: var(--ss-muted);
        }
        .ss-panel {
          border: 1px solid var(--ss-border);
          background: var(--ss-panel);
          border-radius: 8px;
          padding: 12px;
          box-shadow: none;
        }
        .ss-focus-strip {
          display: flex;
          align-items: center;
          justify-content: space-between;
          gap: 12px;
          border: 1px solid var(--ss-border);
          background: var(--ss-panel);
          border-radius: 8px;
          padding: 10px 12px;
          margin: 2px 0 12px;
        }
        .ss-focus-title {
          min-width: 0;
          font-size: .84rem;
          font-weight: 720;
          color: var(--ss-text);
        }
        .ss-focus-meta {
          display: flex;
          flex-wrap: wrap;
          justify-content: flex-end;
          gap: 6px;
        }
        .ss-page-note {
          border: 1px solid #bfdbfe;
          background: #eff6ff;
          color: #174ea6;
          border-radius: 8px;
          padding: 9px 11px;
          font-size: .8rem;
          line-height: 1.35;
          margin-bottom: 12px;
        }
        .ss-hindsight-summary {
          display: grid;
          grid-template-columns: repeat(4, minmax(0, 1fr));
          gap: 10px;
          margin: 8px 0 14px;
        }
        .ss-research-card-grid {
          display: grid;
          grid-template-columns: repeat(2, minmax(0, 1fr));
          gap: 10px;
          margin: 8px 0 14px;
        }
        .ss-case-card-grid {
          display: grid;
          grid-template-columns: repeat(3, minmax(0, 1fr));
          gap: 10px;
          margin: 8px 0 14px;
        }
        .ss-research-card {
          border: 1px solid var(--ss-border);
          background: var(--ss-panel);
          border-radius: 8px;
          padding: 12px;
          min-height: 126px;
          box-shadow: none;
          overflow-wrap: anywhere;
        }
        .ss-research-card.ss-tone-green { border-left: 4px solid var(--ss-green); }
        .ss-research-card.ss-tone-blue { border-left: 4px solid var(--ss-blue); }
        .ss-research-card.ss-tone-amber { border-left: 4px solid var(--ss-amber); }
        .ss-research-card.ss-tone-red { border-left: 4px solid var(--ss-red); }
        .ss-research-card.ss-tone-purple { border-left: 4px solid var(--ss-purple); }
        .ss-card-kicker {
          color: var(--ss-muted);
          font-size: .68rem;
          font-weight: 700;
          text-transform: uppercase;
          letter-spacing: .04em;
          margin-bottom: 6px;
        }
        .ss-card-title {
          color: var(--ss-text);
          font-size: .92rem;
          font-weight: 760;
          line-height: 1.22;
          margin-bottom: 8px;
        }
        .ss-card-value {
          color: var(--ss-text);
          font-size: 1.25rem;
          font-weight: 780;
          line-height: 1.2;
          margin-bottom: 6px;
        }
        .ss-card-body {
          color: var(--ss-muted);
          font-size: .76rem;
          line-height: 1.35;
          margin-bottom: 8px;
        }
        .ss-card-line {
          display: grid;
          grid-template-columns: 74px minmax(0, 1fr);
          gap: 8px;
          border-top: 1px solid var(--ss-border-soft);
          padding-top: 6px;
          margin-top: 6px;
          font-size: .72rem;
          line-height: 1.3;
        }
        .ss-card-line span:first-child {
          color: var(--ss-muted);
          font-weight: 650;
        }
        .ss-card-line span:last-child {
          color: var(--ss-text);
        }
        .ss-empty-note {
          border: 1px dashed var(--ss-border);
          background: var(--ss-panel-soft);
          color: var(--ss-muted);
          border-radius: 8px;
          padding: 12px;
          font-size: .82rem;
        }
        .ss-symbol-row {
          display: grid;
          grid-template-columns: minmax(58px, .8fr) minmax(70px, 1fr) minmax(64px, .8fr);
          gap: 8px;
          align-items: center;
          border-bottom: 1px solid var(--ss-border-soft);
          padding: 7px 0;
          font-size: .76rem;
        }
        .ss-symbol-row:last-child { border-bottom: 0; }
        .ss-symbol {
          font-weight: 750;
          color: var(--ss-text);
        }
        .ss-muted {
          color: var(--ss-muted);
          font-size: .72rem;
        }
        .ss-mini {
          color: var(--ss-muted);
          font-size: .68rem;
          line-height: 1.25;
        }
        .ss-inspector-symbol {
          font-size: 1.45rem;
          font-weight: 780;
          line-height: 1.1;
          margin-bottom: 3px;
        }
        .ss-kv {
          display: grid;
          grid-template-columns: 100px minmax(0, 1fr);
          gap: 6px 10px;
          font-size: .75rem;
          border-top: 1px solid var(--ss-border-soft);
          padding-top: 8px;
          margin-top: 8px;
        }
        .ss-kv div:nth-child(odd) { color: var(--ss-muted); }
        .ss-kv div:nth-child(even) { color: var(--ss-text); font-weight: 580; }
        .stDataFrame, [data-testid="stDataFrame"] {
          border-radius: 8px;
          overflow: hidden;
        }
        [data-testid="stDataFrame"] div {
          color: var(--ss-text);
        }
        button[kind="primary"], .stButton > button {
          border-radius: 7px !important;
          font-weight: 650 !important;
        }
        @media (max-width: 900px) {
          .block-container { padding-left: 1rem; padding-right: 1rem; }
          .ss-topbar { align-items: flex-start; }
          .ss-topbar-meta { justify-content: flex-start; }
          .ss-focus-strip { align-items: flex-start; flex-direction: column; }
          .ss-focus-meta { justify-content: flex-start; }
          .ss-hindsight-summary,
          .ss-research-card-grid,
          .ss-case-card-grid {
            grid-template-columns: 1fr;
          }
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _pill(label: str, value: str, tone: str = "") -> str:
    cls = "ss-pill" + (f" ss-pill-{tone}" if tone else "")
    prefix = f"{label}: " if label else ""
    return f'<span class="{cls}">{prefix}<strong>{value}</strong></span>'


def render_top_bar(ctx: UIContext, *, page: str) -> None:
    freshness = data_freshness_status(ctx.asof_date)
    asof = ctx.asof_date.isoformat() if ctx.asof_date else "seed"
    selected_symbol = str(st.session_state.get(SELECTED_SYMBOL_KEY) or "none")
    st.markdown(
        f"""
        <div class="ss-topbar">
          <div class="ss-brand">
            <div class="ss-mark">SS</div>
            <div>
              <div class="ss-brand-title">SectorScout Research Workbench</div>
              <div class="ss-brand-subtitle">{page} · post-market research overlay</div>
            </div>
          </div>
          <div class="ss-topbar-meta">
            {_pill("today", freshness["today"].isoformat(), "green")}
            {_pill("snapshot", asof, "blue")}
            {_pill("symbol", selected_symbol, "green" if selected_symbol != "none" else "")}
            {_pill("mode", "research / QA only", "purple")}
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _symbol_set_from_views(views: pd.DataFrame) -> set[str]:
    symbols: set[str] = set()
    if views.empty or "canonical_symbols_json" not in views:
        return symbols
    for _, row in views.iterrows():
        symbols.update(str(item).upper() for item in parse_json_list(row.get("canonical_symbols_json")))
    return symbols


def symbol_universe(ctx: UIContext) -> list[str]:
    symbols: set[str] = set()
    for table in ["stock_scores", "signals", "trade_ledger"]:
        frame = latest_rows(ctx.config, table)
        if not frame.empty and "symbol" in frame:
            symbols.update(frame["symbol"].dropna().astype(str).str.upper().tolist())
    views = table_df(ctx.config, "intel_trade_views", limit=1000)
    symbols.update(_symbol_set_from_views(views))
    return sorted(symbol for symbol in symbols if symbol and symbol != "NAN")


def set_selected_symbol(symbol: str | None) -> None:
    if symbol:
        st.session_state[SELECTED_SYMBOL_KEY] = symbol.upper()


def get_selected_symbol(ctx: UIContext) -> str | None:
    symbols = symbol_universe(ctx)
    current = str(st.session_state.get(SELECTED_SYMBOL_KEY) or "").upper()
    if current in symbols:
        return current
    if symbols:
        st.session_state[SELECTED_SYMBOL_KEY] = symbols[0]
        return symbols[0]
    return None


def render_symbol_focus_control(ctx: UIContext, *, label: str = "Symbol focus") -> str | None:
    symbols = symbol_universe(ctx)
    if not symbols:
        st.selectbox(label, ["No symbols yet"], disabled=True)
        return None
    current = get_selected_symbol(ctx)
    index = symbols.index(current) if current in symbols else 0
    selected = st.selectbox(label, symbols, index=index, key=f"{label}_select")
    set_selected_symbol(selected)
    return selected


def build_symbol_rows(ctx: UIContext) -> list[SymbolRow]:
    stock_scores = latest_rows(ctx.config, "stock_scores")
    views = table_df(ctx.config, "intel_trade_views", limit=1000)
    overlap = {row["symbol"]: row for row in compute_overlap(ctx.config, asof_date=ctx.asof_date)}
    external_counts: dict[str, int] = {}
    for symbol in _symbol_set_from_views(views):
        if symbol not in external_counts:
            external_counts[symbol] = 0
        if not views.empty:
            external_counts[symbol] = int(
                views["canonical_symbols_json"].apply(
                    lambda value, wanted=symbol: wanted in [str(item).upper() for item in parse_json_list(value)]
                ).sum()
            )
    rows: list[SymbolRow] = []
    for symbol in symbol_universe(ctx):
        score: float | None = None
        status = ""
        theme = None
        if not stock_scores.empty and "symbol" in stock_scores:
            symbol_rows = stock_scores[stock_scores["symbol"].astype(str).str.upper() == symbol]
            if not symbol_rows.empty:
                first = symbol_rows.iloc[0]
                if "stock_opportunity_score" in first and pd.notna(first["stock_opportunity_score"]):
                    score = float(first["stock_opportunity_score"])
                status = str(first.get("state") or "")
                theme = str(first.get("theme_id") or "") or None
        overlap_label = str(overlap.get(symbol, {}).get("overlap_label") or "WATCH_ONLY")
        setup_status = overlap.get(symbol, {}).get("setup_status")
        rows.append(
            SymbolRow(
                symbol=symbol,
                internal_score=score,
                internal_status=status or "watch",
                external_count=external_counts.get(symbol, 0),
                overlap_label=overlap_label,
                theme=theme,
                setup_status=str(setup_status) if setup_status else None,
            )
        )
    return sorted(rows, key=lambda row: (row.internal_score is None, -(row.internal_score or 0), row.symbol))


def symbol_rows_dataframe(ctx: UIContext) -> pd.DataFrame:
    rows = build_symbol_rows(ctx)
    return pd.DataFrame(
        [
            {
                "Symbol": row.symbol,
                "Research status": friendly_overlap_label(row.overlap_label),
                "SectorScout score": row.internal_score,
                "SectorScout context": friendly_internal_status(row.internal_status, row.setup_status),
                "External notes": row.external_count,
                "Theme": row.theme or "-",
            }
            for row in rows
        ]
    )


def render_symbol_rail(ctx: UIContext, *, max_rows: int = 18) -> str | None:
    selected = render_symbol_focus_control(ctx)
    rows = build_symbol_rows(ctx)[:max_rows]
    st.markdown('<div class="ss-section-title"><span>Watchlist</span><span>internal + overlay</span></div>', unsafe_allow_html=True)
    if not rows:
        st.info("Capture context or load the demo seed to populate this rail.")
        return selected
    for row in rows:
        tone = _overlap_tone(row.overlap_label)
        score = "-" if row.internal_score is None else f"{row.internal_score:.1f}"
        st.markdown(
            f"""
            <div class="ss-symbol-row">
                <div>
                  <div class="ss-symbol">{row.symbol}</div>
                  <div class="ss-mini">{row.theme or row.internal_status}</div>
                </div>
              <div>{_pill("", friendly_overlap_label(row.overlap_label), tone)}</div>
              <div class="ss-muted">score {score}<br>{row.external_count} ctx</div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    return selected


def _overlap_tone(label: str) -> str:
    return {
        "CONFIRMED": "green",
        "CONFLICT": "red",
        "EXTERNAL_ONLY": "blue",
        "INTERNAL_ONLY": "",
        "WATCH_ONLY": "amber",
        "NEEDS_REVIEW": "purple",
    }.get(label, "")


def friendly_overlap_label(label: str) -> str:
    return {
        "CONFIRMED": "SectorScout and outside context overlap",
        "CONFLICT": "Outside context raises risk",
        "EXTERNAL_ONLY": "Only outside sources mention it",
        "INTERNAL_ONLY": "Only SectorScout is watching it",
        "WATCH_ONLY": "Watch only",
        "NEEDS_REVIEW": "Needs your review",
    }.get(label, label.replace("_", " ").title())


def friendly_internal_status(status: object, setup_status: object | None = None) -> str:
    text = _clean_status_text(status)
    setup = _clean_status_text(setup_status)
    if text in {"", "none"}:
        return "Not in SectorScout for this snapshot"
    if text == "watch_only":
        return "On the research watchlist"
    if text == "theme_member":
        return "Theme member"
    if text == "stock_score":
        return "Ranked candidate"
    readable = text.replace("_", " ").title()
    if setup and setup not in {"none", "nan"}:
        return f"{readable}; setup context: {setup.replace('_', ' ').title()}"
    return readable


def friendly_external_context(value: object) -> str:
    text = _clean_status_text(value)
    if text in {"", "none"}:
        return "No captured outside context"
    labels = {
        "bullish": "Positive outside context",
        "bearish": "Risk or opposing outside context",
        "conditional": "Conditional outside context",
        "mixed": "Mixed outside context",
        "neutral": "Neutral outside context",
        "unknown": "Unclear outside context",
    }
    return ", ".join(labels.get(part.strip().lower(), part.strip().title()) for part in text.split(","))


def overlap_rows_dataframe(rows: list[dict[str, Any]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Symbol": row.get("symbol"),
                "What it means": friendly_overlap_label(str(row.get("overlap_label") or "")),
                "Sector or theme": display_cell(row.get("theme")),
                "SectorScout context": friendly_internal_status(row.get("internal_status"), row.get("setup_status")),
                "SectorScout score": row.get("internal_score"),
                "Outside context": friendly_external_context(row.get("external_bias")),
                "Sources": display_cell(row.get("external_sources")),
                "Review step": overlap_next_step_hint(str(row.get("overlap_label") or "")),
            }
            for row in rows
        ]
    )


def overlap_next_step_hint(label: str) -> str:
    return {
        "CONFIRMED": "Open the ticker detail and compare the setup, levels, and notes.",
        "CONFLICT": "Read both contexts and write a manual review note before relying on the setup.",
        "EXTERNAL_ONLY": "Decide whether this deserves a watchlist row or should remain outside context.",
        "INTERNAL_ONLY": "Optional: capture outside context if this symbol matters for today's review.",
        "WATCH_ONLY": "Keep it visible; current evidence is incomplete.",
        "NEEDS_REVIEW": "Confirm the source capture or image extraction first.",
    }.get(label, "Review manually.")


def _clean_status_text(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return ""
    text = str(value).strip()
    if text.lower() in {"nan", "null", "none"}:
        return ""
    return text.lower()


def display_cell(value: object, fallback: str = "-") -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return fallback
    text = str(value).strip()
    if not text or text.lower() in {"none", "nan", "null"}:
        return fallback
    return text


def _filter_views_for_symbol(views: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if views.empty or "canonical_symbols_json" not in views:
        return pd.DataFrame()
    return views[
        views["canonical_symbols_json"].apply(
            lambda value: symbol.upper() in [str(item).upper() for item in parse_json_list(value)]
        )
    ]


def _latest_symbol_row(frame: pd.DataFrame, symbol: str) -> dict[str, Any]:
    if frame.empty or "symbol" not in frame:
        return {}
    filtered = frame[frame["symbol"].astype(str).str.upper() == symbol.upper()]
    if filtered.empty:
        return {}
    return filtered.iloc[0].to_dict()


def render_ticker_inspector(ctx: UIContext, symbol: str | None = None) -> None:
    symbol = symbol or get_selected_symbol(ctx)
    st.markdown('<div class="ss-section-title"><span>Ticker Inspector</span><span>linked context</span></div>', unsafe_allow_html=True)
    if not symbol:
        st.info("No symbol selected yet.")
        return
    stock = _latest_symbol_row(latest_rows(ctx.config, "stock_scores"), symbol)
    setup = _latest_symbol_row(latest_rows(ctx.config, "signals"), symbol)
    views = _filter_views_for_symbol(table_df(ctx.config, "intel_trade_views", limit=1000), symbol)
    overlap_row = next((row for row in compute_overlap(ctx.config, asof_date=ctx.asof_date) if row["symbol"] == symbol), {})
    label = str(overlap_row.get("overlap_label") or "WATCH_ONLY")
    st.markdown(
        f"""
        <div class="ss-panel">
          <div class="ss-inspector-symbol">{symbol}</div>
          <div>{_pill("status", friendly_overlap_label(label), _overlap_tone(label))}</div>
          <div class="ss-kv">
            <div>Theme</div><div>{stock.get("theme_id") or setup.get("theme_id") or "-"}</div>
            <div>Score</div><div>{_format_value(stock.get("stock_opportunity_score"))}</div>
            <div>State</div><div>{stock.get("state") or "-"}</div>
            <div>Setup</div><div>{setup.get("setup_type") or setup.get("state") or "-"}</div>
            <div>External views</div><div>{len(views)}</div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    if not views.empty:
        st.caption("Latest overlay context")
        preview_columns = [col for col in ["source_id", "direction", "timeframe", "summary", "requires_review"] if col in views]
        st.dataframe(views.sort_values("created_at", ascending=False)[preview_columns].head(5), use_container_width=True, hide_index=True)
    notes = table_df(ctx.config, "intel_notes", limit=500)
    if not notes.empty and "symbol" in notes:
        symbol_notes = notes[notes["symbol"].astype(str).str.upper() == symbol.upper()].sort_values("updated_at", ascending=False)
        if not symbol_notes.empty:
            st.caption("Recent notes")
            st.dataframe(symbol_notes[["note_text", "updated_at"]].head(3), use_container_width=True, hide_index=True)


def _format_value(value: object) -> str:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return "-"
    try:
        return f"{float(value):.1f}"
    except Exception:
        return str(value)
