from __future__ import annotations

import re


KNOWN_SYMBOLS = {
    "ES",
    "NQ",
    "GC",
    "SPX",
    "NDX",
    "SPY",
    "QQQ",
    "IWM",
    "SMH",
    "NVDA",
    "HIMS",
    "ASTS",
    "GLD",
}

SYMBOL_ALIASES = {
    "GOLD": "GC",
    "GC": "GC",
    "ES": "ES",
    "NQ": "NQ",
}

RELATED_SYMBOLS = {
    "ES": ["ES", "SPX", "SPY"],
    "SPX": ["SPX", "SPY", "ES"],
    "SPY": ["SPY", "SPX", "ES"],
    "NQ": ["NQ", "NDX", "QQQ"],
    "NDX": ["NDX", "QQQ", "NQ"],
    "QQQ": ["QQQ", "NDX", "NQ"],
    "GC": ["GC", "GLD"],
    "GLD": ["GLD", "GC"],
}


def canonical_symbol(raw: str) -> str:
    cleaned = raw.strip().upper().removeprefix("$")
    return SYMBOL_ALIASES.get(cleaned, cleaned)


def related_symbols(symbol: str) -> list[str]:
    canonical = canonical_symbol(symbol)
    return RELATED_SYMBOLS.get(canonical, [canonical])


def normalize_symbols(raw_symbols: list[str]) -> list[str]:
    normalized: list[str] = []
    for raw in raw_symbols:
        canonical = canonical_symbol(raw)
        if canonical not in normalized:
            normalized.append(canonical)
        for related in related_symbols(canonical):
            if related not in normalized:
                normalized.append(related)
    return normalized


def extract_symbol_tokens(text: str) -> list[str]:
    candidates: list[str] = []
    for match in re.findall(r"\$([A-Za-z]{1,5})\b", text):
        symbol = canonical_symbol(match)
        if symbol not in candidates:
            candidates.append(symbol)
    for token in re.findall(r"\b[A-Z]{1,5}\b", text):
        symbol = canonical_symbol(token)
        if symbol in KNOWN_SYMBOLS and symbol not in candidates:
            candidates.append(symbol)
    if re.search(r"\bGold\b", text, flags=re.IGNORECASE) and "GC" not in candidates:
        candidates.append("GC")
    return candidates
