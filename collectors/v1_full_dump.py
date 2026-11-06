"""v1 collector: the only thing you CAN do with a snapshot API is take snapshots.

Every run pages through everything and writes a full copy to the lake.
Written the way people actually write these (look for the bugs!).
"""

import argparse
import time

import httpx

from common import LAKE, GiveUp, get, log, now_iso, run_forever, stamp, write_jsonl

p = argparse.ArgumentParser()
p.add_argument("--interval", type=float, default=15, help="seconds between full dumps")
p.add_argument("--page-size", type=int, default=100)
p.add_argument("--retries", type=int, default=0, help="per page. Default 0, like the original script")
p.add_argument("--entities", default="customers,orders")
p.add_argument("--once", action="store_true")
args = p.parse_args()


def dump() -> None:
    with httpx.Client() as client:
        for entity in args.entities.split(","):
            t0, rows, page, snapshot_at = time.time(), [], 1, now_iso()
            while True:
                try:
                    batch = get(client, f"/v1/{entity}", {"page": page, "page_size": args.page_size}, args.retries, "v1")["data"]
                except GiveUp:
                    log("v1", f"{entity}: failed on page {page} after {len(rows)} rows, throwing the dump away")
                    raise
                rows += batch
                if len(batch) < args.page_size:  # "last page"
                    break
                page += 1
            for r in rows:
                r["_snapshot_at"] = snapshot_at
            write_jsonl(LAKE / "v1" / entity / f"snapshot_{stamp()}.jsonl", rows)
            ids = [r["id"] for r in rows]
            dupes = len(ids) - len(set(ids))
            log("v1", f"{entity}: {len(rows)} rows, {page} requests, {time.time() - t0:.1f}s"
                + (f"  ({dupes} duplicate ids!)" if dupes else ""))


log("v1", f"full dump every {args.interval}s, page_size={args.page_size}, retries={args.retries}")
run_forever(dump, args.interval, args.once)
