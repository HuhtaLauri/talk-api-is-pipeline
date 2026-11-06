-- ACT 3: the change feed. History is a query, not a collector feature.

SELECT entity, op, count(*) FROM v3_events GROUP BY ALL ORDER BY ALL;

-- Full life story of one customer
SELECT * FROM customers_scd2 WHERE id = 42 ORDER BY valid_from;

-- Point in time: plan mix 30 days ago vs now
SELECT plan, count(*) FROM customers_as_of(now() - INTERVAL 30 DAY) GROUP BY ALL ORDER BY ALL;
SELECT plan, count(*) FROM customers_as_of(now()) GROUP BY ALL ORDER BY ALL;

-- Questions the other pipelines literally cannot answer:
-- churn by month, including customers who no longer exist
SELECT date_trunc('month', ts) AS month, count(*) AS deleted
FROM v3_events WHERE entity = 'customers' AND op = 'delete' GROUP BY ALL ORDER BY ALL;

-- upgrades vs downgrades over time
WITH t AS (
    SELECT id, valid_from, plan, lag(plan) OVER (PARTITION BY id ORDER BY valid_from) AS prev FROM customers_scd2
)
SELECT date_trunc('week', valid_from) AS week,
       count(*) FILTER (WHERE (prev, plan) IN (('free','pro'),('pro','enterprise'),('free','enterprise'))) AS upgrades,
       count(*) FILTER (WHERE (prev, plan) IN (('pro','free'),('enterprise','pro'),('enterprise','free'))) AS downgrades
FROM t GROUP BY ALL ORDER BY ALL;
