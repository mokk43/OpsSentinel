from datetime import datetime
from zoneinfo import ZoneInfo

from monitor.schedule_window import is_within_business_window, window_skip_reason

TZ = "Asia/Shanghai"


def _dt(y, m, d, hh, mm, ss=0) -> datetime:
    return datetime(y, m, d, hh, mm, ss, tzinfo=ZoneInfo(TZ))


def test_weekday_inside_window():
    # Wednesday 2026-08-12 10:00
    when = _dt(2026, 8, 12, 10, 0)
    assert is_within_business_window(when, tz_name=TZ) is True
    assert window_skip_reason(when, tz_name=TZ) is None


def test_before_start():
    when = _dt(2026, 8, 12, 9, 29, 59)
    assert is_within_business_window(when, tz_name=TZ) is False
    assert window_skip_reason(when, tz_name=TZ) is not None


def test_at_start():
    when = _dt(2026, 8, 12, 9, 30, 0)
    assert is_within_business_window(when, tz_name=TZ) is True


def test_at_end_inclusive():
    when = _dt(2026, 8, 12, 18, 10, 0)
    assert is_within_business_window(when, tz_name=TZ) is True


def test_after_end():
    when = _dt(2026, 8, 12, 18, 10, 1)
    assert is_within_business_window(when, tz_name=TZ) is False


def test_weekend():
    # Saturday 2026-08-15
    when = _dt(2026, 8, 15, 12, 0)
    assert is_within_business_window(when, tz_name=TZ) is False
    reason = window_skip_reason(when, tz_name=TZ)
    assert reason is not None
    assert "weekend" in reason
