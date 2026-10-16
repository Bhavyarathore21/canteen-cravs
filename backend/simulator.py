"""
simulator.py - generates ~3 months of realistic canteen data.

It writes to a SEPARATE database (canteen_sim.db), so your real app data
in canteen.db is never touched. Run it from the backend folder:

    python simulator.py

The random seed is fixed, so everyone gets the same data (reproducible).
"""
import itertools
import os
import random
from datetime import date, datetime, time, timedelta

import database

SIM_DB = "canteen_sim.db"
database.DB_NAME = SIM_DB  # the simulator only ever writes to the sim database

random.seed(42)

START = date(2026, 7, 1)
DAYS = 90
N_STUDENTS = 600
IST = timedelta(hours=5, minutes=30)  # the app stores times in UTC
FMT = "%Y-%m-%d %H:%M:%S"
DUMMY_HASH = "simulated-account-cannot-login"

# name, price, category, popularity, daily prep quantity, kind
MENU = [
    ("Chai", 10, "Beverages", 12, 60, "drink"),
    ("Coffee", 20, "Beverages", 5, 40, "drink"),
    ("Cold Drink", 20, "Beverages", 5, 40, "any"),
    ("Cold Coffee", 40, "Beverages", 4, 25, "any"),
    ("Samosa", 15, "Snacks", 10, 40, "snack"),
    ("Vada Pav", 20, "Snacks", 7, 38, "snack"),
    ("Veg Sandwich", 40, "Snacks", 6, 40, "snack"),
    ("Maggi", 30, "Snacks", 7, 36, "snack"),
    ("Poha", 25, "Breakfast", 5, 25, "breakfast"),
    ("Veg Thali", 70, "Meals", 8, 55, "lunch"),
    ("Chole Bhature", 60, "Meals", 6, 40, "lunch"),
    ("Fried Rice", 55, "Meals", 4, 30, "lunch"),
]

# how busy each hour is (IST). 12 to 2 pm is the lunch rush
HOUR_WEIGHTS = {8: 3, 9: 5, 10: 6, 11: 9, 12: 16, 13: 18, 14: 9, 15: 7, 16: 8, 17: 3}


def utc(dt):
    """Convert a local (IST) time into the UTC text the app stores."""
    return (dt - IST).strftime(FMT)


def day_start(d):
    return datetime.combine(START + timedelta(days=d), time(0, 0))


def affinity(kind, hour):
    """How much people want this kind of food at this hour."""
    if kind == "breakfast":
        return 3.0 if hour < 11 else 0.1
    if kind == "lunch":
        if 12 <= hour <= 14:
            return 3.5
        return 0.8 if hour in (11, 15) else 0.05
    if kind == "snack":
        return 2.0 if hour >= 15 else 1.0
    if kind == "drink":
        return 2.0 if (hour <= 9 or hour >= 15) else 1.0
    return 1.0


def orders_for_day(day_index, weekday):
    """How many orders arrive today. App usage grows over the 90 days."""
    adoption = 0.35 + 0.65 * day_index / (DAYS - 1)
    weekday_factor = [1.10, 1.05, 1.00, 1.00, 0.90, 0.50][weekday]  # Mon to Sat
    return max(10, int(220 * adoption * weekday_factor * random.gauss(1.0, 0.12)))


# ---------------------------------------------------------------- setup
if os.path.exists(SIM_DB):
    try:
        os.remove(SIM_DB)
    except PermissionError:
        raise SystemExit("Close canteen_sim.db (SQLite Viewer tab) and run again.")

database.create_tables()
conn = database.get_connection()

conn.execute(
    "INSERT INTO users (name, email, password_hash, role, created_at) "
    "VALUES ('Canteen Admin', 'admin@sim.local', ?, 'admin', ?)",
    (DUMMY_HASH, utc(day_start(0))),
)

students = []
for i in range(1, N_STUDENTS + 1):
    joined = 0 if random.random() < 0.40 else random.randint(1, 60)
    unreliable = random.random() < 0.06
    cur = conn.execute(
        "INSERT INTO users (name, email, password_hash, role, created_at) "
        "VALUES (?, ?, ?, 'user', ?)",
        (f"Student {i:03d}", f"student{i:03d}@sim.local", DUMMY_HASH,
         utc(day_start(joined) + timedelta(hours=9))),
    )
    students.append({
        "id": cur.lastrowid,
        "joined": joined,                                  # day they joined the app
        "weight": min(random.paretovariate(1.5), 15),      # some students order far more
        "noshow_p": 0.30 if unreliable else 0.02,          # chance of not collecting
        "upi_pref": random.random() < 0.72,
        "noshows": 0,
    })

menu = []
for name, price, category, pop, prep, kind in MENU:
    cur = conn.execute(
        "INSERT INTO menu_items (name, price, category, stock, is_available) "
        "VALUES (?, ?, ?, ?, 1)",
        (name, price, category, prep),
    )
    menu.append({
        "id": cur.lastrowid, "name": name, "price": price, "pop": pop,
        "prep": prep, "kind": kind, "stock": 0,
        "out_today": False, "soldout_days": 0, "soldout_hours": [],
    })

