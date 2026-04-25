from __future__ import annotations

from collections import Counter

from sectorscout.config import SectorScoutConfig


def portfolio_risk_passes(
    config: SectorScoutConfig,
    *,
    candidate_rank: int,
    theme_id: str,
    triggered_theme_ids: list[str],
    market_risk_state: str,
) -> tuple[bool, str]:
    if market_risk_state == "RISK_OFF":
        return False, "Market regime blocks new long entries."
    if candidate_rank >= config.portfolio.max_new_positions_per_day:
        return False, "Max new positions per day reached."

    theme_counts = Counter(triggered_theme_ids)
    projected_theme_weight = theme_counts[theme_id] * config.portfolio.max_single_position_weight
    if projected_theme_weight > config.portfolio.max_theme_exposure:
        return False, "Theme exposure limit exceeded."
    return True, "Portfolio risk constraints pass."
