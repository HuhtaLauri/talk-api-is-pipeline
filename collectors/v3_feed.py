"""v3 collector: follow the change feed. Append-only, cursor-based, boring.

Boring is the point: no business logic here. History (SCD2) is built
downstream in SQL, from facts the source already had.
"""

import argparse
import time

import httpx

from common import LAKE, get, log, now_iso, run_forever, stamp, write_jsonl

p = argparse.ArgumentParser()
p.add_argument("--interval", type=float, default=1, help="poll interval when caught up")
p.add_argument("--limit", type=int, default=1000)
p.add_argument("--retries", type=int, default=8)
p.add_argument("--once", action="store_true")
args = p.parse_args()

CURSOR = LAKE / "v3" / "_cursor"


def follow() -> None:
    cursor = int(CURSOR.read_text()) if CURSOR.exists() else 0
    with httpx.Client() as client:
        while True:
            t0 = time.time()
            resp = get(client, "/v3/changes", {"after": cursor, "limit": args.limit}, args.retries, "v3")
            events = resp["events"]
            if not events:
                return
            for e in events:
                e["_extracted_at"] = now_iso()
            # write first, then advance the cursor: at-least-once. Dedupe on seq downstream.
            write_jsonl(LAKE / "v3" / "changes" / f"batch_{cursor:012d}_{stamp()}.jsonl", events)
            cursor = resp["next_cursor"]
            CURSOR.write_text(str(cursor))
            log("v3", f"{len(events)} events, cursor -> {cursor} ({time.time() - t0:.2f}s)")
            if not resp["has_more"]:
                return


log("v3", "following /v3/changes")
run_forever(follow, args.interval, args.once)
