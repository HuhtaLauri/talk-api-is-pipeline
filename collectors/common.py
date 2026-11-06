import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("TALK_DATA", ROOT / "data"))
LAKE = DATA / "lake"
BASE = os.environ.get("SHOP_URL", "http://localhost:8000")


def stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def log(tag: str, msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} [{tag}] {msg}", flush=True)


class GiveUp(Exception):
    pass


def get(client: httpx.Client, path: str, params: dict, retries: int, tag: str) -> dict:
    """GET with optional retries. 429 honours Retry-After, 5xx backs off exponentially."""
    for attempt in range(retries + 1):
        try:
            r = client.get(BASE + path, params=params, timeout=30)
        except httpx.TransportError as e:
            status, wait = f"transport error {e!r}", 2 ** attempt * 0.2
        else:
            if r.status_code == 200:
                return r.json()
            status = f"HTTP {r.status_code}"
            wait = float(r.headers.get("Retry-After", 2 ** attempt * 0.2))
        if attempt < retries:
            log(tag, f"{status} on {path} {params}, retry {attempt + 1}/{retries} in {wait:.1f}s")
            time.sleep(wait)
    raise GiveUp(f"{status} on {path} {params}")


def write_jsonl(path: Path, rows: list[dict]) -> None:
    """Atomic: readers never see half a file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with tmp.open("w") as f:
        for row in rows:
            f.write(json.dumps(row) + "\n")
    tmp.rename(path)


def run_forever(fn, interval: float, once: bool) -> None:
    while True:
        try:
            fn()
        except GiveUp as e:
            log("!!", f"run failed: {e}")
        except httpx.HTTPError as e:
            log("!!", f"run failed: {e!r}")
        if once:
            return
        try:
            time.sleep(interval)
        except KeyboardInterrupt:
            sys.exit(0)
