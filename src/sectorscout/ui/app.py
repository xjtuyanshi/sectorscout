from __future__ import annotations

import argparse
import importlib
from pathlib import Path

import streamlit as st

from sectorscout.ui.data import load_ui_context
from sectorscout.ui.workbench import inject_tradingview_styles, render_top_bar


PAGES: dict[str, str] = {
    "Overview": "overview",
    "Research Checklist": "workflow",
    "External Intel": "external_intel",
    "Capture Inbox": "capture_inbox",
    "Vision Review": "vision_review",
    "Internal vs External Overlap": "overlap",
    "Ticker Detail": "ticker_detail",
    "Historical Pattern Discovery": "historical_lab",
    "Notes / Review": "notes_review",
    "Settings / Source Status": "settings",
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
    module = importlib.import_module(f"sectorscout.ui.pages.{PAGES[selected]}")
    module.render(ctx)


if __name__ == "__main__":
    main()
