{% test accepted_range(model, column_name, min_value=none, max_value=none, inclusive=true) %}
select *
from {{ model }}
where {{ column_name }} is not null
  and (
    {% if min_value is not none %}
      {% if inclusive %}
        {{ column_name }} < {{ min_value }}
      {% else %}
        {{ column_name }} <= {{ min_value }}
      {% endif %}
    {% else %}
      false
    {% endif %}
    or
    {% if max_value is not none %}
      {% if inclusive %}
        {{ column_name }} > {{ max_value }}
      {% else %}
        {{ column_name }} >= {{ max_value }}
      {% endif %}
    {% else %}
      false
    {% endif %}
  )
{% endtest %}
