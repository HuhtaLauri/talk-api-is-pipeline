"""Poke the chaos knobs.

  ctl                       show knobs
  ctl latency_ms=300 error_rate=0.02
  ctl drift=on
  ctl reset                 back to defaults
"""

import json
import os
import sys

import httpx

BASE = os.environ.get("SHOP_URL", "http://localhost:8000")


def main(argv: list[str]) -> None:
    if not argv:
        r = httpx.get(f"{BASE}/admin/chaos")
    elif argv == ["reset"]:
        r = httpx.post(f"{BASE}/admin/chaos", json={"reset": True})
    else:
        changes = {}
        for a in argv:
            if "=" not in a:
                sys.exit(f"expected knob=value, got {a!r}\n{__doc__}")
            k, v = a.split("=", 1)
            try:
                changes[k] = json.loads(v)
            except json.JSONDecodeError:
                changes[k] = v
        r = httpx.post(f"{BASE}/admin/chaos", json=changes)
    if r.status_code != 200:
        sys.exit(r.text)
    for k, v in r.json().items():
        print(f"  {k:<14} {v}")


if __name__ == "__main__":
    main(sys.argv[1:])
