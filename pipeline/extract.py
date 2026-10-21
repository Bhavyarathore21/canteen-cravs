"""Stage 1: EXTRACT. Read data out of the app database, one day at a time."""
import sqlite3
from pathlib import Path

import pandas as pd

# The app stores UTC. India is UTC+5:30 all year, so +330 minutes gives the
# Indian calendar day an order belongs to.
IST = "'+330 minutes'"


def connect(path):
    if not Path(path).exists():
        raise SystemExit(f"Source database not found: {path}\nRun simulator.py first.")
    return sqlite3.connect(path)


def _query(conn, sql, params=()):
    return pd.read_sql_query(sql, conn, params=params)


def list_days(conn):
    """Every Indian calendar day that has orders or stock changes."""
    rows = conn.execute(
        f"SELECT date(created_at, {IST}) FROM orders "
        f"UNION SELECT date(changed_at, {IST}) FROM stock_log ORDER BY 1"
    ).fetchall()
    return [r[0] for r in rows]


def extract_day(conn, day):
    """All rows that belong to this day. Order details follow their order's day."""
    on_day = f"date(o.created_at, {IST}) = ?"
    return {
        "orders": _query(
            conn,
            "SELECT o.id, o.order_code, o.user_id, o.total, o.status, "
            "o.payment_method, o.payment_status, o.created_at "
            f"FROM orders o WHERE {on_day}", (day,)),
        "order_items": _query(
            conn,
            "SELECT i.id, i.order_id, i.menu_item_id, i.quantity, i.price "
            f"FROM order_items i JOIN orders o ON o.id = i.order_id WHERE {on_day}", (day,)),
        "order_status_log": _query(
            conn,
            "SELECT l.id, l.order_id, l.status, l.changed_at "
            f"FROM order_status_log l JOIN orders o ON o.id = l.order_id WHERE {on_day}", (day,)),
        "payment_log": _query(
            conn,
            "SELECT p.id, p.order_id, p.event, p.amount, p.reference, p.created_at "
            f"FROM payment_log p JOIN orders o ON o.id = p.order_id WHERE {on_day}", (day,)),
        "stock_log": _query(
            conn,
            "SELECT id, menu_item_id, quantity_after, reason, changed_at "
            f"FROM stock_log WHERE date(changed_at, {IST}) = ?", (day,)),
    }


def extract_lookups(conn):
    """Small tables, copied fully. Names, emails and password hashes are
    deliberately NOT selected, so they never leave the app database."""
    return {
        "users": _query(conn, "SELECT id, role, no_show_count, created_at FROM users"),
        "menu_items": _query(
            conn, "SELECT id, name, price, category, stock, is_available FROM menu_items"),
    }


def count_rows(conn):
    """Row counts in the source, used to check nothing was lost on the way."""
    tables = ["orders", "order_items", "order_status_log", "payment_log",
              "stock_log", "users", "menu_items"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
