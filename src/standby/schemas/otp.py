"""Pandera schema for standardized BTS Marketing Carrier On-Time Performance (`OTP`)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pandas as pd
import pandera.pandas as pa

if TYPE_CHECKING:
    from standby.config import StandbyConfig

VALID_HOUR_BLOCKS = ("early", "morning", "midday", "afternoon", "evening", "late")
VALID_CANCELLATION_CODES = ("A", "B", "C", "D")
HHMM_REGEX = r"^([01]\d|2[0-3])[0-5]\d$"


def _check_non_null_when_not_cancelled(df: pd.DataFrame) -> bool:
    """Enforce that `tail_num`, `tail_num_clean`, `dep_time`, and `dep_delay_min` are non-null when `is_cancelled == False`."""
    performed = df[~df["is_cancelled"]]
    if performed.empty:
        return True
    for col in ("tail_num", "tail_num_clean", "dep_time", "dep_delay_min"):
        if performed[col].isna().any() or (performed[col].astype(str).str.strip() == "").any():
            return False
    return True


def _check_non_null_when_completed(df: pd.DataFrame) -> bool:
    """Enforce that `arr_time`, `arr_delay_min`, and `actual_elapsed_time_min` are non-null when `is_cancelled == False` and `is_diverted == False`."""
    completed = df[(~df["is_cancelled"]) & (~df["is_diverted"])]
    if completed.empty:
        return True
    for col in ("arr_time", "arr_delay_min", "actual_elapsed_time_min"):
        if completed[col].isna().any():
            return False
    return True


def _check_cancellation_code_consistency(df: pd.DataFrame) -> bool:
    """Enforce that `cancellation_code` is in `{'A', 'B', 'C', 'D'}` iff `is_cancelled == True`."""
    cancelled = df[df["is_cancelled"]]
    performed = df[~df["is_cancelled"]]
    if not cancelled.empty:
        codes = cancelled["cancellation_code"]
        if codes.isna().any():
            return False
        if (codes.astype(str).str.strip() == "").any():
            return False
        if (~codes.isin(VALID_CANCELLATION_CODES)).any():
            return False
    if not performed.empty and performed["cancellation_code"].notna().any():
        return False
    return True


def build_otp_schema(config: StandbyConfig | None = None) -> pa.DataFrameSchema:
    """Build the Pandera schema for standardized OTP flights, honoring `config` when provided."""
    year_check = (
        pa.Check.isin(config.years)
        if config is not None and config.years is not None
        else pa.Check.in_range(2018, 2035)
    )
    mkt_carrier_check = (
        pa.Check.isin(config.marketing_carriers)
        if config is not None and config.marketing_carriers
        else pa.Check(lambda s: s.str.strip().eq(s) & (s.str.len() >= 2))
    )
    op_carrier_checks = [pa.Check(lambda s: s.str.strip().eq(s) & (s.str.len() >= 2))]
    if config is not None and config.all_operating_carriers:
        op_carrier_checks.append(pa.Check.isin(sorted(config.all_operating_carriers)))
    origin_check = (
        pa.Check.equal_to(config.active_hub) if config is not None else pa.Check.str_length(3, 3)
    )
    hour_block_names = (
        list(config.hour_blocks.keys())
        if config is not None and config.hour_blocks
        else list(VALID_HOUR_BLOCKS)
    )

    return pa.DataFrameSchema(
        columns={
            "flight_date": pa.Column(
                str,
                checks=pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"),
                nullable=False,
            ),
            "year": pa.Column("int64", checks=year_check, nullable=False),
            "quarter": pa.Column("int64", checks=pa.Check.in_range(1, 4), nullable=False),
            "month": pa.Column("int64", checks=pa.Check.in_range(1, 12), nullable=False),
            "day_of_month": pa.Column("int64", checks=pa.Check.in_range(1, 31), nullable=False),
            "day_of_week": pa.Column("int64", checks=pa.Check.in_range(1, 7), nullable=False),
            "mkt_carrier": pa.Column(str, checks=mkt_carrier_check, nullable=False),
            "op_carrier": pa.Column(str, checks=op_carrier_checks, nullable=False),
            "flight_num_mkt": pa.Column(str, nullable=False),
            "flight_num_op": pa.Column(str, nullable=True),
            "tail_num": pa.Column(str, nullable=True),
            "tail_num_clean": pa.Column(str, nullable=True),
            "origin": pa.Column(str, checks=origin_check, nullable=False),
            "origin_airport_id": pa.Column("int64", checks=pa.Check.gt(0), nullable=False),
            "dest": pa.Column(str, checks=pa.Check.str_length(3, 3), nullable=False),
            "dest_airport_id": pa.Column("int64", checks=pa.Check.gt(0), nullable=False),
            "crs_dep_time": pa.Column(
                str,
                checks=[
                    pa.Check.str_matches(HHMM_REGEX),
                    pa.Check(
                        lambda s: ~s.eq("2400"),
                        error="crs_dep_time must normalize '2400' to '0000'",
                    ),
                ],
                nullable=False,
            ),
            "dep_time": pa.Column(
                str,
                checks=[
                    pa.Check.str_matches(HHMM_REGEX),
                    pa.Check(
                        lambda s: ~s.eq("2400"),
                        error="dep_time must normalize '2400' to '0000'",
                    ),
                ],
                nullable=True,
            ),
            "crs_arr_time": pa.Column(
                str,
                checks=[
                    pa.Check.str_matches(HHMM_REGEX),
                    pa.Check(
                        lambda s: ~s.eq("2400"),
                        error="crs_arr_time must normalize '2400' to '0000'",
                    ),
                ],
                nullable=False,
            ),
            "arr_time": pa.Column(
                str,
                checks=[
                    pa.Check.str_matches(HHMM_REGEX),
                    pa.Check(
                        lambda s: ~s.eq("2400"),
                        error="arr_time must normalize '2400' to '0000'",
                    ),
                ],
                nullable=True,
            ),
            "dep_hour": pa.Column("int64", checks=pa.Check.in_range(0, 23), nullable=False),
            "hour_block": pa.Column(str, checks=pa.Check.isin(hour_block_names), nullable=False),
            "dep_delay_min": pa.Column("float64", nullable=True),
            "arr_delay_min": pa.Column("float64", nullable=True),
            "is_cancelled": pa.Column(bool, nullable=False),
            "cancellation_code": pa.Column(
                str,
                checks=pa.Check.isin(VALID_CANCELLATION_CODES),
                nullable=True,
            ),
            "is_diverted": pa.Column(bool, nullable=False),
            "crs_elapsed_time_min": pa.Column("float64", nullable=True),
            "actual_elapsed_time_min": pa.Column("float64", nullable=True),
            "distance_miles": pa.Column("float64", checks=pa.Check.gt(0), nullable=False),
            "confidence_tier": pa.Column(str, checks=pa.Check.equal_to("actual"), nullable=False),
        },
        checks=[
            pa.Check(
                _check_non_null_when_not_cancelled,
                name="non_null_when_not_cancelled",
                error="tail_num, tail_num_clean, dep_time, and dep_delay_min must be non-null when is_cancelled == False",
            ),
            pa.Check(
                _check_non_null_when_completed,
                name="non_null_when_completed",
                error="arr_time, arr_delay_min, and actual_elapsed_time_min must be non-null when not cancelled and not diverted",
            ),
            pa.Check(
                _check_cancellation_code_consistency,
                name="cancellation_code_consistency",
                error="cancellation_code must be one of {'A', 'B', 'C', 'D'} iff is_cancelled == True",
            ),
        ],
        coerce=False,
        strict=False,
    )


OtpMarketingFlightsSchema = build_otp_schema()