HOLIDAYS = set(random.sample(range(DAYS), 4))
used_refs = set()


# ---------------------------------------------------------------- helpers
def new_ref():
    """A unique 12-digit UPI reference number."""
    while True:
        ref = str(random.randint(10**11, 10**12 - 1))
        if ref not in used_refs:
            used_refs.add(ref)
            return ref


def status_log(order_id, status, when):
    conn.execute(
        "INSERT INTO order_status_log (order_id, status, changed_at) VALUES (?, ?, ?)",
        (order_id, status, utc(when)),
    )


def pay_log(order_id, event, amount, when, ref=None):
    conn.execute(
        "INSERT INTO payment_log (order_id, event, amount, reference, created_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (order_id, event, amount, ref, utc(when)),
    )


def stock_log(item_id, quantity_after, reason, when):
    conn.execute(
        "INSERT INTO stock_log (menu_item_id, quantity_after, reason, changed_at) "
        "VALUES (?, ?, ?, ?)",
        (item_id, quantity_after, reason, utc(when)),
    )


def pick_wanted(hour):
    """What this student would like to order (before checking stock)."""
    n_lines = random.choices([1, 2, 3], weights=[55, 35, 10])[0]
    pool = list(menu)
    wanted = []
    for _ in range(n_lines):
        weights = [m["pop"] * affinity(m["kind"], hour) for m in pool]
        item = random.choices(pool, weights=weights)[0]
        pool.remove(item)
        qty = random.choices([1, 2, 3], weights=[72, 22, 6])[0]
        wanted.append((item, qty))
    return wanted


# ---------------------------------------------------------------- simulate
lost_units = 0
lost_revenue = 0
total_orders = 0

