from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

import exchange_calendars as xcals
import pandas as pd

from sectorscout.config import SectorScoutConfig

UTC = ZoneInfo("UTC")


def get_exchange_calendar(config: SectorScoutConfig):
    return xcals.get_calendar(config.market.calendar)


def market_timezone(config: SectorScoutConfig) -> ZoneInfo:
    return ZoneInfo(config.market.timezone)


def asof_market_close(session_date: date | str, config: SectorScoutConfig) -> datetime:
    """Return the actual session close in UTC for a NYSE trading session."""
    calendar = get_exchange_calendar(config)
    session = pd.Timestamp(session_date)
    if session.tzinfo is not None:
        session = session.tz_convert("UTC").tz_localize(None)
    else:
        session = session.tz_localize(None)

    if not calendar.is_session(session):
        raise ValueError(f"{session.date()} is not a session for {config.market.calendar}")

    close_ts = calendar.session_close(session)
    if close_ts.tzinfo is None:
        close_ts = close_ts.tz_localize("UTC")
    else:
        close_ts = close_ts.tz_convert("UTC")
    return close_ts.to_pydatetime().astimezone(UTC)


def to_market_time(timestamp_utc: datetime, config: SectorScoutConfig) -> datetime:
    if timestamp_utc.tzinfo is None:
        raise ValueError("timestamp_utc must be timezone-aware")
    return timestamp_utc.astimezone(market_timezone(config))
