---
title: Your API is someone's pipeline
sub_title: war stories from the receiving end
author: Lauri
theme:
  name: catppuccin-mocha
---

Who's on the other end?
===

<!-- incremental_lists: true -->

* the mobile app (the one you designed it for)
* the partner integration someone signed a contract for
* finance, pulling it into a spreadsheet every Monday
* the data team, pulling **everything**, **every night**, **forever**

<!--
speaker_note: |
  Open with a show of hands: who has written a script that pages through someone's API at 3am?
-->

<!-- end_slide -->

Meet Acme Shop
===

<!-- column_layout: [1, 1] -->
<!-- column: 0 -->

A tiny shop that's always busy:

* customers sign up, change plans, move, **get deleted**
* orders: placed → paid → shipped → delivered (sometimes refunded)
* 90 days of history already behind it

<!-- column: 1 -->

```bash +exec
curl -s localhost:8000/admin/stats | jq .truth
```

<!-- reset_layout -->

<!--
speaker_note: |
  F2 to the stage. Show the business log scrolling. This thing never stops.
-->

<!-- end_slide -->

<!-- jump_to_middle -->

Act 1: "here's an endpoint, it returns the customers"
===

<!-- end_slide -->

v1: the snapshot API
===

```bash +exec
curl -s 'localhost:8000/v1/customers?page_size=3' | jq -c '.data[]'
```

<!-- pause -->

What's missing?

<!-- incremental_lists: true -->

* no `updated_at`: you can't ask "what changed?"
* deleted rows simply vanish
* offset paging over a table that's being written to
* so the only possible collector is: **download everything, every time**

<!--
speaker_note: |
  F3, start v1 collector. F2, watch rows scanned/s on the dashboard.
-->

<!-- end_slide -->

The math of a full dump
===

```python +exec
pages = 210          # ~50 customer + ~160 order pages at page_size=100
for error_rate in (0.001, 0.01, 0.02, 0.05):
    ok = (1 - error_rate) ** pages
    print(f"{error_rate:>6.1%} errors per request -> {ok:6.1%} of dumps succeed")
```

<!-- pause -->

Now let's actually do it: `just ctl error_rate=0.02`

<!--
speaker_note: |
  Break it live. Then show --retries 3. Then latency_ms=300 and watch duplicates appear because the dump takes longer while inserts shift the pages.
-->

<!-- end_slide -->

Things to break in v1
===

| knob | war story |
|---|---|
| `error_rate=0.02` | "it works on my laptop" |
| `latency_ms=300` | dumps take longer, pages drift, **duplicate ids** |
| `page_cap=50` | the server silently caps page size, the collector thinks it's done |
| `drift=on` | `plan` → `subscription_tier`, `amount` → `amount_cents` |
| `rate_limit=5` | ops noticed the data team |

<!--
speaker_note: |
  Let the audience pick. Show the warehouse scoreboard after each one.
-->

<!-- end_slide -->

<!-- jump_to_middle -->

Act 2: "just add updated_since"
===

<!-- end_slide -->

v2: incremental
===

```bash +exec
curl -s 'localhost:8000/v2/customers?page_size=2&updated_since=2026-01-01T00:00:00Z' | jq -c '.data[]'
```

<!-- incremental_lists: true -->

* ✅ 100x less load
* ❌ deletes never show up: **ghosts**
* ❌ `updated_at` at second precision + `>`: same-second writes get lost
* ❌ ordered by `updated_at` with offset paging: rows updated mid-sync jump pages
* ❌ only the latest state, so changes in between are gone

<!--
speaker_note: |
  delete_weight=30 and watch ghosts climb. mutation_rate=50 for the boundary bug.
-->

<!-- end_slide -->

<!-- jump_to_middle -->

Interlude: where did customer 1234 go?
===

<!-- end_slide -->

You can't query an absence
===

A deleted row doesn't change. **It stops being there.**
An API that only returns rows that exist can't tell you about the ones that don't.

<!-- pause -->

```bash +exec
curl -s localhost:8000/admin/stats | jq '.truth.customers'
just audit | jq -c '{v1: .v1.deletes_found, v2: .v2.deletes_found, v3: .v3.deletes_found, really_deleted: .v3.deletes_true}'
```

<!--
speaker_note: |
  F2: point at the "deletes found" column. v2 says 0/N. Each of those N is a person whose data is still in our warehouse.
