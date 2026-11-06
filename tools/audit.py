"""Compare what each collector *believes* about customers with the truth.

The source DB is the oracle (we cheat: we read it directly). For each lake we
reconstruct "current state" the way a downstream model naively would, then count:

  missing   rows that exist in the source but not in the warehouse
  ghosts    rows in the warehouse that were deleted in the source
  stale     rows whose (name, email, plan, country) differ from the source
  history   distinct customer versions ever captured vs versions that ever existed

and for deletes, among the ids each pipeline has ever seen:
  deletes found   ids it knows are deleted / ids that really were deleted
  false deletes   ids it thinks are deleted that still exist (hello, truncated dump)
"""

import json
import sqlite3
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LAKE = ROOT / "data" / "lake"
SOURCE = ROOT / "data" / "source.db"
FIELDS = ("name", "email", "plan", "country")


def key(row: dict) -> tuple:
    return tuple(row.get(f) for f in FIELDS)  # note: no fallback for schema drift


@lru_cache(maxsize=4096)
def read(path: Path) -> list[dict]:
    """Lake files are immutable once written, so cache by path."""
    with path.open() as f:
        return [json.loads(line) for line in f]


def truth() -> tuple[dict[int, tuple], int]:
    con = sqlite3.connect(f"file:{SOURCE}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    current = {r["id"]: key(dict(r)) for r in con.execute("SELECT * FROM customers")}
    cols = ", ".join(f"json_extract(data, '$.{f}')" for f in FIELDS)
    versions = con.execute(
        f"SELECT count(*) FROM (SELECT DISTINCT entity_id, {cols} FROM events "
        "WHERE entity='customers' AND op != 'delete')").fetchone()[0]
    return current, versions


class Lake:
    """What one pipeline believes."""

    def __init__(self, current, seen, ever, deleted, nfiles, dupes=0):
        self.current = current      # id -> key, its idea of "customers right now"
        self.seen = seen            # {(id, key)} every version it ever captured
        self.ever = ever            # every id it has ever heard of
        self.deleted = deleted      # ids it believes were deleted
        self.nfiles, self.dupes = nfiles, dupes


def lake_v1() -> Lake | None:
    """Deletes = in an earlier snapshot, absent from the latest one."""
    files = sorted((LAKE / "v1" / "customers").glob("snapshot_*.jsonl"))
    if not files:
        return None
    latest = read(files[-1])
    current = {r["id"]: key(r) for r in latest}
    seen = {(r["id"], key(r)) for f in files for r in read(f) if "plan" in r}
    ever = {r["id"] for f in files for r in read(f)}
    return Lake(current, seen, ever, ever - current.keys(), len(files), len(latest) - len(current))


def lake_v2() -> Lake | None:
    """Deletes = soft-delete rows (v2.1) + ids missing from a reconciliation id list."""
    files = sorted((LAKE / "v2" / "customers").glob("batch_*.jsonl"))
    if not files:
        return None
    current, latest_ts, seen, first_seen, deleted = {}, {}, set(), {}, set()
    for f in files:
        for r in read(f):
            first_seen.setdefault(r["id"], r["_extracted_at"])
            if r.get("deleted"):
                deleted.add(r["id"])
            elif "plan" in r:
                seen.add((r["id"], key(r)))
            if r["updated_at"] >= latest_ts.get(r["id"], ""):
                latest_ts[r["id"]], current[r["id"]] = r["updated_at"], (None if r.get("deleted") else key(r))
    for f in sorted((LAKE / "v2" / "customers_ids").glob("ids_*.jsonl")):
        rows = read(f)
        if not rows:
            continue
        live, at = {r["id"] for r in rows}, rows[0]["_snapshot_at"]
        deleted |= {i for i, t in first_seen.items() if t <= at and i not in live}
    current = {i: k for i, k in current.items() if k is not None and i not in deleted}
    return Lake(current, seen, set(first_seen), deleted, len(files))


def lake_v3() -> Lake | None:
    """Deletes = tombstone events. That's it."""
    files = sorted((LAKE / "v3" / "changes").glob("batch_*.jsonl"))
    if not files:
        return None
    events = {}
    for f in files:
        for e in read(f):
            if e["entity"] == "customers":
                events[e["seq"]] = e  # dedupe on seq
    current, seen, ever, deleted = {}, set(), set(), set()
    for seq in sorted(events):
        e = events[seq]
        ever.add(e["id"])
        if e["op"] == "delete":
            current.pop(e["id"], None)
            deleted.add(e["id"])
        else:
            current[e["id"]] = key(e["data"])
            seen.add((e["id"], key(e["data"])))
    return Lake(current, seen, ever, deleted, len(files))


def lake_bytes(version: str) -> int:
    d = LAKE / version
    return sum(p.stat().st_size for p in d.rglob("*.jsonl")) if d.exists() else 0


def audit() -> dict:
    if not SOURCE.exists():
        return {}
    src, true_versions = truth()
    out = {"_truth": {"rows": len(src), "versions": true_versions}}
    for name, fn in (("v1", lake_v1), ("v2", lake_v2), ("v3", lake_v3)):
        res = fn()
        if res is None:
            continue
        wh = res.current
        truly_deleted = res.ever - src.keys()
        out[name] = {
            "rows": len(wh),
            "missing": len(src.keys() - wh.keys()),
            "ghosts": len(wh.keys() - src.keys()),
            "stale": sum(1 for i in src.keys() & wh.keys() if src[i] != wh[i]),
            "dupes": res.dupes,
            "history": len(res.seen),
            "deletes_found": len(res.deleted & truly_deleted),
            "deletes_true": len(truly_deleted),
            "false_deletes": len(res.deleted & src.keys()),
            "files": res.nfiles,
            "bytes": lake_bytes(name),
        }
    return out


if __name__ == "__main__":
    print(json.dumps(audit(), indent=2))
