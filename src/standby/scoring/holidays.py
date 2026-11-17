"""Deterministic 6-window US holiday calendar matching `dbt/macros/holiday_calendar.sql` 1:1."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

import pandas as pd


def _coerce_date(value: Any) -> date | None:
    """Coerce a date-like value (`date`, `datetime`, `pd.Timestamp`, ISO `str`) to `datetime.date`."""
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        if pd.isna(value):
            return None
        return value.date()
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if pd.isna(value):
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if len(text) == 10:
            return date.fromisoformat(text)
        return datetime.fromisoformat(text).date()
    raise ValueError(f"Cannot coerce value of type {type(value).__name__} to date: {value!r}")


def thanksgiving_date(year: int) -> date:
    """Return US Thanksgiving Day (4th Thursday of November) for `year`."""
    nov1 = date(int(year), 11, 1)
    offset = (3 - nov1.weekday()) % 7 + 21
    return nov1 + timedelta(days=offset)


def memorial_day_date(year: int) -> date:
    """Return US Memorial Day (last Monday of May) for `year`."""
    may31 = date(int(year), 5, 31)
    return may31 - timedelta(days=may31.weekday())


def labor_day_date(year: int) -> date:
    """Return US Labor Day (1st Monday of September) for `year`."""
    sep1 = date(int(year), 9, 1)
    offset = (0 - sep1.weekday()) % 7
    return sep1 + timedelta(days=offset)


def holiday_name(value: Any) -> str | None:
    """
    Return the deterministic US holiday travel window name for `value`, or `None`.

    Matches `dbt/macros/holiday_calendar.sql` date-for-date across all 6 windows:
      1. `Christmas / New Year`: Dec 20 through Jan 3
      2. `Thanksgiving`: Wed before through Mon after (4th Thu of Nov - 1d .. + 4d)
      3. `Memorial Day`: Fri before through Tue after (last Mon of May - 3d .. + 1d)
      4. `Independence Day`: Jul 1 through Jul 7
      5. `Labor Day`: Fri before through Tue after (1st Mon of Sep - 3d .. + 1d)
      6. `Spring Break`: Mar 10 through Apr 7
    """
    d = _coerce_date(value)
    if d is None:
        return None

    m, day, y = d.month, d.day, d.year
    if (m == 12 and day >= 20) or (m == 1 and day <= 3):
        return "Christmas / New Year"

    tg = thanksgiving_date(y)
    if (tg - timedelta(days=1)) <= d <= (tg + timedelta(days=4)):
        return "Thanksgiving"

    mem = memorial_day_date(y)
    if (mem - timedelta(days=3)) <= d <= (mem + timedelta(days=1)):
        return "Memorial Day"

    if m == 7 and 1 <= day <= 7:
        return "Independence Day"

    lab = labor_day_date(y)
    if (lab - timedelta(days=3)) <= d <= (lab + timedelta(days=1)):
        return "Labor Day"

    if (m == 3 and day >= 10) or (m == 4 and day <= 7):
        return "Spring Break"

    return None


def is_holiday_window(value: Any) -> bool:
    """Return `True` iff `value` falls inside one of the 6 US holiday travel windows."""
    return holiday_name(value) is not None


def holiday_name_series(dates: pd.Series) -> pd.Series:
    """Vectorized holiday window name mapping over a date-like `pd.Series` (`object` dtype)."""
    if dates.empty:
        return pd.Series([], index=dates.index, dtype=object)

    if pd.api.types.is_datetime64_any_dtype(dates):
        keys = dates.dt.strftime("%Y-%m-%d").where(dates.notna(), None)
    else:
        keys = dates

    unique_vals = keys.dropna().unique().tolist()
    cache = {val: holiday_name(val) for val in unique_vals}
    mapped = [cache.get(v, None) if v is not None and not pd.isna(v) else None for v in keys]
    return pd.Series(mapped, index=dates.index, dtype=object)


def is_holiday_window_series(dates: pd.Series) -> pd.Series:
    """Vectorized boolean holiday window indicator over a date-like `pd.Series`."""
    names = holiday_name_series(dates)
    return names.notna().astype(bool)
