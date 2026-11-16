"""Pandera schema for BTS Lookup tables (`L_AIRCRAFT_TYPE`, `L_AIRPORT_ID`, etc.)."""

from __future__ import annotations

import pandera.pandas as pa

BtsLookupSchema = pa.DataFrameSchema(
    columns={
        "lookup_table": pa.Column(str, nullable=False),
        "code": pa.Column(str, checks=pa.Check(lambda s: s.str.len() > 0), nullable=False),
        "description": pa.Column(str, checks=pa.Check(lambda s: s.str.len() > 0), nullable=False),
        "confidence_tier": pa.Column(str, checks=pa.Check.equal_to("actual"), nullable=False),
    },
    coerce=False,
    strict=False,
)
