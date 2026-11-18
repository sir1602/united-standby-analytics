{% macro holiday_name_for_date(date_expr) %}
    {#- Anchor dates; date arithmetic goes through cross-adapter helpers (macros/cross_db.sql). -#}
    {%- set year_expr = 'extract(year from ' ~ date_expr ~ ')' -%}
    {%- set nov_1 = date_from_parts(year_expr, 11, 1) -%}
    {%- set may_31 = date_from_parts(year_expr, 5, 31) -%}
    {%- set sep_1 = date_from_parts(year_expr, 9, 1) -%}
    {#- Thanksgiving: 4th Thursday of November -#}
    {%- set thanksgiving = add_days(nov_1, 'mod(5 - ' ~ bq_dayofweek(nov_1) ~ ' + 7, 7) + 21') -%}
    {#- Memorial Day: last Monday of May -#}
    {%- set memorial_day = sub_days(may_31, 'mod(' ~ bq_dayofweek(may_31) ~ ' - 2 + 7, 7)') -%}
    {#- Labor Day: 1st Monday of September -#}
    {%- set labor_day = add_days(sep_1, 'mod(2 - ' ~ bq_dayofweek(sep_1) ~ ' + 7, 7)') %}
    case
        -- Christmas & New Year peak travel window: Dec 20 through Jan 3
        when (extract(month from {{ date_expr }}) = 12 and extract(day from {{ date_expr }}) >= 20)
          or (extract(month from {{ date_expr }}) = 1 and extract(day from {{ date_expr }}) <= 3)
            then 'Christmas / New Year'
        -- Thanksgiving window: Wednesday before through Monday after (4th Thursday of Nov)
        when {{ date_expr }} between {{ sub_days(thanksgiving, 1) }} and {{ add_days(thanksgiving, 4) }}
            then 'Thanksgiving'
        -- Memorial Day window: Friday before through Tuesday after (last Monday of May)
        when {{ date_expr }} between {{ sub_days(memorial_day, 3) }} and {{ add_days(memorial_day, 1) }}
            then 'Memorial Day'
        -- Independence Day window: Jul 1 through Jul 7
        when extract(month from {{ date_expr }}) = 7 and extract(day from {{ date_expr }}) between 1 and 7
            then 'Independence Day'
        -- Labor Day window: Friday before through Tuesday after (1st Monday of Sep)
        when {{ date_expr }} between {{ sub_days(labor_day, 3) }} and {{ add_days(labor_day, 1) }}
            then 'Labor Day'
        -- Spring Break travel window: Mar 10 through Apr 7
        when (extract(month from {{ date_expr }}) = 3 and extract(day from {{ date_expr }}) >= 10)
          or (extract(month from {{ date_expr }}) = 4 and extract(day from {{ date_expr }}) <= 7)
            then 'Spring Break'
        else cast(null as string)
    end
{% endmacro %}

{% macro is_holiday_window(date_expr) %}
    ({{ holiday_name_for_date(date_expr) }} is not null)
{% endmacro %}
