from __future__ import annotations

from sectorscout.config import SectorScoutConfig


def market_gate_allows_new_long(risk_state: str) -> tuple[bool, str]:
    if risk_state == "RISK_OFF":
        return False, "Market regime is RISK_OFF; no new long entries."
    if risk_state == "NEUTRAL":
        return True, "Market regime is NEUTRAL; Phase 5 must apply reduced sizing/A+ filters."
    return True, "Market regime permits new long research candidates."


def portfolio_risk_passes(
    config: SectorScoutConfig,
    *,
    candidate_rank: int,
    theme_id: str,
    selected_theme_ids: list[str],
) -> tuple[bool, str]:
    if candidate_rank >= config.portfolio.max_new_positions_per_day:
        return False, "Max new positions per day reached."

    projected_theme_count = selected_theme_ids.count(theme_id) + 1
    projected_theme_weight = projected_theme_count * config.portfolio.max_single_position_weight
    if projected_theme_weight > config.portfolio.max_theme_exposure:
        return False, "Theme exposure limit exceeded."
    return True, "Portfolio risk constraints pass."
