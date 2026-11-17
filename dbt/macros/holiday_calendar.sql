{% macro holiday_name_for_date(date_expr) %}
    case
        -- Christmas & New Year peak travel window: Dec 20 through Jan 3
        when (extract(month from {{ date_expr }}) = 12 and extract(day from {{ date_expr }}) >= 20)
          or (extract(month from {{ date_expr }}) = 1 and extract(day from {{ date_expr }}) <= 3)
            then 'Christmas / New Year'
        -- Thanksgiving window: Wednesday before through Monday after (4th Thursday of Nov)
        when {{ date_expr }} between
            date_sub(
                date_add(
                    date(extract(year from {{ date_expr }}), 11, 1),
                    interval mod(5 - extract(dayofweek from date(extract(year from {{ date_expr }}), 11, 1)) + 7, 7) + 21 day
                ),
                interval 1 day
            )
            and
            date_add(
                date_add(
                    date(extract(year from {{ date_expr }}), 11, 1),
                    interval mod(5 - extract(dayofweek from date(extract(year from {{ date_expr }}), 11, 1)) + 7, 7) + 21 day
                ),
                interval 4 day
            )
            then 'Thanksgiving'
        -- Memorial Day window: Friday before through Tuesday after (last Monday of May)
        when {{ date_expr }} between
            date_sub(
                date_sub(
                    date(extract(year from {{ date_expr }}), 5, 31),
                    interval mod(extract(dayofweek from date(extract(year from {{ date_expr }}), 5, 31)) - 2 + 7, 7) day
                ),
                interval 3 day
            )
            and
            date_add(
                date_sub(
                    date(extract(year from {{ date_expr }}), 5, 31),
                    interval mod(extract(dayofweek from date(extract(year from {{ date_expr }}), 5, 31)) - 2 + 7, 7) day
                ),
                interval 1 day
            )
            then 'Memorial Day'
        -- Independence Day window: Jul 1 through Jul 7
        when extract(month from {{ date_expr }}) = 7 and extract(day from {{ date_expr }}) between 1 and 7
            then 'Independence Day'
        -- Labor Day window: Friday before through Tuesday after (1st Monday of Sep)
        when {{ date_expr }} between
            date_sub(
                date_add(
                    date(extract(year from {{ date_expr }}), 9, 1),
                    interval mod(2 - extract(dayofweek from date(extract(year from {{ date_expr }}), 9, 1)) + 7, 7) day
                ),
                interval 3 day
            )
            and
            date_add(
                date_add(
                    date(extract(year from {{ date_expr }}), 9, 1),
                    interval mod(2 - extract(dayofweek from date(extract(year from {{ date_expr }}), 9, 1)) + 7, 7) day
                ),
                interval 1 day
            )
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