for d in range(DAYS):
    day = START + timedelta(days=d)
    weekday = day.weekday()
    if weekday == 6 or d in HOLIDAYS:  # Sundays and 4 random holidays: closed
        continue

    # Morning: fresh food is prepared, so stock resets every day
    open_time = datetime.combine(day, time(7, 30))
    for m in menu:
        m["stock"] = m["prep"]
        m["out_today"] = False
        stock_log(m["id"], m["stock"], "daily prep", open_time)

    active = [s for s in students if s["joined"] <= d]
    cum_weights = list(itertools.accumulate(s["weight"] for s in active))

    n_orders = orders_for_day(d, weekday)
    hours = random.choices(
        list(HOUR_WEIGHTS), weights=list(HOUR_WEIGHTS.values()), k=n_orders
    )
    times = sorted(
        datetime.combine(day, time(h, 0)) + timedelta(seconds=random.randint(0, 3599))
        for h in hours
    )

    recent = []             # recent order times, to measure how busy the kitchen is
    pending_restores = []   # stock coming back from cancelled orders
    code_no = 0

    for placed in times:
        # Put back stock from cancellations that have happened by now
        due = [p for p in pending_restores if p[0] <= placed]
        pending_restores = [p for p in pending_restores if p[0] > placed]
        for when, item, qty, code in sorted(due, key=lambda p: p[0]):
            item["stock"] += qty
            stock_log(item["id"], item["stock"], f"cancelled by student, order {code}", when)

        # Work out what can actually be sold (stock check)
        lines = []
        for item, qty in pick_wanted(placed.hour):
            got = min(qty, item["stock"])
            if got < qty:  # lost sale because the item ran out
                lost_units += qty - got
                lost_revenue += (qty - got) * item["price"]
            if got > 0:
                lines.append((item, got))
        if not lines:
            continue  # nothing available, so no order was placed

        student = random.choices(active, cum_weights=cum_weights)[0]
        code_no += 1
        order_code = f"A{code_no:03d}"
        total = sum(item["price"] * qty for item, qty in lines)

        # Cash is blocked after 3 no-shows (same rule as the real app)
        if student["noshows"] >= 3:
            method = "UPI"
        elif student["upi_pref"]:
            method = "UPI" if random.random() < 0.92 else "Cash"
        else:
            method = "UPI" if random.random() < 0.25 else "Cash"

        # Reduce stock at the moment of ordering
        for item, qty in lines:
            item["stock"] -= qty
            stock_log(item["id"], item["stock"], f"order {order_code}", placed)
            if item["stock"] == 0 and not item["out_today"]:
                item["out_today"] = True
                item["soldout_days"] += 1
                item["soldout_hours"].append(placed.hour + placed.minute / 60)

        # How long the kitchen needs: depends on items and how busy it is
        recent = [p for p in recent if (placed - p).total_seconds() <= 600]
        kitchen_load = len(recent)
        recent.append(placed)
        units = sum(qty for _, qty in lines)
        prep_min = min(35, 3 + 1.2 * units + 1.3 * kitchen_load + random.uniform(0, 3))

        status_events = [("Placed", placed)]
        pay_events = []  # (event, amount, when, reference)
        ref = new_ref() if method == "UPI" else None
        if method == "UPI":
            submit_t = placed + timedelta(seconds=random.randint(20, 90))
            confirm_t = submit_t + timedelta(seconds=random.randint(15, 120))

        if random.random() < 0.035:  # cancelled by the student
            paid_before = method == "UPI" and random.random() < 0.5
            if paid_before:
                cancel_t = confirm_t + timedelta(seconds=random.randint(10, 60))
            else:
                cancel_t = placed + timedelta(seconds=random.randint(60, 180))
            final_status = "Cancelled"
            status_events.append(("Cancelled", cancel_t))
            payment_status = "Pending"
            if paid_before:
                refund_t = cancel_t + timedelta(minutes=random.randint(10, 180))
                pay_events += [
                    ("UPI reference submitted", total, submit_t, ref),
                    ("UPI payment confirmed", total, confirm_t, None),
                    ("Refund pending (order cancelled)", total, cancel_t, None),
                    ("Refund sent", total, refund_t, None),
                ]
                payment_status = "Refunded"
            for item, qty in lines:
                pending_restores.append((cancel_t, item, qty, order_code))
        else:
            if method == "UPI":
                pay_events += [
                    ("UPI reference submitted", total, submit_t, ref),
                    ("UPI payment confirmed", total, confirm_t, None),
                ]
                prep_start = confirm_t + timedelta(seconds=random.randint(10, 90))
            else:
                prep_start = placed + timedelta(seconds=random.randint(20, 150))
            ready_t = prep_start + timedelta(minutes=prep_min)
            status_events += [("Preparing", prep_start), ("Ready", ready_t)]

            noshow_p = student["noshow_p"] * (1.5 if method == "Cash" else 1.0)
            if random.random() < noshow_p:  # never collected
                expired_t = ready_t + timedelta(minutes=random.randint(30, 50))
                status_events.append(("Expired", expired_t))
                final_status = "Expired"
                student["noshows"] += 1
                payment_status = "Paid" if method == "UPI" else "Pending"
            else:
                wait = min(1500, int(random.expovariate(1 / 240))) + 30
                completed_t = ready_t + timedelta(seconds=wait)
                if method == "Cash":
                    pay_events.append(
                        ("Cash received", total, completed_t - timedelta(seconds=5), None)
                    )
                status_events.append(("Completed", completed_t))
                final_status = "Completed"
                payment_status = "Paid"

        # Save everything about this order
        cur = conn.execute(
            "INSERT INTO orders (order_code, user_id, total, status, payment_method, "
            "payment_status, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (order_code, student["id"], total, final_status, method,
             payment_status, utc(placed)),
        )
        order_id = cur.lastrowid
        for item, qty in lines:
            conn.execute(
                "INSERT INTO order_items (order_id, menu_item_id, quantity, price) "
                "VALUES (?, ?, ?, ?)",
                (order_id, item["id"], qty, item["price"]),
            )
        for status, when in status_events:
            status_log(order_id, status, when)
        for event, amount, when, reference in pay_events:
            pay_log(order_id, event, amount, when, reference)
        total_orders += 1

    # End of day: any cancellations still waiting put stock back
    for when, item, qty, code in sorted(pending_restores, key=lambda p: p[0]):
        item["stock"] += qty
        stock_log(item["id"], item["stock"], f"cancelled by student, order {code}", when)

for s in students:
    conn.execute("UPDATE users SET no_show_count = ? WHERE id = ?", (s["noshows"], s["id"]))
conn.commit()

# ---------------------------------------------------------------- summary
print(f"Created {SIM_DB}: {total_orders} orders over {DAYS} days "
      f"({START} to {START + timedelta(days=DAYS - 1)})")
print("\nOrders by final status:")
for row in conn.execute("SELECT status, COUNT(*) AS n FROM orders GROUP BY status ORDER BY n DESC"):
    print(f"  {row['status']:<10} {row['n']}")
print("\nPayment method split:")
for row in conn.execute("SELECT payment_method, COUNT(*) AS n FROM orders GROUP BY payment_method"):
    print(f"  {row['payment_method']:<10} {row['n']}")
print("\nItems that sold out (days out of {} open days):".format(
    conn.execute("SELECT COUNT(DISTINCT date(changed_at)) FROM stock_log").fetchone()[0]))
for m in sorted(menu, key=lambda m: -m["soldout_days"]):
    if m["soldout_days"]:
        avg = sum(m["soldout_hours"]) / len(m["soldout_hours"])
        print(f"  {m['name']:<14} {m['soldout_days']:>3} days, usually out around "
              f"{int(avg)}:{int((avg % 1) * 60):02d}")
print(f"\nGround truth (hidden in the data, your analysis should find it):")
print(f"  Lost sales from sell-outs: {lost_units} items, about Rs {lost_revenue}")
conn.close()