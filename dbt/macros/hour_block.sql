{% macro assign_hour_block(dep_hour_expr) %}
    case
        when {{ dep_hour_expr }} >= 5 and {{ dep_hour_expr }} < 8 then 'early'
        when {{ dep_hour_expr }} >= 8 and {{ dep_hour_expr }} < 11 then 'morning'
        when {{ dep_hour_expr }} >= 11 and {{ dep_hour_expr }} < 14 then 'midday'
        when {{ dep_hour_expr }} >= 14 and {{ dep_hour_expr }} < 17 then 'afternoon'
        when {{ dep_hour_expr }} >= 17 and {{ dep_hour_expr }} < 20 then 'evening'
        else 'late'
    end
{% endmacro %}

{% macro hour_block_multiplier(hour_block_expr) %}
    case lower(trim({{ hour_block_expr }}))
        when 'early' then 0.96
        when 'morning' then 1.03
        when 'midday' then 1.00
        when 'afternoon' then 1.02
        when 'evening' then 1.04
        when 'late' then 0.94
        else 1.00
    end
{% endmacro %}
