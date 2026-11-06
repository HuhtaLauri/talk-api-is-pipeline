-- INTERLUDE: where did customer 1234 go?
-- Four ways to find out that something was deleted, from worst to best.

-- 1. v1: diff consecutive snapshots.
--    "Deleted somewhere between these two dumps". You only know the window, not when.
WITH snaps AS (
    SELECT DISTINCT _snapshot_at::TIMESTAMPTZ AS snap_at FROM v1_customers
), pairs AS (
    SELECT snap_at AS cur, lag(snap_at) OVER (ORDER BY snap_at) AS prev FROM snaps
)
SELECT p.prev AS deleted_after, p.cur AS deleted_before, count(*) AS "deleted"
FROM pairs p
JOIN v1_customers a ON a._snapshot_at::TIMESTAMPTZ = p.prev
ANTI JOIN v1_customers b ON b._snapshot_at::TIMESTAMPTZ = p.cur AND b.id = a.id
GROUP BY ALL ORDER BY deleted_before DESC LIMIT 10;

-- ...and its failure mode: rows that were "deleted" and came back.
-- Every one of these was a truncated dump (try `just ctl page_cap=50`).
WITH s AS (
    SELECT DISTINCT _snapshot_at::TIMESTAMPTZ AS snap_at FROM v1_customers
), presence AS (
    SELECT c.id, s.snap_at, bool_or(v.id IS NOT NULL) AS present
    FROM (SELECT DISTINCT id FROM v1_customers) c CROSS JOIN s
    LEFT JOIN v1_customers v ON v.id = c.id AND v._snapshot_at::TIMESTAMPTZ = s.snap_at
    GROUP BY ALL
), flips AS (
    SELECT id, snap_at, present, lag(present) OVER (PARTITION BY id ORDER BY snap_at) AS was FROM presence
)
SELECT snap_at AS resurrected_at, count(*) AS zombies
FROM flips WHERE present AND was = false GROUP BY ALL ORDER BY ALL;

-- 2. v2: there is nothing to query. That's the point.
--    Every id we hold might be a ghost, and we can't tell which.

-- 3. v2 + reconciliation (`just collect v2 --reconcile-every 3`):
--    ids we've seen before the id list was taken, but which aren't on it.
WITH lists AS (SELECT DISTINCT _snapshot_at::TIMESTAMPTZ AS snap_at FROM v2_customers_ids),
latest AS (SELECT max(snap_at) AS snap_at FROM lists),
known AS (SELECT id, min(_extracted_at::TIMESTAMPTZ) AS first_seen FROM v2_customers GROUP BY id)
SELECT count(*) AS inferred_deletes
FROM known, latest
WHERE first_seen <= latest.snap_at
  AND id NOT IN (SELECT id FROM v2_customers_ids WHERE _snapshot_at::TIMESTAMPTZ = latest.snap_at);

-- 3b. v2.1 soft deletes (`just collect v2 --soft-deletes`): an explicit row, second precision.
-- SELECT id, updated_at AS deleted_at FROM v2_customers WHERE deleted ORDER BY updated_at DESC LIMIT 10;

-- 4. v3: tombstones. Exact time, who, and what the row looked like right before.
SELECT seq, ts AS deleted_at, id, data.name, data.plan
FROM v3_events WHERE entity = 'customers' AND op = 'delete'
ORDER BY seq DESC LIMIT 10;

-- The GDPR question: whose personal data is still sitting in our warehouse after they were deleted?
SELECT 'v1 latest snapshot' AS pipeline, count(*) AS deleted_people_still_present
FROM (SELECT * FROM v1_customers WHERE _snapshot_at = (SELECT max(_snapshot_at) FROM v1_customers)) v
SEMI JOIN v3_events e ON e.entity = 'customers' AND e.op = 'delete' AND e.id = v.id
UNION ALL
SELECT 'v2 (no delete detection)', count(DISTINCT id)
FROM v2_customers v
SEMI JOIN v3_events e ON e.entity = 'customers' AND e.op = 'delete' AND e.id = v.id;
