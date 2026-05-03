from __future__ import annotations

import re
from datetime import date

from sectorscout.intel.models import TradeViewDraft
from sectorscout.intel.symbol_normalize import extract_symbol_tokens, normalize_symbols


SETUP_KEYWORDS = [
    "demand",
    "supply",
    "liquidity",
    "reclaim",
    "breakout",
    "pullback",
    "sweep",
    "FCR",
    "ATH",
    "control",
]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def extract_timeframe(text: str) -> str:
    lowered = text.lower()
    if re.search(r"\b1\s*-\s*2h\b|\b1\s*/\s*2h\b", lowered):
        return "multi_timeframe"
    if re.search(r"\b15\s*m\b|\b15min\b", lowered):
        return "15m"
    if re.search(r"\b1\s*h\b|\b1h\b", lowered):
        return "1h"
    if re.search(r"\b2\s*h\b|\b2h\b", lowered):
        return "2h"
    if re.search(r"\b4\s*h\b|\b4h\b", lowered):
        return "4h"
    if re.search(r"\b1\s*d\b|\bdaily\b|\bday\b", lowered):
        return "daily"
    if re.search(r"\b1\s*w\b|\bweekly\b|\bweek\b", lowered):
        return "weekly"
    if "htf" in lowered or "high-timeframe" in lowered or "high timeframe" in lowered:
        return "multi_timeframe"
    return "unknown"


def extract_direction(text: str) -> str:
    lowered = text.lower()
    bullish = any(word in lowered for word in ["bullish", "control up", "expansion", "leader"])
    bearish = any(word in lowered for word in ["bearish", "control break", "break below", "fails"])
    conditional = any(word in lowered for word in ["if ", "until proven otherwise", "unless", "wait for"])
    if conditional and bullish:
        return "conditional"
    if bullish and bearish:
        return "mixed"
    if bullish:
        return "bullish"
    if bearish:
        return "bearish"
    if conditional:
        return "conditional"
    return "unknown"


def extract_setup_types(text: str) -> list[str]:
    lowered = text.lower()
    found: list[str] = []
    for keyword in SETUP_KEYWORDS:
        if keyword.lower() in lowered and keyword not in found:
            found.append(keyword)
    return found or ["research_context"]


def _numbers_from_line(line: str) -> list[str]:
    return [match.replace(",", "") for match in re.findall(r"\b\d{3,6}(?:,\d{3})?\b", line)]


def extract_key_levels(text: str) -> list[str]:
    levels: list[str] = []
    keywords = [
        "demand",
        "supply",
        "liquidity",
        "target",
        "toward",
        "bounce",
        "around",
        "near",
        "invalidation",
    ]
    for line in text.splitlines():
        lowered = line.lower()
        if any(keyword in lowered for keyword in keywords):
            for level in _numbers_from_line(line):
                if level not in levels:
                    levels.append(level)
    return levels


def _first_sentence_containing(text: str, keywords: list[str]) -> str | None:
    normalized = normalize_text(text)
    sentences = re.split(r"(?<=[.!?])\s+", normalized)
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            return sentence[:500]
    return None


def extract_trigger_condition(text: str) -> str | None:
    return _first_sentence_containing(
        text,
        ["bullish reaction", "reclaim", "breakout", "pullback", "if tested", "fcr"],
    )


def extract_invalidation_condition(text: str) -> str | None:
    return _first_sentence_containing(
        text,
        ["control break", "control breaks", "control is broken", "break below", "demand fails", "invalidation"],
    )


def extract_target_area(text: str) -> str | None:
    return _first_sentence_containing(text, ["target", "toward", "bounce toward", "ath"])


def extract_no_trade_condition(text: str) -> str | None:
    return _first_sentence_containing(text, ["do not chase", "don't chase", "not chase", "wait for"])


def extract_trade_view(
    text: str,
    *,
    source_id: str,
    source_type: str,
    source_title: str | None = None,
    author: str | None = None,
    platform: str | None = None,
    url: str | None = None,
    asof_date: date | str | None = None,
    published_at: str | None = None,
    collected_at: str | None = None,
    captured_at: str | None = None,
    rights_scope: str = "manual_private",
    requires_review: bool = False,
    media_id: str | None = None,
    extraction_method: str = "rule_text_v1",
) -> TradeViewDraft:
    raw_symbols = extract_symbol_tokens(text)
    canonical_symbols = normalize_symbols(raw_symbols)
    excerpt = normalize_text(text)[:700]
    direction = extract_direction(text)
    timeframe = extract_timeframe(text)
    setup_type = extract_setup_types(text)
    key_levels = extract_key_levels(text)
    summary_bits = []
    if canonical_symbols:
        summary_bits.append("Mentions " + ", ".join(canonical_symbols[:8]))
    if direction != "unknown":
        summary_bits.append(f"Direction/context: {direction}")
    if key_levels:
        summary_bits.append("Visible/text levels: " + ", ".join(key_levels[:8]))
    summary = ". ".join(summary_bits) or "External research context captured for review."
    return TradeViewDraft(
        source_id=source_id,
        source_type=source_type,
        source_title=source_title,
        author=author,
        platform=platform,
        url=url,
        asof_date=asof_date.isoformat() if hasattr(asof_date, "isoformat") else asof_date,
        published_at=published_at,
        collected_at=collected_at,
        captured_at=captured_at,
        raw_symbols=raw_symbols,
        canonical_symbols=canonical_symbols,
        asset_class="unknown",
        timeframe=timeframe,
        direction=direction,
        setup_type=setup_type,
        key_levels=key_levels,
        trigger_condition=extract_trigger_condition(text),
        invalidation_condition=extract_invalidation_condition(text),
        target_area=extract_target_area(text),
        no_trade_condition=extract_no_trade_condition(text),
        risk_notes=_first_sentence_containing(text, ["risk", "fails", "extended", "too high"]),
        summary=summary,
        source_excerpt=excerpt,
        extraction_method=extraction_method,
        extraction_confidence="medium" if raw_symbols or key_levels else "low",
        rights_scope=rights_scope,
        requires_review=requires_review,
        user_confirmed=False,
        media_id=media_id,
    )
