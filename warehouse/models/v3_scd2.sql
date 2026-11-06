-- SCD type 2, built downstream from the change feed.
-- The collector did nothing clever. All the history logic lives here, in SQL,
-- where it can be rebuilt, fixed and re-run at any time from the raw events.

CREATE OR REPLACE VIEW v3_events AS
SELECT DISTINCT ON (seq)                      -- at-least-once delivery: dedupe on seq
    seq, ts::TIMESTAMPTZ AS ts, entity, id, op, data
FROM v3_changes
ORDER BY seq;

CREATE OR REPLACE VIEW customers_scd2 AS
WITH versions AS (
    SELECT
        id,
        data.name, data.email, data.plan, data.country, data.mrr,
        op,
        ts                                              AS valid_from,
        lead(ts) OVER (PARTITION BY id ORDER BY seq)    AS valid_to
    FROM v3_events
    WHERE entity = 'customers'
)
SELECT *, valid_to IS NULL AS is_current
FROM versions
WHERE op <> 'delete';                                   -- a delete just closes the previous version

-- "What did the world look like at time T?"
CREATE OR REPLACE MACRO customers_as_of(t) AS TABLE
    SELECT * FROM customers_scd2
    WHERE valid_from <= t::TIMESTAMPTZ AND (valid_to IS NULL OR valid_to > t::TIMESTAMPTZ);
