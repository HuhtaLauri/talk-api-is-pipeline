"""Acme Shop API. Three generations of the same data, plus chaos knobs.

  v1  /v1/{entity}?page=&page_size=            offset paging, no timestamps, hard deletes
  v2  /v2/{entity}?updated_since=&page=        "just add updated_since"
      /v2/{entity}?...&include_deleted=true    v2.1: soft deletes, opt-in
      /v2/{entity}/ids                         reconciliation: every live id
  v3  /v3/changes?after=&limit=                ordered change feed with cursor + tombstones

  /admin/chaos   GET / POST {knob: value}      break things live
  /admin/stats   GET                           what each API version costs the server
"""

import asyncio
import json
import random
import threading
import time
from collections import defaultdict
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse

from . import db
from .sim import Sim

CHAOS_DEFAULTS = {
    "latency_ms": 0,        # added to every /v* request
    "error_rate": 0.0,      # probability of a 500
    "rate_limit": 0,        # max requests/sec per API version, 0 = unlimited (429 + Retry-After)
    "page_cap": 0,          # silently cap page_size to this, 0 = off
    "drift": False,         # v1/v2: someone shipped a "harmless refactor"
    "mutation_rate": 3.0,   # business events per second
    "delete_weight": 2.0,   # relative weight of customer deletes in the simulation
    "paused": False,        # freeze the business
    "log_ops": True,        # print every business event
}
CHAOS = dict(CHAOS_DEFAULTS)

STATS = defaultdict(lambda: defaultdict(int))  # version -> counter -> n
_stats_lock = threading.Lock()
_buckets: dict[str, list[float]] = defaultdict(list)


def record(version: str, **counters: int) -> None:
    with _stats_lock:
        for k, v in counters.items():
            STATS[version][k] += v


# -- the business, running in the background ---------------------------------

def run_business(stop: threading.Event) -> None:
    con = db.connect()
    sim = Sim(con, log=lambda m: CHAOS["log_ops"] and print(f"  biz  {m}", flush=True))
    debt = 0.0
    while not stop.is_set():
        time.sleep(0.1)
        if CHAOS["paused"]:
            continue
        debt += CHAOS["mutation_rate"] * 0.1
        n, debt = int(debt), debt - int(debt)
        if n:
            con.execute("BEGIN IMMEDIATE")
            for _ in range(n):
                sim.step(db.now(), CHAOS["delete_weight"])
            con.execute("COMMIT")


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init(db.connect())
    stop = threading.Event()
    t = threading.Thread(target=run_business, args=(stop,), daemon=True)
    t.start()
    yield
    stop.set()


app = FastAPI(title="Acme Shop API", lifespan=lifespan)


@app.middleware("http")
async def chaos_middleware(request: Request, call_next):
    path = request.url.path
    version = path.split("/")[1] if path.count("/") >= 2 else ""
    if version not in ("v1", "v2", "v3"):
        return await call_next(request)

    record(version, requests=1)
    if CHAOS["latency_ms"]:
        await asyncio.sleep(CHAOS["latency_ms"] / 1000)
    if CHAOS["rate_limit"]:
        now, bucket = time.monotonic(), _buckets[version]
        bucket[:] = [t for t in bucket if now - t < 1.0]
        if len(bucket) >= CHAOS["rate_limit"]:
            record(version, throttled=1)
            return JSONResponse({"error": "slow down"}, status_code=429, headers={"Retry-After": "1"})
        bucket.append(now)
    if random.random() < CHAOS["error_rate"]:
        record(version, errors=1)
        return JSONResponse({"error": "upstream connection reset by peer"}, status_code=500)
    return await call_next(request)


def _check(entity: str) -> None:
    if entity not in db.COLUMNS:
        raise HTTPException(404, f"unknown entity {entity}")


def _page_size(requested: int) -> int:
    return min(requested, CHAOS["page_cap"]) if CHAOS["page_cap"] else requested


def _drift(entity: str, row: dict) -> dict:
    """The 'harmless refactor'."""
    if not CHAOS["drift"]:
        return row
    if entity == "customers" and "plan" in row:
        row["subscription_tier"] = row.pop("plan")
    if entity == "orders" and "amount" in row:
        row["amount_cents"] = int(round(row.pop("amount") * 100))  # unit change, same day
    return row


# -- v1: the snapshot API -----------------------------------------------------

V1_ORDER = {"customers": "name", "orders": "id DESC"}  # alphabetical / newest first, as the UI wanted


