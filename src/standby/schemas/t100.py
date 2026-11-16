"""Pandera schema for standardized BTS T-100 Segment (`FMG`) records."""

from __future__ import annotations

import pandera.pandas as pa

T100SegmentsSchema = pa.DataFrameSchema(
    columns={
        "year": pa.Column("int64", checks=pa.Check.in_range(2018, 2035), nullable=False),
        "quarter": pa.Column("int64", checks=pa.Check.in_range(1, 4), nullable=False),
        "month": pa.Column("int64", checks=pa.Check.in_range(1, 12), nullable=False),
        "op_carrier": pa.Column(str, nullable=False),
        "carrier_name": pa.Column(str, nullable=False),
        "origin": pa.Column(str, checks=pa.Check.str_length(3, 3), nullable=False),
        "origin_airport_id": pa.Column("int64", checks=pa.Check.gt(0), nullable=False),
        "origin_country": pa.Column(str, nullable=False),
        "dest": pa.Column(str, checks=pa.Check.str_length(3, 3), nullable=False),
        "dest_airport_id": pa.Column("int64", checks=pa.Check.gt(0), nullable=False),
        "dest_country": pa.Column(str, nullable=False),
        "bts_aircraft_type": pa.Column(str, nullable=False),
        "aircraft_group": pa.Column("int64", nullable=False),
        "aircraft_config": pa.Column("int64", nullable=False),
        "service_class": pa.Column(str, checks=pa.Check.equal_to("F"), nullable=False),
        "data_source": pa.Column(
            str,
            checks=pa.Check.isin(["DU", "DF", "IU", "IF"]),
            nullable=False,
        ),
        "is_international": pa.Column(bool, nullable=False),
        "departures_scheduled": pa.Column("float64", checks=pa.Check.ge(0.0), nullable=False),
        "departures_performed": pa.Column("float64", checks=pa.Check.ge(0.0), nullable=False),
        "seats": pa.Column("float64", checks=pa.Check.ge(0.0), nullable=False),
        "passengers": pa.Column("float64", checks=pa.Check.ge(0.0), nullable=False),
        "distance_miles": pa.Column("float64", checks=pa.Check.ge(0.0), nullable=False),
        "confidence_tier": pa.Column(str, checks=pa.Check.equal_to("actual"), nullable=False),
    },
    coerce=False,
    strict=False,
)
