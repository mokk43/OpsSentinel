"""Business-hours window checks (Asia/Shanghai by default)."""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo


def now_in_tz(tz_name: str) -> datetime:
    return datetime.now(ZoneInfo(tz_name))


def is_within_business_window(
    when: datetime,
    *,
    tz_name: str = "Asia/Shanghai",
    start_hour: int = 9,
    start_minute: int = 30,
    end_hour: int = 18,
    end_minute: int = 10,
) -> bool:
    """
    True if `when` is Mon–Fri and local time is in [start, end] inclusive of end
    as a last allowed start time (second-precision: time <= end).
    """
    tz = ZoneInfo(tz_name)
    if when.tzinfo is None:
        local = when.replace(tzinfo=tz)
    else:
        local = when.astimezone(tz)

    # Monday=0 … Sunday=6
    if local.weekday() > 4:
        return False

    start = time(start_hour, start_minute)
    end = time(end_hour, end_minute)
    current = local.timetz().replace(tzinfo=None)
    # Compare as time-of-day without tz
    current_tod = time(local.hour, local.minute, local.second, local.microsecond)
    return start <= current_tod <= end


def window_skip_reason(
    when: datetime,
    *,
    tz_name: str = "Asia/Shanghai",
    start_hour: int = 9,
    start_minute: int = 30,
    end_hour: int = 18,
    end_minute: int = 10,
) -> str | None:
    """Return a human reason if outside the window, else None."""
    tz = ZoneInfo(tz_name)
    if when.tzinfo is None:
        local = when.replace(tzinfo=tz)
    else:
        local = when.astimezone(tz)

    if local.weekday() > 4:
        return f"outside window: weekend ({local.strftime('%A')}) tz={tz_name}"

    if is_within_business_window(
        local,
        tz_name=tz_name,
        start_hour=start_hour,
        start_minute=start_minute,
        end_hour=end_hour,
        end_minute=end_minute,
    ):
        return None

    return (
        f"outside window: {local.strftime('%Y-%m-%d %H:%M:%S %Z')} "
        f"not in Mon–Fri "
        f"{start_hour:02d}:{start_minute:02d}–{end_hour:02d}:{end_minute:02d} {tz_name}"
    )
