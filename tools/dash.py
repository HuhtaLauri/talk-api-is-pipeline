"""Live scoreboard: what each API costs the server, and what each pipeline gets right."""

import os
import time

import httpx
from rich.console import Group
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from audit import audit
from shop.server import CHAOS_DEFAULTS as DEFAULTS

BASE = os.environ.get("SHOP_URL", "http://localhost:8000")
COLORS = {"v1": "red", "v2": "yellow", "v3": "green"}


def fmt_bytes(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def bad(n: int) -> Text:
    return Text(str(n), style="bold red" if n else "dim")


def found(n: int, of: int) -> Text:
    style = "green" if n == of else "yellow" if n else "bold red"
    return Text(f"{n}/{of}", style=style if of else "dim")


class Dash:
    def __init__(self):
        self.prev, self.prev_t, self.rates = {}, time.time(), {}
        self.audit, self.audit_t = {}, 0.0

    def poll(self):
        try:
            stats = httpx.get(f"{BASE}/admin/stats", timeout=2).json()
        except httpx.HTTPError:
            return None
        now = time.time()
        dt = max(now - self.prev_t, 1e-6)
        for v, c in stats["versions"].items():
            p = self.prev.get(v, {})
            self.rates[v] = {k: (c.get(k, 0) - p.get(k, 0)) / dt for k in c}
        self.prev, self.prev_t = stats["versions"], now
        if now - self.audit_t > 3:
            try:
                self.audit = audit()
            except Exception as e:  # mid-write, reset, etc. Keep the show going.
                self.audit = {"_error": str(e)}
            self.audit_t = now
        return stats

    def render(self, stats):
        if stats is None:
            return Panel(Text("API is down. (Did you break it? Nice.)", style="bold red"), title="Acme Shop")

        chaos = stats["chaos"]
        knobs = "  ".join(
            f"[bold]{k}[/]=[{'red' if v != d else 'dim'}]{v}[/]"
            for k, v, d in ((k, v, DEFAULTS.get(k, v)) for k, v in chaos.items()))
        t = stats["truth"]
        header = Text.from_markup(
            f"source: [bold]{t['customers']}[/] customers  [bold]{t['orders']}[/] orders  "
            f"[bold]{t['events']}[/] events\n{knobs}")

        load = Table(title="Server load (per second)", expand=True)
        for col in ("api", "req/s", "rows out/s", "rows scanned/s", "500/s", "429/s", "total req", "total scanned"):
            load.add_column(col, justify="right")
        for v in ("v1", "v2", "v3"):
            r, c = self.rates.get(v, {}), stats["versions"].get(v, {})
            load.add_row(Text(v, style=COLORS[v]), f"{r.get('requests', 0):.1f}", f"{r.get('rows_served', 0):.0f}",
                         f"{r.get('rows_scanned', 0):.0f}", f"{r.get('errors', 0):.1f}", f"{r.get('throttled', 0):.1f}",
                         str(c.get("requests", 0)), str(c.get("rows_scanned", 0)))

        a = self.audit
        wh = Table(title="Warehouse vs reality (customers)", expand=True)
        for col in ("pipeline", "rows", "missing", "ghosts", "stale", "dupes", "deletes found", "false deletes",
                    "history captured", "files", "lake size"):
            wh.add_column(col, justify="right")
        tr = a.get("_truth", {})
        if tr:
            wh.add_row("truth", str(tr["rows"]), "", "", "", "", "", "", f"{tr['versions']} versions", "", "")
        for v in ("v1", "v2", "v3"):
            if v not in a:
                wh.add_row(Text(v, style=COLORS[v]), "-", "", "", "", "", "", "", "not running", "", "")
                continue
            x = a[v]
            pct = 100 * x["history"] / tr["versions"] if tr.get("versions") else 0
            wh.add_row(Text(v, style=COLORS[v]), str(x["rows"]), bad(x["missing"]), bad(x["ghosts"]),
                       bad(x["stale"]), bad(x["dupes"]), found(x["deletes_found"], x["deletes_true"]),
                       bad(x["false_deletes"]), f"{x['history']} ({pct:.0f}%)", str(x["files"]),
                       fmt_bytes(x["bytes"]))
        parts = [header, load, wh]
        if "_error" in a:
            parts.append(Text(f"audit: {a['_error']}", style="dim"))
        return Panel(Group(*parts), title="Acme Shop · your API is someone's pipeline")


if __name__ == "__main__":
    d = Dash()
    with Live(d.render(d.poll()), refresh_per_second=2, screen=True) as live:
        while True:
            time.sleep(1)
            live.update(d.render(d.poll()))
