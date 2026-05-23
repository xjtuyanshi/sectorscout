from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

import yaml

from sectorscout.config import SectorScoutConfig
from sectorscout.intel.storage import insert_raw_item, insert_trade_view, trade_view_exists
from sectorscout.intel.text_extract import extract_trade_view, normalize_text


RECENT_SEARCH_ENDPOINT = "https://api.x.com/2/tweets/search/recent"
DEFAULT_X_SOURCES_PATH = Path("data/intel/x_sources.yaml")
DEFAULT_X_HANDLES = ("optionflys", "rbswingtrader", "ChandlerTrading")
DEFAULT_X_SOURCE_IDS = {
    "optionflys": "x_optionflys",
    "rbswingtrader": "x_rbswingtrader",
    "chandlertrading": "x_chandler",
}
HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


@dataclass(frozen=True)
class XSource:
    handle: str
    source_id: str
    enabled: bool = True
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["tags"] = list(self.tags)
        return payload


@dataclass(frozen=True)
class XCollectResult:
    status: str
    reason: str | None
    query: str
    handles: list[str]
    posts_seen: int
    raw_items: int
    trade_views: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def sanitize_handle(handle: str) -> str:
    cleaned = handle.strip().removeprefix("@")
    if not HANDLE_RE.match(cleaned):
        raise ValueError(f"Unsupported X handle: {handle}")
    return cleaned


def _source_id_for_handle(handle: str) -> str:
    cleaned = sanitize_handle(handle)
    return DEFAULT_X_SOURCE_IDS.get(cleaned.lower(), f"x_{cleaned.lower()}")


def load_x_sources(path: str | Path = DEFAULT_X_SOURCES_PATH) -> list[XSource]:
    source_path = Path(path)
    if not source_path.exists():
        return [XSource(handle=handle, source_id=_source_id_for_handle(handle)) for handle in DEFAULT_X_HANDLES]
    raw = yaml.safe_load(source_path.read_text(encoding="utf-8")) or {}
    sources: list[XSource] = []
    for item in raw.get("sources", []):
        if str(item.get("type") or "x_account") != "x_account":
            continue
        handle = sanitize_handle(str(item.get("handle") or item.get("id") or ""))
        sources.append(
            XSource(
                handle=handle,
                source_id=str(item.get("id") or _source_id_for_handle(handle)),
                enabled=bool(item.get("enabled", True)),
                tags=tuple(str(tag) for tag in item.get("tags", [])),
            )
        )
    return sources


def enabled_handles(path: str | Path = DEFAULT_X_SOURCES_PATH) -> list[str]:
    return [source.handle for source in load_x_sources(path) if source.enabled]


def build_recent_search_query(
    handles: list[str] | tuple[str, ...],
    *,
    include_replies: bool = False,
    include_retweets: bool = False,
) -> str:
    cleaned = [sanitize_handle(handle) for handle in handles]
    if not cleaned:
        raise ValueError("At least one X handle is required.")
    author_clause = " OR ".join(f"from:{handle}" for handle in cleaned)
    query = f"({author_clause})"
    if not include_retweets:
        query += " -is:retweet"
    if not include_replies:
        query += " -is:reply"
    return query


def x_api_status(*, bearer_token: str | None = None) -> dict[str, Any]:
    token_present = bool(bearer_token or os.environ.get("X_BEARER_TOKEN"))
    return {
        "provider": "x_api_recent_search",
        "endpoint": RECENT_SEARCH_ENDPOINT,
        "token_present": token_present,
        "status": "configured" if token_present else "skipped_missing_X_BEARER_TOKEN",
        "default_handles": list(DEFAULT_X_HANDLES),
    }


def _request_recent_search(*, query: str, bearer_token: str, max_results: int) -> dict[str, Any]:
    params = {
        "query": query,
        "max_results": str(max(10, min(max_results, 100))),
        "tweet.fields": "created_at,author_id,public_metrics,lang,entities,context_annotations,attachments,referenced_tweets,conversation_id",
        "expansions": "author_id,attachments.media_keys",
        "user.fields": "username,name,verified",
        "media.fields": "url,preview_image_url,type,width,height,alt_text",
    }
    url = RECENT_SEARCH_ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {bearer_token}",
            "User-Agent": "SectorScoutIntel/0.1 public X API collector",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode("utf-8"))


def _tweet_url(username: str, tweet_id: str) -> str:
    return f"https://x.com/{username}/status/{tweet_id}"