-->

<!-- end_slide -->

Four ways to find a delete
===

| strategy | what you learn | costs | how it fails |
|---|---|---|---|
| **snapshot diff** (v1) | "gone sometime between dump A and B" | a full dump, every time | a truncated dump = **mass false delete** |
| **id reconciliation** (v2 `/ids`) | "gone sometime before this list" | full scan on the server, races | compares against what you *think* you know |
| **soft delete** (v2.1 `include_deleted`) | `deleted_at`, to the second | consumers must opt in and handle it | same high-water-mark bugs, PII stays in the table |
| **tombstone event** (v3) | exactly when, and what it looked like | an index seek | retention: compact too early and it's gone |

<!--
speaker_note: |
  Demo order: (1) just ctl page_cap=50, watch v1 false deletes go to ~4700, then reset; show 04_deletes.sql zombies. (2) Restart v2 with --reconcile-every 3 and watch ghosts go to zero, while rows scanned/s climbs. (3) Restart v2 with --soft-deletes.
-->

<!-- end_slide -->

The day we deleted all our customers
===

```bash +exec
just ctl page_cap=50 > /dev/null
```

<!-- pause -->

* The API team caps page size "to protect the database"
* The collector sees a short page and assumes it's the last one
* The snapshot diff marks 4,700 customers as deleted
* The churn dashboard pages the CEO

<!-- pause -->

```bash +exec
just ctl reset > /dev/null && echo "phew"
```

<!--
speaker_note: |
  War story slot: swap in your own. Point: snapshot-diff delete detection is only as good as your confidence the snapshot is complete, and v1 gives you no way to know (no total, no has_more, no cursor).
-->

<!-- end_slide -->

Deletes are a compliance problem too
===

<!-- incremental_lists: true -->

* "Delete my data" has to reach **every copy**
* If your API can't tell consumers about deletes, the deleted person lives on in every warehouse that pulls from you
* The event log isn't automatically innocent either: it keeps the before-image. You need retention, compaction, crypto-shredding.
* Tombstones turn a legal obligation into a `WHERE op = 'delete'`

<!--
speaker_note: |
  F4, run the last query in 04_deletes.sql: deleted_people_still_present per pipeline.
-->

<!-- end_slide -->

<!-- jump_to_middle -->

Act 3: give them the log
===

<!-- end_slide -->

v3: the change feed
===

```bash +exec
curl -s 'localhost:8000/v3/changes?after=0&limit=2' | jq -c '.events[]'
```

<!-- incremental_lists: true -->

* monotonic `seq` cursor: resume exactly where you left off
* deletes are events (tombstones), with a timestamp and the last known state
* every version, not just the latest
* cheap for the server: an index seek, not a table scan

<!-- end_slide -->

History is a query, not a collector feature
===

```sql
SELECT plan, count(*)
FROM customers_as_of(now() - INTERVAL 30 DAY)
GROUP BY ALL;
```

The collector is 40 lines and has no business logic.
SCD2 lives in `warehouse/models/v3_scd2.sql`, so you can rebuild it, fix it and re-run it.

<!--
speaker_note: |
  F4. Run 03_v3_feed.sql live. Ask the audience for a question to answer with as_of.
-->

<!-- end_slide -->

The scoreboard
===

<!--
speaker_note: |
  F2. Let it sit. Point at rows scanned, ghosts, history captured %, lake size.
-->

|  | server load | correct now | deletes | history | lake size |
|---|---|---|---|---|---|
| v1 snapshot | 🔥🔥🔥 | mostly | inferred, false positives | sampled | 📈📈📈 |
| v2 updated_since | 🔥 | ghosts | never (or full-scan reconcile) | latest only | 📈 |
| v3 change feed | · | yes | exact | all of it | 📈 |

<!-- end_slide -->

If you design APIs
===

<!-- incremental_lists: true -->

1. Someone **will** pull all of it. Design for that, not against it.
2. Give them a cursor: monotonic, opaque, resumable
3. Deletes are data: give them a timestamp and put them in the same stream as everything else
4. Timestamps: precision and semantics are part of the contract
5. Changing a schema is a release: version it, announce it
6. Expose the history you already have. Don't make every consumer rebuild it with a cron job.

<!-- end_slide -->

<!-- jump_to_middle -->

Questions? Requests? Want me to break something?
===
