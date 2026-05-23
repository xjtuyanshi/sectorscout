from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd
import streamlit as st

from sectorscout.intel.overlap import compute_overlap
from sectorscout.ui.data import UIContext, latest_rows, parse_json_list, table_df


SELECTED_SYMBOL_KEY = "sectorscout_selected_symbol"


@dataclass(frozen=True)
class SymbolRow:
    symbol: str
    internal_score: float | None
    internal_status: str
    external_count: int
    overlap_label: str
    theme: str | None


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
          max-width: 1680px;
          padding-top: 1.05rem;
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
          font-size: 1.55rem;
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
          min-width: 0;
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
        button[kind="primary"], .stButton > button {
          border-radius: 7px !important;
          font-weight: 650 !important;
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
            {_pill("as-of", asof, "blue")}
            {_pill("symbol", selected_symbol, "green" if selected_symbol != "none" else "")}
            {_pill("mode", "research only", "purple")}
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
        rows.append(
            SymbolRow(
                symbol=symbol,
                internal_score=score,
                internal_status=status or "watch",
                external_count=external_counts.get(symbol, 0),
                overlap_label=overlap_label,
                theme=theme,
            )
        )
    return sorted(rows, key=lambda row: (row.internal_score is None, -(row.internal_score or 0), row.symbol))


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
              <div>{_pill("", row.overlap_label, tone)}</div>
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
          <div>{_pill("overlap", label, _overlap_tone(label))}</div>
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
