from __future__ import annotations

import os
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pandas as pd

from sectorscout.config import SectorScoutConfig

UTC = ZoneInfo("UTC")
NY = ZoneInfo("America/New_York")


class SimpleXNYSCalendar:
    """Fast XNYS calendar for SectorScout's daily research workflow.

    This covers regular weekdays, major NYSE holidays, common observed holiday
    rules, and common early closes needed by the fixture/test windows. Set
    SECTORSCOUT_USE_EXCHANGE_CALENDARS=1 to force the external calendar library.
    """

    def is_session(self, session: pd.Timestamp) -> bool:
        session_date = _session_date(session)
        return session_date.weekday() < 5 and session_date not in _nyse_holidays(session_date.year)

    def session_close(self, session: pd.Timestamp) -> pd.Timestamp:
        session_date = _session_date(session)
        if not self.is_session(pd.Timestamp(session_date)):
            raise ValueError(f"{session_date} is not an XNYS session")
        close_hour = 13 if session_date in _nyse_early_closes(session_date.year) else 16
        return pd.Timestamp(datetime.combine(session_date, time(close_hour, 0), NY)).tz_convert("UTC")

    def session_open(self, session: pd.Timestamp) -> pd.Timestamp:
        session_date = _session_date(session)
        if not self.is_session(pd.Timestamp(session_date)):
            raise ValueError(f"{session_date} is not an XNYS session")
        return pd.Timestamp(datetime.combine(session_date, time(9, 30), NY)).tz_convert("UTC")

    def next_session(self, session: pd.Timestamp) -> pd.Timestamp:
        candidate = _session_date(session) + timedelta(days=1)
        while not self.is_session(pd.Timestamp(candidate)):
            candidate += timedelta(days=1)
        return pd.Timestamp(candidate)

    def sessions_in_range(self, start: pd.Timestamp | str, end: pd.Timestamp | str) -> pd.DatetimeIndex:
        current = _session_date(pd.Timestamp(start))
        through = _session_date(pd.Timestamp(end))
        sessions: list[pd.Timestamp] = []
        while current <= through:
            timestamp = pd.Timestamp(current)
            if self.is_session(timestamp):
                sessions.append(timestamp)
            current += timedelta(days=1)
        return pd.DatetimeIndex(sessions)


def get_exchange_calendar(config: SectorScoutConfig):
    if config.market.calendar == "XNYS" and os.environ.get("SECTORSCOUT_USE_EXCHANGE_CALENDARS") != "1":
        return SimpleXNYSCalendar()
    import exchange_calendars as xcals

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


def market_session_open(session_date: date | str, config: SectorScoutConfig) -> datetime:
    """Return the actual session open in UTC for a NYSE trading session."""
    calendar = get_exchange_calendar(config)
    session = pd.Timestamp(session_date)
    if session.tzinfo is not None:
        session = session.tz_convert("UTC").tz_localize(None)
    else:
        session = session.tz_localize(None)

    if not calendar.is_session(session):
        raise ValueError(f"{session.date()} is not a session for {config.market.calendar}")

    open_ts = calendar.session_open(session)
    if open_ts.tzinfo is None:
        open_ts = open_ts.tz_localize("UTC")
    else:
        open_ts = open_ts.tz_convert("UTC")
    return open_ts.to_pydatetime().astimezone(UTC)


def next_market_session(session_date: date | str, config: SectorScoutConfig) -> date:
    """Return the next exchange session after session_date."""
    calendar = get_exchange_calendar(config)
    session = pd.Timestamp(session_date)
    if session.tzinfo is not None:
        session = session.tz_convert("UTC").tz_localize(None)
    else:
        session = session.tz_localize(None)
    next_session = calendar.next_session(session)
    return next_session.date()


def to_market_time(timestamp_utc: datetime, config: SectorScoutConfig) -> datetime:
    if timestamp_utc.tzinfo is None:
        raise ValueError("timestamp_utc must be timezone-aware")
    return timestamp_utc.astimezone(market_timezone(config))


def _session_date(session: pd.Timestamp | date | str) -> date:
    timestamp = pd.Timestamp(session)
    if timestamp.tzinfo is not None:
        timestamp = timestamp.tz_convert("UTC").tz_localize(None)
    else:
        timestamp = timestamp.tz_localize(None)
    return timestamp.date()


def _observed_date(month: int, day: int, year: int) -> date:
    actual = date(year, month, day)
    if actual.weekday() == 5:
        return actual - timedelta(days=1)
    if actual.weekday() == 6:
        return actual + timedelta(days=1)
    return actual


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    current = date(year, month, 1)
    days_until = (weekday - current.weekday()) % 7
    return current + timedelta(days=days_until + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    current = date(year, month + 1, 1) - timedelta(days=1) if month < 12 else date(year, 12, 31)
    days_back = (current.weekday() - weekday) % 7
    return current - timedelta(days=days_back)


def _easter_date(year: int) -> date:
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _nyse_holidays(year: int) -> set[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    return {
        _observed_date(1, 1, year),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_date(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _observed_date(6, 19, year),
        _observed_date(7, 4, year),
        _nth_weekday(year, 9, 0, 1),
        thanksgiving,
        _observed_date(12, 25, year),
    }


def _nyse_early_closes(year: int) -> set[date]:
    thanksgiving = _nth_weekday(year, 11, 3, 4)
    candidates = {
        thanksgiving + timedelta(days=1),
        date(year, 12, 24),
    }
    july_4 = date(year, 7, 4)
    if july_4.weekday() == 1:
        candidates.add(date(year, 7, 3))
    if july_4.weekday() == 3:
        candidates.add(date(year, 7, 5))
    return {candidate for candidate in candidates if candidate.weekday() < 5}
