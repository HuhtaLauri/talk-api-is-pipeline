# Your API is someone's pipeline — demo rig
set dotenv-load
export SHOP_URL := env("SHOP_URL", "http://localhost:8000")

default:
    @just --list

# one-time: deps, seed data, check presentation tools (run with network!)
setup:
    uv sync
    duckdb -c "INSTALL sqlite;"
    @just seed
    @command -v presenterm >/dev/null || echo "!! presenterm missing: cargo install --locked presenterm"
    @echo "ready. run: just talk"

# create data/source.db with ~90 days of backfilled history
seed:
    uv run python -m shop.sim

# the source system (API + simulated business)
server:
    uv run uvicorn shop.server:app --port 8000 --no-access-log --log-level warning

# start a collector: just collect v1|v2|v3 [--flags]
collect version *ARGS:
    #!/usr/bin/env bash
    case {{version}} in
      v1) f=v1_full_dump ;; v2) f=v2_incremental ;; v3) f=v3_feed ;;
      *) echo "v1, v2 or v3"; exit 1 ;;
    esac
    cd collectors && exec uv run python $f.py {{ARGS}}

# live scoreboard
dash:
    cd tools && uv run python dash.py

# chaos knobs: just ctl latency_ms=300 error_rate=0.05 | just ctl reset
ctl *ARGS:
    @uv run python tools/ctl.py {{ARGS}}

# one-shot audit as JSON
audit:
    @uv run python tools/audit.py

# duckdb shell over the lake (regenerates views first)
wh:
    @uv run python tools/wh_views.py >/dev/null
    duckdb -init data/views.sql

# wipe lake + collector state, keep source db
wipe-lake:
    rm -rf data/lake data/views.sql

# wipe EVERYTHING and reseed (stop the server first: just stop)
reset:
    @! curl -sf localhost:8000/admin/chaos >/dev/null || { echo "server is running, 'just stop' first"; exit 1; }
    @just wipe-lake
    rm -f data/source.db data/source.db-wal data/source.db-shm
    @just seed

# slides only. Via uv run so `python` in +exec snippets is the project venv, not pyenv
slides:
    uv run presenterm -x slides/talk.md

# speaker notes (run in a second terminal on the laptop screen)
notes:
    uv run presenterm --listen-speaker-notes slides/talk.md

# tear down the tmux session and anything still holding :8000
stop:
    -tmux kill-session -t apitalk 2>/dev/null
    -pkill -f "[u]vicorn shop.server"

# the whole show in tmux
talk:
    ./tmux/talk.sh
