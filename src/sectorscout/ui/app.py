from __future__ import annotations

import argparse
from pathlib import Path

import streamlit as st

from sectorscout.ui.data import load_ui_context
from sectorscout.ui.pages import (
    capture_inbox,
    external_intel,
    notes_review,
    overview,
    overlap,
    settings,
    ticker_detail,
    vision_review,
    workflow,
)
from sectorscout.ui.workbench import inject_tradingview_styles, render_top_bar


PAGES = {
    "Overview": overview.render,
    "Research Workflow": workflow.render,
    "External Intel": external_intel.render,
    "Capture Inbox": capture_inbox.render,
    "Vision Review": vision_review.render,
    "Internal vs External Overlap": overlap.render,
    "Ticker Detail": ticker_detail.render,
    "Notes / Review": notes_review.render,
    "Settings / Source Status": settings.render,
}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--config", default="config.yaml")
    args, _ = parser.parse_known_args()
    return args


def main() -> None:
    args = _parse_args()
    st.set_page_config(page_title="SectorScout Intel", layout="wide", initial_sidebar_state="collapsed")
    inject_tradingview_styles()
    with st.sidebar:
        st.title("SectorScout")
        config_path = st.text_input("Config path", value=str(args.config))
        selected = st.radio("Page", list(PAGES), label_visibility="collapsed")
    ctx = load_ui_context(Path(config_path))
    render_top_bar(ctx, page=selected)
    PAGES[selected](ctx)


if __name__ == "__main__":
    main()
