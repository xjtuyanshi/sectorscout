from __future__ import annotations

from pathlib import Path

import streamlit as st

from sectorscout.intel.capture_inbox import capture_markdown_text, capture_text
from sectorscout.intel.chandler_seed import seed_chandler_fixture
from sectorscout.intel.public_web import collect_public_url
from sectorscout.intel.storage import save_media_bytes
from sectorscout.intel.vision_extract import extract_image_observation
from sectorscout.ui.data import UIContext


MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_UPLOAD_SUFFIXES = {".md", ".png", ".jpg", ".jpeg", ".webp"}


def _detect_image_mime(payload: bytes) -> str | None:
    if payload.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if payload.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if len(payload) >= 12 and payload[:4] == b"RIFF" and payload[8:12] == b"WEBP":
        return "image/webp"
    return None


def _validate_upload_file(filename: str, payload: bytes) -> str | None:
    suffix = Path(filename).suffix.lower()
    if len(payload) > MAX_UPLOAD_BYTES:
        return "File is too large for MVP capture. Keep uploads under 10 MB."
    if suffix not in ALLOWED_UPLOAD_SUFFIXES:
        return "Unsupported upload type. Use Markdown, PNG, JPEG, or WEBP."
    if suffix == ".md":
        try:
            payload.decode("utf-8")
        except UnicodeDecodeError:
            return "Markdown uploads must be UTF-8 encoded."
        return None
    detected_mime = _detect_image_mime(payload)
    expected_mime = "image/jpeg" if suffix in {".jpg", ".jpeg"} else f"image/{suffix.removeprefix('.')}"
    if detected_mime is None:
        return "Image upload must be a readable PNG, JPEG, or WEBP file."
    if detected_mime != expected_mime:
        return f"Image extension does not match detected content type: {detected_mime}."
    return None


def render(ctx: UIContext) -> None:
    st.title("Capture Inbox")
    st.caption("Manual capture is the safe MVP path for private/community screenshots and pasted notes.")

    with st.container(border=True):
        st.subheader("Seed Fixture")
        st.write("Load the local Chandler 2026-04-26 fixture when you want a known public external context sample.")
        if st.button("Load Chandler fixture"):
            raw_item_id = seed_chandler_fixture(ctx.config, asof_date=ctx.asof_date)
            st.success(f"Loaded Chandler fixture raw item {raw_item_id}.")

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
        upload_error = _validate_upload_file(uploaded.name, payload)
        if upload_error:
            st.error(upload_error)
        elif uploaded.name.lower().endswith(".md"):
            text = payload.decode("utf-8")
            raw_item_id, view_id = capture_markdown_text(ctx.config, text, asof_date=ctx.asof_date)
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
        cleaned_url = url.strip()
        result = collect_public_url(ctx.config, cleaned_url, asof_date=ctx.asof_date)
        if result.status == "COLLECTED":
            st.success(f"Collected {result.title or result.url}")
        else:
            st.warning(f"{result.status}: {result.reason}")