@app.get("/v1/{entity}")
def v1_list(entity: str, page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=1000)):
    _check(entity)
    size = _page_size(page_size)
    offset = (page - 1) * size
    cols = [c for c in db.COLUMNS[entity] if c not in ("created_at", "updated_at")]
    rows = db.connect().execute(
        f"SELECT {','.join(cols)} FROM {entity} ORDER BY {V1_ORDER[entity]} LIMIT ? OFFSET ?", (size, offset)
    ).fetchall()
    data = [_drift(entity, dict(r)) for r in rows]
    record("v1", rows_served=len(data), rows_scanned=offset + len(data))
    return {"data": data}


# -- v2: "just add updated_since" ---------------------------------------------

@app.get("/v2/{entity}")
def v2_list(
    entity: str,
    updated_since: str = "1970-01-01T00:00:00Z",
    page: int = Query(1, ge=1),
    page_size: int = Query(100, ge=1, le=1000),
    include_deleted: bool = Query(False, description="v2.1: soft deletes show up as {id, updated_at, deleted: true}"),
):
    _check(entity)
    size = _page_size(page_size)
    offset = (page - 1) * size
    source = entity
    if include_deleted:
        cols = db.COLUMNS[entity]
        live = ", ".join(cols)
        dead = ", ".join("id" if c == "id" else "deleted_at" if c == "updated_at" else "NULL" for c in cols)
        source = (f"(SELECT {live}, 0 AS deleted FROM {entity} UNION ALL "
                  f"SELECT {dead}, 1 FROM tombstones WHERE entity = '{entity}')")
    rows = db.connect().execute(
        f"SELECT * FROM {source} WHERE updated_at > ? ORDER BY updated_at LIMIT ? OFFSET ?",
        (updated_since, size + 1, offset),
    ).fetchall()
    more = len(rows) > size
    data = []
    for r in rows[:size]:
        r = dict(r)
        if include_deleted:
            if r["deleted"]:
                r = {"id": r["id"], "updated_at": r["updated_at"], "deleted": True}
            else:
                r["deleted"] = False
        data.append(_drift(entity, r))
    record("v2", rows_served=len(data), rows_scanned=offset + len(data))
    return {"data": data, "next_page": page + 1 if more else None}


@app.get("/v2/{entity}/ids")
def v2_ids(entity: str):
    """The reconciliation endpoint: 'just give us all the ids and we'll figure out deletes'."""
    _check(entity)
    ids = [r[0] for r in db.connect().execute(f"SELECT id FROM {entity} ORDER BY id")]
    record("v2", rows_served=len(ids), rows_scanned=len(ids))
    return {"ids": ids}


# -- v3: the change feed ------------------------------------------------------

@app.get("/v3/changes")
def v3_changes(after: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=5000), entity: str | None = None):
    limit = _page_size(limit)
    sql, args = "SELECT * FROM events WHERE seq > ?", [after]
    if entity:
        _check(entity)
        sql += " AND entity = ?"
        args.append(entity)
    rows = db.connect().execute(sql + " ORDER BY seq LIMIT ?", args + [limit + 1]).fetchall()
    more = len(rows) > limit
    events = [
        {"seq": r["seq"], "ts": r["ts"], "entity": r["entity"], "id": r["entity_id"],
         "op": r["op"], "schema_version": 1, "data": json.loads(r["data"])}
        for r in rows[:limit]
    ]
    record("v3", rows_served=len(events), rows_scanned=len(events))
    return {"events": events, "next_cursor": events[-1]["seq"] if events else after, "has_more": more}


# -- admin --------------------------------------------------------------------

@app.get("/admin/chaos")
def get_chaos():
    return CHAOS


@app.post("/admin/chaos")
def set_chaos(changes: dict):
    if changes.get("reset"):
        CHAOS.clear()
        CHAOS.update(CHAOS_DEFAULTS)
        return CHAOS
    for k, v in changes.items():
        if k not in CHAOS_DEFAULTS:
            raise HTTPException(400, f"unknown knob {k}; try {sorted(CHAOS_DEFAULTS)}")
        if isinstance(CHAOS_DEFAULTS[k], bool) and isinstance(v, str):
            v = v.lower() in ("1", "true", "on", "yes")
        CHAOS[k] = type(CHAOS_DEFAULTS[k])(v)
    print(f"  chaos {changes}", flush=True)
    return CHAOS


@app.get("/admin/stats")
def get_stats():
    con = db.connect()
    truth = {k: con.execute(f"SELECT count(*) FROM {k}").fetchone()[0] for k in db.COLUMNS}
    truth["events"] = con.execute("SELECT coalesce(max(seq),0) FROM events").fetchone()[0]
    with _stats_lock:
        return {"versions": {v: dict(c) for v, c in STATS.items()}, "truth": truth, "chaos": CHAOS}
