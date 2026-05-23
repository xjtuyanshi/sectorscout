from __future__ import annotations

import pandas as pd
import streamlit as st

from sectorscout.intel.storage import insert_note
from sectorscout.ui.data import UIContext, latest_rows, parse_json_list, table_df
from sectorscout.ui.workbench import get_selected_symbol, render_ticker_inspector, set_selected_symbol


def _symbols_from_views(views: pd.DataFrame) -> set[str]:
    symbols: set[str] = set()
    for _, row in views.iterrows():
        symbols.update(str(item).upper() for item in parse_json_list(row.get("canonical_symbols_json")))
    return symbols


def render(ctx: UIContext) -> None:
    st.title("Ticker Detail")
    stock_scores = latest_rows(ctx.config, "stock_scores")
    signals = latest_rows(ctx.config, "signals")
    views = table_df(ctx.config, "intel_trade_views", limit=1000)
    symbols = set()
    if not stock_scores.empty:
        symbols.update(stock_scores["symbol"].astype(str).str.upper().tolist())
    if not signals.empty:
        symbols.update(signals["symbol"].astype(str).str.upper().tolist())
    if not views.empty:
        symbols.update(_symbols_from_views(views))
    if not symbols:
        st.info("No ticker data available yet.")
        return
    sorted_symbols = sorted(symbols)
    current = get_selected_symbol(ctx)
    index = sorted_symbols.index(current) if current in sorted_symbols else 0
    symbol = st.selectbox("Symbol", sorted_symbols, index=index)
    set_selected_symbol(symbol)
    left, right = st.columns([2.2, 1.1], gap="medium")
    with left:
        st.subheader("Internal")
        if not stock_scores.empty:
            st.dataframe(stock_scores[stock_scores["symbol"].astype(str).str.upper() == symbol], use_container_width=True)
        if not signals.empty:
            st.dataframe(signals[signals["symbol"].astype(str).str.upper() == symbol], use_container_width=True)
        st.subheader("External")
        if views.empty:
            st.write("No external views.")
        else:
            mask = views["canonical_symbols_json"].apply(lambda value: symbol in [str(item).upper() for item in parse_json_list(value)])
            st.dataframe(views[mask], use_container_width=True)
    with right:
        render_ticker_inspector(ctx, symbol)

    st.subheader("Research Notes")
    with st.form(f"note_form_{symbol}"):
        note = st.text_area("Personal note", placeholder="Research context, watch plan, follow-up question...")
        saved = st.form_submit_button("Save note", disabled=not note.strip())
    if saved:
        note_id = insert_note(
            ctx.config,
            symbol=symbol,
            object_type="ticker",
            object_id=symbol,
            asof_date=ctx.asof_date,
            note_text=note,
        )
        st.success(f"Saved note {note_id}.")
        st.rerun()
    notes = table_df(ctx.config, "intel_notes", limit=500)
    if not notes.empty:
        symbol_notes = notes[notes["symbol"].astype(str).str.upper() == symbol].sort_values("updated_at", ascending=False)
        if not symbol_notes.empty:
            st.dataframe(symbol_notes, use_container_width=True)
