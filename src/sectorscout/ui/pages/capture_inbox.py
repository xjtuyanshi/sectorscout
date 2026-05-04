from __future__ import annotations

from datetime import date

import streamlit as st

from sectorscout.intel.capture_inbox import capture_markdown_text, capture_text
from sectorscout.intel.public_web import collect_public_url
from sectorscout.intel.storage import save_media_bytes
from sectorscout.intel.vision_extract import extract_image_observation
from sectorscout.ui.data import UIContext


def render(ctx: UIContext) -> None:
    st.title("Capture Inbox")
    st.caption("Manual capture is the safe MVP path for private/community screenshots and pasted notes.")
    platform = st.selectbox("Platform", ["discord", "x", "website", "newsletter", "youtube", "other"])
    rights_scope = st.selectbox("Rights scope", ["manual_private", "public", "authorized_channel"])
    author = st.text_input("Author / source name")
    channel = st.text_input("Channel / source label")
    tags = st.text_input("Tags", placeholder="NQ, QQQ, NVDA")
    source_id = st.text_input("Source ID", value=f"{platform}_manual")

    st.subheader("Paste Text")
    pasted = st.text_area("Raw pasted content", height=180)
    if st.button("Save pasted text", disabled=not pasted.strip()):
        raw_item_id, view_id = capture_text(
            ctx.config,
            pasted,
            source_id=source_id,
            platform=platform,
            author=author or None,
            rights_scope=rights_scope,
            asof_date=ctx.asof_date,
            metadata={"channel": channel, "tags": tags},
        )
        st.success(f"Saved raw item {raw_item_id}. Draft view: {view_id or 'already existed'}.")

    st.subheader("Upload Markdown Or Image")
    uploaded = st.file_uploader("Upload .md, .png, .jpg, .jpeg, .webp", type=["md", "png", "jpg", "jpeg", "webp"])
    allow_vision_provider = st.checkbox(
        "Allow configured vision provider to process this image",
        value=False,
        help="For private or authorized-channel captures, leave this off unless you explicitly want the image sent to the configured vision provider.",
    )
    if uploaded is not None and st.button("Save uploaded file"):
        payload = uploaded.getvalue()
        if uploaded.name.lower().endswith(".md"):
            raw_item_id, view_id = capture_markdown_text(ctx.config, payload.decode("utf-8"), asof_date=ctx.asof_date)
            st.success(f"Saved markdown item {raw_item_id}. Draft view: {view_id or 'already existed'}.")
        else:
            media_id = save_media_bytes(
                ctx.config,
                payload,
                filename=uploaded.name,
                raw_item_id=None,
                source_id=source_id,
                rights_scope=rights_scope,
                metadata={
                    "platform": platform,
                    "author": author,
                    "channel": channel,
                    "tags": tags,
                    "vision_provider_consent": allow_vision_provider,
                },
            )
            observation_id = extract_image_observation(ctx.config, media_id)
            st.success(f"Saved media {media_id}. Observation: {observation_id}.")

    st.subheader("Public URL")
    url = st.text_input("Public URL")
    if st.button("Collect public URL", disabled=not url.strip()):
        result = collect_public_url(ctx.config, url, asof_date=ctx.asof_date)
        if result.status == "COLLECTED":
            st.success(f"Collected {result.title or result.url}")
        else:
            st.warning(f"{result.status}: {result.reason}")
