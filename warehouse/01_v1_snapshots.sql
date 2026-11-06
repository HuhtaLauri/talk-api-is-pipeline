-- ACT 1: the snapshot API. All we can store is copies of "now".

-- How many copies of the same customers have we paid for?
SELECT count(DISTINCT filename) AS snapshots, count(*) AS rows_stored, count(DISTINCT id) AS distinct_customers
FROM v1_customers;

-- Duplicates INSIDE a single snapshot (offset paging + inserts sorted by name)
SELECT filename, count(*) - count(DISTINCT id) AS duplicate_rows
FROM v1_customers GROUP BY ALL HAVING duplicate_rows > 0 ORDER BY filename;

-- Poor man's history: diff consecutive snapshots.
-- Anything that changed and changed back between two dumps is invisible.
WITH s AS (
    SELECT id, plan, _snapshot_at::TIMESTAMPTZ AS seen_at,
           lag(plan) OVER (PARTITION BY id ORDER BY _snapshot_at) AS prev_plan
    FROM v1_customers
)
SELECT id, prev_plan, plan, seen_at AS changed_somewhere_before
FROM s WHERE plan IS DISTINCT FROM prev_plan AND prev_plan IS NOT NULL
ORDER BY seen_at DESC LIMIT 20;

-- After `ctl drift=on`: what did union_by_name do to us?
-- SELECT plan, subscription_tier, count(*) FROM v1_customers GROUP BY ALL;
