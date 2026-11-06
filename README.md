# Your API is someone's pipeline

A war-stories talk, with a live rig you can break on stage. Everything runs locally:
tmux, nvim, presenterm, DuckDB, and a small FastAPI service on `localhost:8000`.

## Quick start

```bash
just setup          # once, with network: uv sync, duckdb sqlite ext, seed data
just talk           # builds the tmux stage and attaches
NO_ATTACH=1 just talk   # pre-build it before walking on stage
just notes          # speaker notes, in a second terminal on the laptop screen
```

Needs `just`, `uv`, `duckdb`, `tmux` ≥ 3.4, `jq`, `curl`, and `presenterm` (`cargo install --locked presenterm`).

## The stage (tmux session `apitalk`)

| key | window | what |
|---|---|---|
| F1 | slides | presenterm, `-x` so `+exec` snippets hit the live API |
| F2 | stage | server + business log · `just ctl` prompt · live dashboard |
| F3 | pipes | v1 / v2 / v3 collectors, typed but **not started**: hit Enter when the story gets there |
| F4 | lake | duckdb over the lake · nvim on `warehouse/` (`<leader>r` sends the SQL paragraph to duckdb) |
| F5 | | zoom the current pane |

## The cast

**Source system** (`src/shop`): SQLite plus a background "business" that creates, updates and
deletes customers and orders. Every write also goes to an `events` outbox in the same transaction.
`just seed` backfills about 90 days of history.

| API | shape | the collector it forces on you |
|---|---|---|
| `/v1/{customers,orders}` | offset paging, no timestamps, hard deletes, sorted by name / newest first | `v1_full_dump.py`: download everything every 15s |
| `/v2/{entity}?updated_since=` | second-precision `updated_at`, `>` comparison, offset paging ordered by `updated_at` | `v2_incremental.py`: high-water mark |
| `/v2/{entity}?...&include_deleted=true` | v2.1: soft-delete rows `{id, updated_at, deleted: true}` | `--soft-deletes` |
| `/v2/{entity}/ids` | every live id, unpaged (the reconciliation endpoint) | `--reconcile-every N` |
| `/v3/changes?after=` | ordered event log, `seq` cursor, tombstones | `v3_feed.py`: append-only, no logic |

**The lake** (`data/lake/`) holds JSONL files, one file per collector run, so you can `ls`, `wc -l` and `jq` them live.

**The warehouse** (`warehouse/`): DuckDB views over the lake, plus:
- `models/v3_scd2.sql`: SCD2 + `customers_as_of(ts)` built purely from the feed
- `01_` / `02_` / `03_*.sql`: the walkthrough queries for each act
- `04_deletes.sql`: the four delete-detection strategies side by side, zombies from truncated dumps, and the GDPR query

**The dashboard** (`just dash`) shows rows scanned per API version (offset paging costs `offset + limit`)
and audits each pipeline's idea of "current customers" against the source:
missing, ghosts, stale, duplicates, **deletes found / really deleted**, **false deletes**,
% of history captured, lake size.

## Breaking things

```bash
just ctl                                  # show knobs
just ctl error_rate=0.02                  # v1 dumps never finish (0.98^210 ≈ 1%)
just ctl latency_ms=300                   # slower dumps → page drift → duplicate ids
just ctl page_cap=50                      # silent truncation: v1 thinks page 1 was the last
just ctl drift=on                         # plan→subscription_tier, amount→amount_cents
just ctl rate_limit=5                     # 429s; see who honours Retry-After
just ctl delete_weight=40                 # churn spike → v2 ghosts climb
just ctl mutation_rate=50                 # same-second writes → v2 boundary losses
just ctl paused=true                      # freeze the world to explain something
just ctl reset
```

Collector flags let you "fix" them live too, e.g. `just collect v1 --retries 3`, `--page-size 1000`, `--interval 2`.

### Delete detection, step by step

```bash
just ctl delete_weight=40          # churn spike: v2 "deletes found" stays at 0/N, ghosts climb
just ctl page_cap=50               # truncated v1 dump → thousands of false deletes → just ctl reset → zombies
just collect v2 --reconcile-every 3   # ghosts → 0, but watch v2 rows scanned/s
just collect v2 --soft-deletes        # v2.1: explicit tombstone rows
```

The v2 state file keeps its high-water mark, so `--soft-deletes` only catches deletes from now on.
To show it catching everything, `rm -r data/lake/v2` first.

Recovery: `just wipe-lake` drops collected data (stop collectors first, or the cursor and the lake drift apart).
`just stop` kills the session and server; `just reset` (refuses while the server runs) reseeds everything.
Ctrl-C a collector, restart it, and v3 resumes from `data/lake/v3/_cursor`.
