"""v2 collector: high-water mark on updated_at.

Looks efficient. Is efficient. Is also wrong in at least three ways.
"""

import argparse
import json
import time

import httpx

from common import LAKE, get, log, now_iso, run_forever, stamp, write_jsonl

p = argparse.ArgumentParser()
p.add_argument("--interval", type=float, default=5)
p.add_argument("--page-size", type=int, default=100)
p.add_argument("--retries", type=int, default=3)
p.add_argument("--entities", default="customers,orders")
p.add_argument("--soft-deletes", action="store_true", help="v2.1: ask for tombstone rows (include_deleted=true)")
p.add_argument("--reconcile-every", type=int, default=0, metavar="N",
               help="every N runs, land the full id list so deletes can be inferred downstream")
p.add_argument("--once", action="store_true")
args = p.parse_args()

STATE = LAKE / "v2" / "_state.json"


def load_state() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


RUNS = 0


def reconcile(client: httpx.Client, entity: str) -> None:
    t0, snapshot_at = time.time(), now_iso()
    ids = get(client, f"/v2/{entity}/ids", {}, args.retries, "v2")["ids"]
    write_jsonl(LAKE / "v2" / f"{entity}_ids" / f"ids_{stamp()}.jsonl",
                [{"id": i, "_snapshot_at": snapshot_at} for i in ids])
    log("v2", f"{entity}: reconciled {len(ids)} live ids ({time.time() - t0:.1f}s)")


def sync() -> None:
    global RUNS
    RUNS += 1
    state = load_state()
    with httpx.Client() as client:
        for entity in args.entities.split(","):
            since = state.get(entity, "1970-01-01T00:00:00Z")
            t0, rows, page = time.time(), [], 1
            while page:
                params = {"updated_since": since, "page": page, "page_size": args.page_size}
                if args.soft_deletes:
                    params["include_deleted"] = "true"
                resp = get(client, f"/v2/{entity}", params, args.retries, "v2")
                rows += resp["data"]
                page = resp["next_page"]
            if args.reconcile_every and RUNS % args.reconcile_every == 0:
                reconcile(client, entity)
            if not rows:
                continue
            extracted = now_iso()
            for r in rows:
                r["_extracted_at"] = extracted
            write_jsonl(LAKE / "v2" / entity / f"batch_{stamp()}.jsonl", rows)
            state[entity] = max(r["updated_at"] for r in rows)  # high-water mark
            STATE.write_text(json.dumps(state, indent=2))
            deleted = sum(1 for r in rows if r.get("deleted"))
            log("v2", f"{entity}: {len(rows)} changed rows since {since} ({time.time() - t0:.1f}s)"
                + (f", {deleted} soft deletes" if deleted else ""))


log("v2", f"incremental every {args.interval}s on updated_at"
    f"{', soft deletes' if args.soft_deletes else ''}"
    f"{f', reconcile every {args.reconcile_every} runs' if args.reconcile_every else ''}")
run_forever(sync, args.interval, args.once)
