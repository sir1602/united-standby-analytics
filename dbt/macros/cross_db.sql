{#
  Cross-adapter helpers so the same models run on BigQuery (production) and DuckDB (free, local).

  Models stay written in BigQuery SQL. Two mechanisms cover the gaps:

  1. `register_bigquery_compat_shims()` (an `on-run-start` hook) creates DuckDB macros and a
     type that carry BigQuery names, for functions DuckDB lacks entirely: `safe_divide`,
     `logical_or`, `format_date`, `regexp_contains`, `generate_date_array`, `safe.parse_date` and the `float64`
     type. On BigQuery the hook renders nothing and dbt skips it.

  2. Adapter-dispatched macros for constructs that are syntax-level in DuckDB or exist there
     with DIFFERENT semantics. Each `default__` renders exactly the original BigQuery SQL:
       - greatest/least: DuckDB skips NULL arguments, BigQuery returns NULL. Without
         `bq_greatest()`/`bq_least()`, a clip like `least(1.0, greatest(0.0, x))` would turn a
         missing `x` into 0.0 and silently impute values (violates SC-5).
       - extract(dayofweek): DuckDB numbers Sunday as 0, BigQuery as 1.
       - date(y, m, d), date_add/date_sub(d, interval n day), safe_cast(x as t).
#}

{% macro register_bigquery_compat_shims() -%}
    {{ return(adapter.dispatch('register_bigquery_compat_shims')()) }}
{%- endmacro %}

{% macro default__register_bigquery_compat_shims() -%}
{%- endmacro %}

{% macro duckdb__register_bigquery_compat_shims() -%}
create type if not exists float64 as double;
create or replace macro safe_divide(a, b) as case when b = 0 then null else a / b end;
create or replace macro bq_greatest(a, b) as case when a is null or b is null then null else greatest(a, b) end;
create or replace macro bq_least(a, b) as case when a is null or b is null then null else least(a, b) end;
create or replace macro logical_or(x) as bool_or(x);
create or replace macro format_date(fmt, d) as strftime(d, fmt);
create or replace macro regexp_contains(s, pattern) as regexp_matches(s, pattern);
create or replace macro generate_date_array(start_date, end_date, step) as
    cast(generate_series(start_date, end_date, step) as date[]);
create schema if not exists safe;
create or replace macro safe.parse_date(fmt, s) as cast(try_strptime(s, fmt) as date);
{%- endmacro %}


{# NULL-propagating greatest/least: renders the function name to call. #}
{% macro bq_greatest() -%}
    {{ return(adapter.dispatch('bq_greatest')()) }}
{%- endmacro %}
{% macro default__bq_greatest() -%}greatest{%- endmacro %}
{% macro duckdb__bq_greatest() -%}bq_greatest{%- endmacro %}

{% macro bq_least() -%}
    {{ return(adapter.dispatch('bq_least')()) }}
{%- endmacro %}
{% macro default__bq_least() -%}least{%- endmacro %}
{% macro duckdb__bq_least() -%}bq_least{%- endmacro %}


{# BigQuery `extract(dayofweek ...)`: 1 = Sunday .. 7 = Saturday. #}
{% macro bq_dayofweek(date_expr) -%}
    {{ return(adapter.dispatch('bq_dayofweek')(date_expr)) }}
{%- endmacro %}
{% macro default__bq_dayofweek(date_expr) -%}extract(dayofweek from {{ date_expr }}){%- endmacro %}
{% macro duckdb__bq_dayofweek(date_expr) -%}(extract(dow from {{ date_expr }}) + 1){%- endmacro %}


{# DATE from year/month/day expressions. #}
{% macro date_from_parts(year_expr, month_expr, day_expr) -%}
    {{ return(adapter.dispatch('date_from_parts')(year_expr, month_expr, day_expr)) }}
{%- endmacro %}
{% macro default__date_from_parts(year_expr, month_expr, day_expr) -%}date({{ year_expr }}, {{ month_expr }}, {{ day_expr }}){%- endmacro %}
{% macro duckdb__date_from_parts(year_expr, month_expr, day_expr) -%}make_date(cast({{ year_expr }} as integer), {{ month_expr }}, {{ day_expr }}){%- endmacro %}


{# DATE plus/minus a whole number of days (the day count may be an expression). #}
{% macro add_days(date_expr, days_expr) -%}
    {{ return(adapter.dispatch('add_days')(date_expr, days_expr)) }}
{%- endmacro %}
{% macro default__add_days(date_expr, days_expr) -%}date_add({{ date_expr }}, interval {{ days_expr }} day){%- endmacro %}
{% macro duckdb__add_days(date_expr, days_expr) -%}cast({{ date_expr }} + to_days(cast({{ days_expr }} as integer)) as date){%- endmacro %}

{% macro sub_days(date_expr, days_expr) -%}
    {{ return(adapter.dispatch('sub_days')(date_expr, days_expr)) }}
{%- endmacro %}
{% macro default__sub_days(date_expr, days_expr) -%}date_sub({{ date_expr }}, interval {{ days_expr }} day){%- endmacro %}
{% macro duckdb__sub_days(date_expr, days_expr) -%}cast({{ date_expr }} - to_days(cast({{ days_expr }} as integer)) as date){%- endmacro %}


{# Cast that returns NULL instead of erroring. #}
{% macro safe_cast_as(expr, type_name) -%}
    {{ return(adapter.dispatch('safe_cast_as')(expr, type_name)) }}
{%- endmacro %}
{% macro default__safe_cast_as(expr, type_name) -%}safe_cast({{ expr }} as {{ type_name }}){%- endmacro %}
{% macro duckdb__safe_cast_as(expr, type_name) -%}try_cast({{ expr }} as {{ type_name }}){%- endmacro %}
