-- Daily sales per store.
-- The pull request "adds when the store joined its region" by joining to store_history. That table has
-- two rows for store 3, so with bug=true every order of store 3 is counted twice. dbt builds the model
-- without complaint and its tests pass.
select
    o.store_id,
    cast(o.order_date as date) as sale_date,
    s.region,
    count(*) as orders,
    round(sum(o.amount), 2) as revenue
    {% if var('bug', false) %}, min(h.valid_from) as region_since{% endif %}
from {{ ref('orders') }} o
join {{ ref('stores') }} s on s.store_id = o.store_id
{% if var('bug', false) %}
join {{ ref('store_history') }} h on h.store_id = o.store_id
{% endif %}
group by 1, 2, 3
