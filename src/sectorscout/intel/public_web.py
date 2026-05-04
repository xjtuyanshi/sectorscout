from __future__ import annotations

import ipaddress
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser

from sectorscout.config import SectorScoutConfig
from sectorscout.intel.storage import insert_raw_item, insert_trade_view, trade_view_exists
from sectorscout.intel.text_extract import extract_trade_view, normalize_text


REDIRECT_STATUS_CODES = {301, 302, 303, 307, 308}
MAX_PUBLIC_REDIRECTS = 5


class UnsafePublicUrlError(ValueError):
    pass


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.title: str | None = None
        self._in_title = False
        self._skip_depth = 0
        self.text_parts: list[str] = []
        self.media_urls: list[str] = []
        self.outbound_links: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attrs_dict = dict(attrs)
        if tag in {"script", "style", "noscript"}:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag == "img" and attrs_dict.get("src"):
            self.media_urls.append(attrs_dict["src"] or "")
        if tag == "a" and attrs_dict.get("href"):
            self.outbound_links.append(attrs_dict["href"] or "")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript"} and self._skip_depth:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        cleaned = data.strip()
        if not cleaned:
            return
        if self._in_title:
            self.title = cleaned
        if self._skip_depth == 0:
            self.text_parts.append(cleaned)


@dataclass(frozen=True)
class PublicWebResult:
    status: str
    raw_item_id: str | None
    title: str | None
    url: str
    reason: str | None = None


@dataclass(frozen=True)
class _HttpFetchResult:
    status_code: int
    content_type: str
    payload: str
    final_url: str
    redirect_url: str | None = None


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


def _looks_login_required(text: str) -> bool:
    lowered = text.lower()
    login_terms = ["log in", "login", "sign in", "password", "captcha"]
    return sum(1 for term in login_terms if term in lowered) >= 2 and len(text) < 2500


def _is_public_http_url(url: str) -> tuple[bool, str | None]:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False, "Only public http/https URLs are supported."
    if parsed.username or parsed.password:
        return False, "Credentialed URLs are not supported."
    host = parsed.hostname
    if not host:
        return False, "URL host is missing."
    lowered = host.lower()
    if lowered in {"localhost", "0.0.0.0"} or lowered.endswith(".local"):
        return False, "Local/private hosts are not supported."
    try:
        address = ipaddress.ip_address(lowered)
    except ValueError:
        return True, None
    if not address.is_global:
        return False, "Local/private IP addresses are not supported."
    return True, None


def _open_url_once(url: str) -> _HttpFetchResult:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "SectorScoutIntel/0.1 public research fetcher"},
    )
    opener = urllib.request.build_opener(_NoRedirectHandler)
    try:
        with opener.open(request, timeout=20) as response:
            content_type = response.headers.get("content-type", "")
            final_url = response.geturl()
            payload = response.read(2_000_000).decode("utf-8", errors="replace")
            return _HttpFetchResult(
                status_code=int(response.status),
                content_type=content_type,
                payload=payload,
                final_url=final_url,
            )
    except urllib.error.HTTPError as exc:
        if exc.code in REDIRECT_STATUS_CODES:
            return _HttpFetchResult(
                status_code=int(exc.code),
                content_type=exc.headers.get("content-type", ""),
                payload="",
                final_url=url,
                redirect_url=exc.headers.get("Location"),
            )
        raise


def _fetch_url(url: str) -> tuple[str, str, str]:
    current_url = url
    redirect_chain: list[str] = []
    for _ in range(MAX_PUBLIC_REDIRECTS + 1):
        allowed, reason = _is_public_http_url(current_url)
        if not allowed:
            raise UnsafePublicUrlError(reason or "Unsupported URL.")
        result = _open_url_once(current_url)
        if result.status_code not in REDIRECT_STATUS_CODES:
            allowed, reason = _is_public_http_url(result.final_url)
            if not allowed:
                raise UnsafePublicUrlError(f"Resolved to unsupported URL: {reason}")
            return result.content_type, result.payload, result.final_url
        if not result.redirect_url:
            raise urllib.error.URLError("Redirect response did not include a Location header.")
        next_url = urllib.parse.urljoin(current_url, result.redirect_url)
        allowed, reason = _is_public_http_url(next_url)
        if not allowed:
            raise UnsafePublicUrlError(f"Redirected to unsupported URL before request: {reason}")
        redirect_chain.append(next_url)
        current_url = next_url
    raise urllib.error.URLError(f"Too many redirects: {' -> '.join(redirect_chain)}")


def collect_public_url(
    config: SectorScoutConfig,
    url: str,
    *,
    source_id: str | None = None,
    asof_date: date | None = None,
    metadata: dict | None = None,
) -> PublicWebResult:
    parsed = urllib.parse.urlparse(url)
    allowed, reason = _is_public_http_url(url)
    if not allowed:
        return PublicWebResult("SKIPPED", None, None, url, reason)
    try:
        content_type, payload, final_url = _fetch_url(url)
    except UnsafePublicUrlError as exc:
        return PublicWebResult("SKIPPED", None, None, url, str(exc))
    except urllib.error.HTTPError as exc:
        if exc.code in {401, 403}:
            return PublicWebResult("LOGIN_REQUIRED", None, None, url, f"HTTP {exc.code}")
        return PublicWebResult("ERROR", None, None, url, f"HTTP {exc.code}")
    except Exception as exc:
        return PublicWebResult("ERROR", None, None, url, str(exc))
    allowed, reason = _is_public_http_url(final_url)
    if not allowed:
        return PublicWebResult("SKIPPED", None, None, url, f"Redirected to unsupported URL: {reason}")

    parser = _ReadableHTMLParser()
    if "html" in content_type.lower() or re.search(r"<html|<article|<body", payload, re.I):
        parser.feed(payload)
        raw_text = "\n".join(parser.text_parts)
        title = parser.title
    else:
        raw_text = payload
        title = parsed.netloc
    if _looks_login_required(raw_text):
        return PublicWebResult("LOGIN_REQUIRED", None, title, url, "Page appears to require login.")
    normalized = normalize_text(raw_text)
    raw_item_id = insert_raw_item(
        config,
        source_id=source_id or parsed.netloc.replace(".", "_"),
        source_type="public_web",
        title=title,
        author=None,
        platform="website",
        url=url,
        asof_date=asof_date,
        raw_text=raw_text,
        normalized_text=normalized,
        rights_scope="public",
        collection_method="public_web",
        metadata={
            "content_type": content_type,
            "media_urls": parser.media_urls,
            "outbound_links": parser.outbound_links,
            **(metadata or {}),
        },
    )
    if not trade_view_exists(config, raw_item_id, "rule_text_v1"):
        draft = extract_trade_view(
            raw_text,
            source_id=source_id or parsed.netloc.replace(".", "_"),
            source_type="public_web",
            source_title=title,
            platform="website",
            url=url,
            asof_date=asof_date,
            rights_scope="public",
            requires_review=True,
        )
        insert_trade_view(config, raw_item_id=raw_item_id, draft=draft)
    return PublicWebResult("COLLECTED", raw_item_id, title, url)
