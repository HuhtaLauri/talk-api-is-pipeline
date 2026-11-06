"""The 'business': a random walk of customers and orders.

Used twice: once to backfill ~90 days of history at seed time (fake clock),
and live in a background thread inside the API server (real clock).
"""

import random
import sqlite3
from datetime import datetime, timedelta

from . import db

FIRST = "Aino Eero Lauri Maija Ville Saara Juha Noora Mikko Iida Ada Grace Linus Ken Barbara Edsger Margaret Dennis".split()
LAST = "Virtanen Korhonen Nieminen Lovelace Hopper Torvalds Thompson Liskov Dijkstra Hamilton Ritchie Kernighan Mäkinen".split()
COUNTRIES = ["FI", "SE", "NO", "DE", "NL", "EE", "US", "GB"]
PLANS = {"free": 0, "pro": 49, "enterprise": 499}
NEXT_STATUS = {"placed": "paid", "paid": "shipped", "shipped": "delivered"}


class Sim:
    def __init__(self, con: sqlite3.Connection, seed: int | None = None, log=None):
        self.con = con
        self.rng = random.Random(seed)
        self.log = log or (lambda msg: None)

    # -- helpers -----------------------------------------------------------
    def _random_row(self, table: str, where: str = "1=1") -> dict | None:
        hi = self.con.execute(f"SELECT max(id) FROM {table}").fetchone()[0] or 0
        if not hi:
            return None
        pivot = self.rng.randint(1, hi)
        r = self.con.execute(
            f"SELECT * FROM {table} WHERE id >= ? AND {where} ORDER BY id LIMIT 1", (pivot,)
        ).fetchone() or self.con.execute(
            f"SELECT * FROM {table} WHERE {where} ORDER BY id LIMIT 1"
        ).fetchone()
        return dict(r) if r else None

    # -- operations --------------------------------------------------------
    def new_customer(self, at):
        first, last = self.rng.choice(FIRST), self.rng.choice(LAST)
        plan = self.rng.choices(list(PLANS), weights=[6, 3, 1])[0]
        row = db.apply(self.con, "customers", "insert", {
            "name": f"{first} {last}",
            "email": f"{first}.{last}{self.rng.randint(1, 9999)}@example.com".lower(),
            "plan": plan, "country": self.rng.choice(COUNTRIES),
            "mrr": PLANS[plan] * self.rng.uniform(0.8, 1.5),
        }, at)
        self.log(f"+ customer {row['id']} {row['name']} ({plan})")

    def change_plan(self, at):
        c = self._random_row("customers")
        if not c:
            return
        old = c["plan"]
        c["plan"] = self.rng.choice([p for p in PLANS if p != old])
        c["mrr"] = PLANS[c["plan"]] * self.rng.uniform(0.8, 1.5)
        db.apply(self.con, "customers", "update", c, at)
        self.log(f"~ customer {c['id']} plan {old} -> {c['plan']}")

    def change_email(self, at):
        c = self._random_row("customers")
        if not c:
            return
        c["email"] = c["email"].replace("@example.com", f"+{self.rng.randint(1, 99)}@example.com")
        db.apply(self.con, "customers", "update", c, at)
        self.log(f"~ customer {c['id']} email changed")

    def move_country(self, at):
        c = self._random_row("customers")
        if not c:
            return
        old = c["country"]
        c["country"] = self.rng.choice([x for x in COUNTRIES if x != old])
        db.apply(self.con, "customers", "update", c, at)
        self.log(f"~ customer {c['id']} moved {old} -> {c['country']}")

    def delete_customer(self, at):
        c = self._random_row("customers")
        if not c:
            return
        db.apply(self.con, "customers", "delete", c, at)
        self.log(f"- customer {c['id']} deleted (GDPR, churn, who knows)")

    def new_order(self, at):
        c = self._random_row("customers")
        if not c:
            return
        row = db.apply(self.con, "orders", "insert", {
            "customer_id": c["id"], "amount": round(self.rng.lognormvariate(4, 0.8), 2), "status": "placed",
        }, at)
        self.log(f"+ order {row['id']} for customer {c['id']} €{row['amount']}")

    def advance_order(self, at):
        o = self._random_row("orders", "status IN ('placed','paid','shipped')")
        if not o:
            return
        old = o["status"]
        o["status"] = "refunded" if self.rng.random() < 0.05 else NEXT_STATUS[old]
        db.apply(self.con, "orders", "update", o, at)
        self.log(f"~ order {o['id']} {old} -> {o['status']}")

    def step(self, at: datetime, delete_weight: float = 2.0) -> None:
        ops = [
            (self.new_customer, 10), (self.change_plan, 15), (self.change_email, 5),
            (self.move_country, 3), (self.delete_customer, delete_weight),
            (self.new_order, 40), (self.advance_order, 25),
        ]
        fn = self.rng.choices([o for o, _ in ops], weights=[w for _, w in ops])[0]
        fn(at)


def seed(days: int = 90, customers: int = 1500, ops: int = 40_000, rng_seed: int = 42) -> None:
    """Create the database with a backfilled history."""
    if db.DB_PATH.exists():
        print(f"{db.DB_PATH} exists, skipping seed (just reset to start over)")
        return
    con = db.connect()
    db.init(con)
    sim = Sim(con, seed=rng_seed)
    start = db.now() - timedelta(days=days)
    con.execute("BEGIN")
    t = start
    for _ in range(customers):
        t += timedelta(seconds=sim.rng.uniform(0, 600))
        sim.new_customer(t)
    span = (db.now() - t).total_seconds()
    for _ in range(ops):
        t += timedelta(seconds=sim.rng.expovariate(ops / span))
        if t >= db.now():
            break
        sim.step(t)
    con.execute("COMMIT")
    n = {k: con.execute(f"SELECT count(*) FROM {k}").fetchone()[0] for k in ("customers", "orders", "events")}
    print(f"seeded {db.DB_PATH}: {n}")


if __name__ == "__main__":
    seed()