def _published_date(tweet: dict[str, Any]) -> date | None:
    created_at = str(tweet.get("created_at") or "")
    if len(created_at) >= 10:
        try:
            return date.fromisoformat(created_at[:10])
        except ValueError:
            return None
    return None


def collect_x_recent_search(
    config: SectorScoutConfig,
    *,
    handles: list[str] | None = None,
    sources_path: str | Path = DEFAULT_X_SOURCES_PATH,
    asof_date: date | None = None,
    max_results: int = 50,
    include_replies: bool = False,
    include_retweets: bool = False,
    bearer_token: str | None = None,
) -> XCollectResult:
    token = bearer_token or os.environ.get("X_BEARER_TOKEN")
    selected_handles = handles or enabled_handles(sources_path)
    query = build_recent_search_query(
        selected_handles,
        include_replies=include_replies,
        include_retweets=include_retweets,
    )
    if not token:
        return XCollectResult(
            status="SKIPPED",
            reason="X_BEARER_TOKEN is missing. Public/manual capture remains available.",
            query=query,
            handles=[sanitize_handle(handle) for handle in selected_handles],
            posts_seen=0,
            raw_items=0,
            trade_views=0,
        )
    try:
        payload = _request_recent_search(query=query, bearer_token=token, max_results=max_results)
    except urllib.error.HTTPError as exc:
        status = "RATE_LIMITED" if exc.code == 429 else "ERROR"
        return XCollectResult(status=status, reason=f"X API HTTP {exc.code}", query=query, handles=list(selected_handles), posts_seen=0, raw_items=0, trade_views=0)
    except Exception as exc:
        return XCollectResult(status="ERROR", reason=str(exc), query=query, handles=list(selected_handles), posts_seen=0, raw_items=0, trade_views=0)

    users_by_id = {
        str(user.get("id")): user
        for user in (payload.get("includes", {}).get("users") or [])
    }
    media_by_key = {
        str(media.get("media_key")): media
        for media in (payload.get("includes", {}).get("media") or [])
    }
    source_ids = {source.handle.lower(): source.source_id for source in load_x_sources(sources_path)}
    raw_count = 0
    view_count = 0
    for tweet in payload.get("data") or []:
        tweet_id = str(tweet.get("id") or "")
        text = str(tweet.get("text") or "")
        if not tweet_id or not text:
            continue
        user = users_by_id.get(str(tweet.get("author_id")), {})
        username = sanitize_handle(str(user.get("username") or selected_handles[0]))
        source_id = source_ids.get(username.lower(), _source_id_for_handle(username))
        url = _tweet_url(username, tweet_id)
        published = str(tweet.get("created_at") or "") or None
        view_asof = asof_date or _published_date(tweet)
        attachments = tweet.get("attachments") or {}
        media_keys = [str(key) for key in attachments.get("media_keys") or []]
        media_urls = [
            media_by_key[key].get("url") or media_by_key[key].get("preview_image_url")
            for key in media_keys
            if key in media_by_key and (media_by_key[key].get("url") or media_by_key[key].get("preview_image_url"))
        ]
        raw_item_id = insert_raw_item(
            config,
            source_id=source_id,
            source_type="x_account",
            title=f"X post by @{username}",
            author=username,
            platform="x",
            url=url,
            published_at=published,
            asof_date=view_asof,
            raw_text=text,
            normalized_text=normalize_text(text),
            rights_scope="public",
            collection_method="x_api_recent_search",
            metadata={
                "tweet_id": tweet_id,
                "author_id": tweet.get("author_id"),
                "public_metrics": tweet.get("public_metrics") or {},
                "conversation_id": tweet.get("conversation_id"),
                "lang": tweet.get("lang"),
                "media_urls": media_urls,
                "collection_query": query,
            },
        )
        raw_count += 1
        if not trade_view_exists(config, raw_item_id, "x_api_recent_search_v1", asof_date=view_asof):
            draft = extract_trade_view(
                text,
                source_id=source_id,
                source_type="x_account",
                source_title=f"X post by @{username}",
                author=username,
                platform="x",
                url=url,
                asof_date=view_asof,
                published_at=published,
                rights_scope="public",
                requires_review=True,
                extraction_method="x_api_recent_search_v1",
            )
            insert_trade_view(config, raw_item_id=raw_item_id, draft=draft)
            view_count += 1
    return XCollectResult(
        status="COLLECTED",
        reason=None,
        query=query,
        handles=[sanitize_handle(handle) for handle in selected_handles],
        posts_seen=len(payload.get("data") or []),
        raw_items=raw_count,
        trade_views=view_count,
    )
