from datetime import date
from zoneinfo import ZoneInfo

import pytest

from sectorscout.config import SectorScoutConfig
from sectorscout.market_calendar import asof_market_close, to_market_time


def test_regular_session_close_uses_utc_and_market_timezone() -> None:
    config = SectorScoutConfig()
    close_utc = asof_market_close(date(2024, 11, 27), config)
    assert close_utc.tzinfo == ZoneInfo("UTC")
    assert close_utc.hour == 21
    assert close_utc.minute == 0
    close_local = to_market_time(close_utc, config)
    assert close_local.tzinfo == ZoneInfo("America/New_York")
    assert close_local.hour == 16
    assert close_local.minute == 0


def test_half_day_session_uses_actual_early_close() -> None:
    config = SectorScoutConfig()
    close_utc = asof_market_close(date(2024, 11, 29), config)
    close_local = to_market_time(close_utc, config)
    assert close_utc.hour == 18
    assert close_utc.minute == 0
    assert close_local.hour == 13
    assert close_local.minute == 0


def test_non_session_raises() -> None:
    config = SectorScoutConfig()
    with pytest.raises(ValueError):
        asof_market_close(date(2024, 11, 30), config)

