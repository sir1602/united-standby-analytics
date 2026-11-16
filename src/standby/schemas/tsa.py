"""Pandera schema for TSA Daily Checkpoint Throughput."""

from __future__ import annotations

import pandera.pandas as pa

TsaThroughputSchema = pa.DataFrameSchema(
    columns={
        "date_day": pa.Column(
            str,
            checks=pa.Check.str_matches(r"^\d{4}-\d{2}-\d{2}$"),
            unique=True,
            nullable=False,
        ),
        "tsa_throughput": pa.Column("int64", checks=pa.Check.gt(0), nullable=False),
        "source_page_year": pa.Column(str, nullable=False),
        "source_url": pa.Column(str, nullable=False),
        "confidence_tier": pa.Column(str, checks=pa.Check.equal_to("actual"), nullable=False),
    },
    coerce=False,
    strict=False,
)
