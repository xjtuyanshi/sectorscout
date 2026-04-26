from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date

from sectorscout.config import SectorScoutConfig
from sectorscout.data_quality import compute_data_quality
from sectorscout.db import connect_database
from sectorscout.setups import detect_setups


REPORT_CATEGORIES = [
    "Research only",
    "Watchlist",
    "Setup forming",
    "Triggered setup candidate",
    "Triggered actionable setup",
    "Blocked by market regime",
    "Exit/review",
]


@dataclass(frozen=True)
class DailyReport:
    asof_date: str
    disclaimer: str
    data_quality: dict
    categories: dict[str, list[dict]]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, indent=2)


def generate_daily_report(config: SectorScoutConfig, asof_date: date) -> DailyReport:
    detect_setups(config, asof_date, persist=True)
    categories = {category: [] for category in REPORT_CATEGORIES}
    with connect_database(config.database.path) as connection:
        signal_rows = connection.execute(
            """
            SELECT
                symbol, theme_id, setup_type, state, action_category,
                actionable, entry_trigger, stop_loss, reward_risk,
                data_quality_pass, data_quality_reason, market_gate_pass,
                market_gate_reason, market_regime_risk_state,
                portfolio_risk_pass, portfolio_risk_reason, reason
            FROM signals
            WHERE asof_date = ?
            ORDER BY actionable DESC, symbol, setup_type
            """,
            [asof_date],
        ).fetchall()
        watch_rows = connection.execute(
            """
            SELECT symbol, theme_id, state, reason
            FROM watchlist
            WHERE asof_date = ?
            ORDER BY symbol, theme_id
            """,
            [asof_date],
        ).fetchall()

    for row in signal_rows:
        (
            symbol,
            theme_id,
            setup_type,
            state,
            action_category,
            actionable,
            entry_trigger,
            stop_loss,
            reward_risk,
            data_quality_pass,
            data_quality_reason,
            market_gate_pass,
            market_gate_reason,
            market_regime,
            portfolio_risk_pass,
            portfolio_risk_reason,
            reason,
        ) = row
        categories[action_category].append(
            {
                "symbol": symbol,
                "theme_id": theme_id,
                "setup_type": setup_type,
                "state": state,
                "actionable": bool(actionable),
                "entry_trigger": entry_trigger,
                "stop_loss": stop_loss,
                "reward_risk": reward_risk,
                "data_quality_pass": bool(data_quality_pass),
                "data_quality_reason": data_quality_reason,
                "market_gate_pass": bool(market_gate_pass),
                "market_gate_reason": market_gate_reason,
                "market_regime": market_regime,
                "portfolio_risk_pass": bool(portfolio_risk_pass),
                "portfolio_risk_reason": portfolio_risk_reason,
                "reason": reason,
            }
        )

    signaled = {(item["symbol"], item["theme_id"]) for values in categories.values() for item in values}
    for symbol, theme_id, state, reason in watch_rows:
        if (symbol, theme_id) in signaled:
            continue
        category = "Blocked by market regime" if state == "RISK_OFF" else "Watchlist"
        categories[category].append(
            {
                "symbol": symbol,
                "theme_id": theme_id,
                "state": state,
                "actionable": False,
                "reason": reason,
            }
        )

    return DailyReport(
        asof_date=asof_date.isoformat(),
        disclaimer=(
            "Research output only. Rows are not instructions. "
            "Phase 4 triggered candidates still require Phase 5 execution validation."
        ),
        data_quality=compute_data_quality(config, asof_date).to_dict(),
        categories=categories,
    )
