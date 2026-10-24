"""Stage 2: TRANSFORM. Clean the raw data and build analytics-ready tables."""
import hashlib

import pandas as pd

import config


def to_ist(series):
    """UTC text -> Indian time (no timezone label, easier to work with)."""
    return pd.to_datetime(series, utc=True).dt.tz_convert(config.LOCAL_TZ).dt.tz_localize(None)


def hash_reference(ref):
    """UPI reference numbers are replaced by a one-way hash."""
    if pd.isna(ref):
        return None
    return hashlib.sha256(str(ref).encode()).hexdigest()[:16]


def clean(raw):
    """Takes the raw tables, returns cleaned tables with Indian times and new columns."""
    # ---- status history: one row per (order, status) -> one row per order
    log = raw["order_status_log"].drop_duplicates("id").copy()
    log["changed_at_ist"] = to_ist(log["changed_at"])
    events = log.groupby(["order_id", "status"])["changed_at_ist"].min().unstack("status")
    wanted = ["Placed", "Preparing", "Ready", "Completed", "Cancelled", "Expired"]
    events = events.reindex(columns=wanted)
    events.columns = [f"{c.lower()}_at" for c in events.columns]  # placed_at, ready_at ...
    for col in events.columns:  # a status that never happened must still be a date column
        events[col] = pd.to_datetime(events[col])

    # ---- payments
    pay = raw["payment_log"].drop_duplicates("id").copy()
    pay["created_at_ist"] = to_ist(pay["created_at"])
    pay["reference_hash"] = pay["reference"].map(hash_reference)
    pay = pay.drop(columns=["reference", "created_at"])

    paid_at = (pay[pay["event"].isin(["UPI payment confirmed", "Cash received"])]
               .groupby("order_id")["created_at_ist"].min().rename("paid_at"))
    refunded = (pay[pay["event"] == "Refund sent"]
                .groupby("order_id")["amount"].sum().rename("refunded_amount"))

    # ---- orders
    o = raw["orders"].drop_duplicates("id").copy()
    o["status"] = o["status"].str.strip().str.title()
    o["payment_status"] = o["payment_status"].str.strip().str.title()
    o["payment_method"] = (o["payment_method"].str.strip().str.lower()
                           .map({"upi": "UPI", "cash": "Cash"}))  # unknown values become NaN
    o["created_at_ist"] = to_ist(o["created_at"])
    o = o.drop(columns="created_at")

    o["order_date"] = o["created_at_ist"].dt.strftime("%Y-%m-%d")
    o["order_hour"] = o["created_at_ist"].dt.hour
    o["weekday"] = o["created_at_ist"].dt.day_name()
    o["is_weekend"] = o["created_at_ist"].dt.dayofweek >= 5
    o["day_slot"] = pd.cut(
        o["order_hour"], bins=[0, 11, 12, 14, 15, 24], right=False,
        labels=["Morning", "Pre-lunch", "Lunch rush", "Post-lunch", "Evening"],
    ).astype(str)

    o = (o.merge(events, how="left", left_on="id", right_index=True)
          .merge(paid_at, how="left", left_on="id", right_index=True)
          .merge(refunded, how="left", left_on="id", right_index=True))
    o["refunded_amount"] = o["refunded_amount"].fillna(0)

    def minutes(later, earlier):
        return ((o[later] - o[earlier]).dt.total_seconds() / 60).round(2)

    o["minutes_to_start_cooking"] = minutes("preparing_at", "placed_at")
    o["prep_minutes"] = minutes("ready_at", "preparing_at")
    o["wait_minutes"] = minutes("ready_at", "placed_at")           # what the student feels
    o["pickup_delay_minutes"] = minutes("completed_at", "ready_at")

    # ---- order lines
    items = raw["order_items"].drop_duplicates("id").copy()
    items["line_total"] = items["quantity"] * items["price"]

    # ---- stock history
    s = raw["stock_log"].drop_duplicates("id").copy()
    s["changed_at_ist"] = to_ist(s["changed_at"])
    s["log_date"] = s["changed_at_ist"].dt.strftime("%Y-%m-%d")
    s["log_hour"] = s["changed_at_ist"].dt.hour
    s["is_sell_out"] = s["quantity_after"].eq(0) & s["reason"].fillna("").str.startswith("order")
    s = s.drop(columns="changed_at")

    # ---- lookups
    u = raw["users"].drop_duplicates("id").copy()
    u["joined_at_ist"] = to_ist(u["created_at"])
    u = u.drop(columns="created_at")
    m = raw["menu_items"].drop_duplicates("id").copy()
    m["is_available"] = m["is_available"].astype(bool)

    return {"orders": o, "order_items": items, "payment_log": pay,
            "stock_log": s, "users": u, "menu_items": m}


def build_curated(cleaned):
    """Analytics-ready tables: joined, enriched and summarised."""
    o, items, menu = cleaned["orders"], cleaned["order_items"], cleaned["menu_items"]

    order_items = (
        items
        .merge(menu[["id", "name", "category"]].rename(
            columns={"id": "menu_item_id", "name": "item_name"}), on="menu_item_id", how="left")
        .merge(o[["id", "order_date", "order_hour", "weekday", "day_slot", "status",
                  "payment_method"]].rename(columns={"id": "order_id", "status": "order_status"}),
               on="order_id", how="left")
    )

    flags = o.assign(
        is_completed=o["status"].eq("Completed"),
        is_cancelled=o["status"].eq("Cancelled"),
        is_expired=o["status"].eq("Expired"),
        is_upi=o["payment_method"].eq("UPI"),
        revenue=o["total"].where(o["status"].eq("Completed"), 0),
    )
    daily = flags.groupby("order_date").agg(
        orders=("id", "count"),
        completed_orders=("is_completed", "sum"),
        cancelled_orders=("is_cancelled", "sum"),
        expired_orders=("is_expired", "sum"),
        revenue=("revenue", "sum"),
        avg_wait_minutes=("wait_minutes", "mean"),
        upi_share=("is_upi", "mean"),
    ).round(2).reset_index()

    return {
        "orders_enriched": o,
        "order_items_enriched": order_items,
        "stock_events": cleaned["stock_log"],
        "payments": cleaned["payment_log"],
        "users": cleaned["users"],
        "menu_items": menu,
        "daily_sales_summary": daily,
    }
