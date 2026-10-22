"""
build_warehouse.py - turns the curated data lake into a STAR SCHEMA.

    curated Parquet files  --->  canteen_dw.duckdb
                                   dim_date, dim_time, dim_user, dim_menu_item   (descriptions)
                                   fact_orders, fact_order_items,
                                   fact_stock_events, fact_payment_events        (events and numbers)

Run from the warehouse folder:   python build_warehouse.py
Safe to run again: it rebuilds every table from scratch.
"""
import sys
from pathlib import Path

import duckdb

HERE = Path(__file__).resolve().parent
CURATED = HERE.parent / "data_lake" / "curated"
DB_FILE = HERE / "canteen_dw.duckdb"


def parquet(name):
    path = CURATED / name / "data.parquet"
    if not path.exists():
        raise SystemExit(f"Missing {path}\nRun the pipeline first (python run_pipeline.py).")
    return path.as_posix()


con = duckdb.connect(str(DB_FILE))

# The curated files, as temporary views we can query with SQL
for view, folder in [("c_orders", "orders_enriched"), ("c_items", "order_items_enriched"),
                     ("c_stock", "stock_events"), ("c_payments", "payments"),
                     ("c_users", "users"), ("c_menu", "menu_items")]:
    con.execute(f"CREATE OR REPLACE TEMP VIEW {view} AS SELECT * FROM read_parquet('{parquet(folder)}')")

# --------------------------------------------------------------------------- dimensions
con.execute("""
CREATE OR REPLACE TABLE dim_date AS
SELECT
    CAST(strftime(d, '%Y%m%d') AS INTEGER)  AS date_key,      -- 20260701
    d                                       AS date,
    dayname(d)                              AS day_name,
    isodow(d)                               AS weekday_num,   -- Monday = 1
    isodow(d) >= 6                          AS is_weekend,
    weekofyear(d)                           AS week_of_year,
    monthname(d)                            AS month_name,
    d IN (SELECT DISTINCT CAST(order_date AS DATE) FROM c_orders) AS is_open_day
FROM (
    SELECT CAST(unnest(generate_series(
        (SELECT MIN(CAST(order_date AS DATE)) FROM c_orders),
        (SELECT MAX(CAST(order_date AS DATE)) FROM c_orders),
        INTERVAL 1 DAY)) AS DATE) AS d
)
""")

con.execute("""
CREATE OR REPLACE TABLE dim_time AS
SELECT h AS time_key,
       printf('%02d:00', h) AS hour_label,
       CASE WHEN h < 11 THEN 'Morning'
            WHEN h = 11 THEN 'Pre-lunch'
            WHEN h < 14 THEN 'Lunch rush'
            WHEN h = 14 THEN 'Post-lunch'
            ELSE 'Evening' END AS day_slot
FROM range(7, 21) t(h)
""")

con.execute("""
CREATE OR REPLACE TABLE dim_user AS
SELECT id AS user_key, role, no_show_count, CAST(joined_at_ist AS DATE) AS joined_date
FROM c_users
""")

con.execute("""
CREATE OR REPLACE TABLE dim_menu_item AS
SELECT id AS item_key, name AS item_name, category, price
FROM c_menu
""")

# --------------------------------------------------------------------------- facts
con.execute("""
CREATE OR REPLACE TABLE fact_orders AS
SELECT
    o.id                                              AS order_id,
    o.order_code,
    CAST(replace(o.order_date, '-', '') AS INTEGER)   AS date_key,
    o.order_hour                                      AS time_key,
    o.user_id                                         AS user_key,
    o.payment_method,
    o.status                                          AS order_status,
    o.payment_status,
    o.total                                           AS total_amount,
    COALESCE(l.lines, 0)                              AS item_lines,
    COALESCE(l.units, 0)                              AS units,
    o.placed_at, o.ready_at, o.completed_at,
    o.minutes_to_start_cooking, o.prep_minutes, o.wait_minutes, o.pickup_delay_minutes,
    o.refunded_amount
FROM c_orders o
LEFT JOIN (SELECT order_id, COUNT(*) AS lines, SUM(quantity) AS units
           FROM c_items GROUP BY order_id) l ON l.order_id = o.id
""")

