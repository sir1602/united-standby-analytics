"""Pandera schemas for FAA Aircraft Registry (`MASTER.txt`, `DEREG.txt`, and `ACFTREF.txt`)."""

from __future__ import annotations

import pandera.pandas as pa

FaaMasterDeregSchema = pa.DataFrameSchema(
    columns={
        "n_number": pa.Column(str, checks=pa.Check(lambda s: s.str.len() > 0), nullable=False),
        "serial_number": pa.Column(str, nullable=True),
        "mfr_mdl_code": pa.Column(str, checks=pa.Check(lambda s: s.str.len() > 0), nullable=False),
        "year_mfr": pa.Column(str, nullable=True),
        "registry_source": pa.Column(
            str,
            checks=pa.Check.isin(["faa_master", "faa_dereg"]),
            nullable=False,
        ),
        "cancel_date": pa.Column(str, nullable=True),
    },
    coerce=False,
    strict=False,
)

FaaAcftrefSchema = pa.DataFrameSchema(
    columns={
        "code": pa.Column(
            str,
            checks=pa.Check(lambda s: s.str.len() > 0),
            unique=True,
            nullable=False,
        ),
        "faa_manufacturer": pa.Column(str, nullable=False),
        "faa_model": pa.Column(str, nullable=False),
        "faa_tc_max_seats": pa.Column("int64", checks=pa.Check.ge(0), nullable=False),
        "is_commercial_airliner": pa.Column(bool, nullable=False),
    },
    coerce=False,
    strict=False,
)
