-- ACT 2: updated_since. Efficient! Correct? Let's see.

-- Current state the "obvious" way: latest row per id
CREATE OR REPLACE VIEW v2_customers_current AS
SELECT * FROM v2_customers QUALIFY row_number() OVER (PARTITION BY id ORDER BY updated_at DESC, _extracted_at DESC) = 1;

SELECT count(*) FROM v2_customers_current;

-- Ghosts: customers we still have that the source deleted.
-- (v2 has no way to tell us. We cheat and ask the source / the v3 feed.)
SELECT count(*) AS ghosts FROM v2_customers_current c
WHERE c.id NOT IN (SELECT id FROM customers_scd2 WHERE is_current);

-- History we captured vs history that happened, for one busy customer
WITH busiest AS (SELECT id FROM customers_scd2 GROUP BY id ORDER BY count(*) DESC LIMIT 1)
SELECT 'v2' AS src, count(*) AS versions FROM v2_customers WHERE id = (FROM busiest)
UNION ALL
SELECT 'truth (v3)', count(*) FROM customers_scd2 WHERE id = (FROM busiest);

-- Same-second updates: rows sharing updated_at with the high-water mark are at risk
SELECT updated_at, count(*) FROM v2_customers GROUP BY ALL HAVING count(*) > 1 ORDER BY updated_at DESC LIMIT 10;
