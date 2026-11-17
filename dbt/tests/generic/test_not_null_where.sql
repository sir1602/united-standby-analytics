{% test not_null_where(model, column_name, where_clause) %}
select *
from {{ model }}
where ({{ where_clause }})
  and {{ column_name }} is null
{% endtest %}