con.execute("""
CREATE OR REPLACE TABLE fact_order_items AS
SELECT
    order_id,
    CAST(replace(order_date, '-', '') AS INTEGER) AS date_key,
    order_hour                                    AS time_key,
    menu_item_id                                  AS item_key,
    quantity, price AS unit_price, line_total, order_status
FROM c_items
""")

con.execute("""
CREATE OR REPLACE TABLE fact_stock_events AS
SELECT
    id                                            AS stock_event_id,
    CAST(replace(log_date, '-', '') AS INTEGER)   AS date_key,
    log_hour                                      AS time_key,
    menu_item_id                                  AS item_key,
    changed_at_ist                                AS event_time,
    quantity_after,
    CASE WHEN reason = 'daily prep'        THEN 'daily_prep'
         WHEN reason LIKE 'order %'        THEN 'order'
         WHEN reason LIKE 'cancelled%'     THEN 'cancel_restore'
         ELSE 'other' END                         AS event_type,
    is_sell_out
FROM c_stock
""")

con.execute("""
CREATE OR REPLACE TABLE fact_payment_events AS
SELECT
    p.id                                                   AS payment_event_id,
    p.order_id,
    CAST(strftime(p.created_at_ist, '%Y%m%d') AS INTEGER)  AS date_key,
    p.event, p.amount, p.created_at_ist AS event_time, p.reference_hash
FROM c_payments p
""")

# --------------------------------------------------------------------------- integrity checks
checks = [
    ("every order has a valid date",        "SELECT COUNT(*) FROM fact_orders f ANTI JOIN dim_date d USING (date_key)"),
    ("every order has a valid hour",        "SELECT COUNT(*) FROM fact_orders f ANTI JOIN dim_time t USING (time_key)"),
    ("every order has a valid user",        "SELECT COUNT(*) FROM fact_orders f ANTI JOIN dim_user u USING (user_key)"),
    ("every order line has a valid item",   "SELECT COUNT(*) FROM fact_order_items f ANTI JOIN dim_menu_item i USING (item_key)"),
    ("every order line has an order",       "SELECT COUNT(*) FROM fact_order_items f ANTI JOIN fact_orders o USING (order_id)"),
    ("every stock event has a valid item",  "SELECT COUNT(*) FROM fact_stock_events f ANTI JOIN dim_menu_item i USING (item_key)"),
    ("every stock event has a valid hour",  "SELECT COUNT(*) FROM fact_stock_events f ANTI JOIN dim_time t USING (time_key)"),
    ("every stock event has a valid date",  "SELECT COUNT(*) FROM fact_stock_events f ANTI JOIN dim_date d USING (date_key)"),
    ("every payment event has an order",    "SELECT COUNT(*) FROM fact_payment_events f ANTI JOIN fact_orders o USING (order_id)"),
    ("order ids are unique",                "SELECT COUNT(*) - COUNT(DISTINCT order_id) FROM fact_orders"),
    ("order totals equal line totals",      "SELECT COUNT(*) FROM fact_orders o JOIN (SELECT order_id, SUM(line_total) t FROM fact_order_items GROUP BY 1) l USING (order_id) WHERE ABS(o.total_amount - l.t) > 0.01"),
]
print("Star schema built. Integrity checks:")
failed = 0
for name, sql in checks:
    bad = con.execute(sql).fetchone()[0]
    failed += bad != 0
    print(f"  [{'PASS' if bad == 0 else 'FAIL'}] {name}" + ("" if bad == 0 else f"  ({bad} bad rows)"))

print("\nRows per table:")
for (table,) in con.execute("SELECT table_name FROM information_schema.tables WHERE table_type = 'BASE TABLE' ORDER BY table_name").fetchall():
    print(f"  {table:<20} {con.execute(f'SELECT COUNT(*) FROM {table}').fetchone()[0]}")
con.close()
if failed:
    sys.exit(1)
print(f"\nWarehouse file: {DB_FILE}")
