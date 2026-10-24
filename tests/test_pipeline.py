"""
Tests for the data pipeline: cleaning, time conversion, privacy and the quality checks.

Every test builds a tiny dataset by hand (2 orders), so it is clear what the right
answer is. The quality-check tests break ONE thing and confirm the matching check fails.
"""
import sqlite3

import pandas as pd
import pytest

import database
import extract
import quality
import transform


# --------------------------------------------------------------------- test data
def make_raw():
    """Two orders on 1 July 2026. Times are UTC, like the real app database.

    Order 1: UPI, 2 samosas, placed 04:00 UTC (= 09:30 IST), completed.
    Order 2: Cash, 1 chai, placed 05:00 UTC (= 10:30 IST), cancelled.
    """
    return {
        "orders": pd.DataFrame({
            "id": [1, 2], "order_code": ["A001", "A002"], "user_id": [1, 2],
            "total": [30.0, 10.0], "status": ["Completed", "Cancelled"],
            "payment_method": ["UPI", "Cash"], "payment_status": ["Paid", "Pending"],
            "created_at": ["2026-07-01 04:00:00", "2026-07-01 05:00:00"],
        }),
        "order_items": pd.DataFrame({
            "id": [1, 2], "order_id": [1, 2], "menu_item_id": [1, 2],
            "quantity": [2, 1], "price": [15.0, 10.0],
        }),
        "order_status_log": pd.DataFrame({
            "id": [1, 2, 3, 4, 5, 6], "order_id": [1, 1, 1, 1, 2, 2],
            "status": ["Placed", "Preparing", "Ready", "Completed", "Placed", "Cancelled"],
            "changed_at": ["2026-07-01 04:00:00", "2026-07-01 04:02:00", "2026-07-01 04:10:00",
                           "2026-07-01 04:14:00", "2026-07-01 05:00:00", "2026-07-01 05:02:00"],
        }),
        "payment_log": pd.DataFrame({
            "id": [1, 2], "order_id": [1, 1],
            "event": ["UPI reference submitted", "UPI payment confirmed"],
            "amount": [30.0, 30.0], "reference": ["123456789012", None],
            "created_at": ["2026-07-01 04:00:30", "2026-07-01 04:01:30"],
        }),
        "stock_log": pd.DataFrame({
            "id": [1, 2, 3, 4, 5], "menu_item_id": [1, 1, 2, 2, 2],
            "quantity_after": [40, 38, 50, 49, 50],
            "reason": ["daily prep", "order A001", "daily prep", "order A002",
                       "cancelled by student, order A002"],
            "changed_at": ["2026-07-01 02:00:00", "2026-07-01 04:00:00", "2026-07-01 02:00:00",
                           "2026-07-01 05:00:00", "2026-07-01 05:02:00"],
        }),
        "users": pd.DataFrame({"id": [1, 2], "role": ["user", "user"],
                               "no_show_count": [0, 0],
                               "created_at": ["2026-07-01 03:00:00", "2026-07-01 03:00:00"]}),
        "menu_items": pd.DataFrame({"id": [1, 2], "name": ["Samosa", "Chai"],
                                    "price": [15.0, 10.0], "category": ["Snacks", "Beverages"],
                                    "stock": [38, 50], "is_available": [1, 1]}),
    }


def run_checks(raw, source_counts=None):
    """Clean the raw data and run all quality checks. Returns (cleaned, results)."""
    cleaned = transform.clean(raw)
    counts = source_counts or {t: len(df) for t, df in raw.items()}
    return cleaned, quality.run_checks(raw, cleaned, counts)


def failed(results, severity="error"):
    """Names of the checks that failed."""
    return {r["check"] for r in results if r["severity"] == severity and not r["passed"]}


# --------------------------------------------------------------------- cleaning
def test_good_data_passes_every_check():
    _, results = run_checks(make_raw())
    assert failed(results, "error") == set()
    assert failed(results, "warning") == set()


def test_utc_times_become_indian_time():
    cleaned, _ = run_checks(make_raw())
    first = cleaned["orders"].set_index("id").loc[1]
    assert first["created_at_ist"] == pd.Timestamp("2026-07-01 09:30:00")
    assert first["order_hour"] == 9
    assert first["order_date"] == "2026-07-01"
    assert first["day_slot"] == "Morning"


def test_waiting_times_are_calculated_from_the_status_history():
    cleaned, _ = run_checks(make_raw())
    first = cleaned["orders"].set_index("id").loc[1]
    assert first["minutes_to_start_cooking"] == 2   # Placed 04:00 -> Preparing 04:02
    assert first["prep_minutes"] == 8               # Preparing 04:02 -> Ready 04:10
    assert first["wait_minutes"] == 10              # Placed 04:00 -> Ready 04:10
    assert first["pickup_delay_minutes"] == 4       # Ready 04:10 -> Completed 04:14


def test_cancelled_order_has_no_cooking_times():
    cleaned, _ = run_checks(make_raw())
    second = cleaned["orders"].set_index("id").loc[2]
    assert pd.isna(second["wait_minutes"])


def test_payment_events_become_paid_time_and_refund_amount():
    cleaned, _ = run_checks(make_raw())
    first = cleaned["orders"].set_index("id").loc[1]
    assert first["paid_at"] == pd.Timestamp("2026-07-01 09:31:30")
    assert first["refunded_amount"] == 0


def test_upi_reference_numbers_are_hashed():
    cleaned, _ = run_checks(make_raw())
    assert "reference" not in cleaned["payment_log"].columns
    hashes = cleaned["payment_log"]["reference_hash"].dropna()
    assert len(hashes) == 1 and "123456789012" not in hashes.iloc[0]
    assert transform.hash_reference("123456789012") == transform.hash_reference("123456789012")
    assert transform.hash_reference(None) is None


def test_payment_method_spelling_is_standardised():
    raw = make_raw()
    raw["orders"].loc[0, "payment_method"] = " upi "
    cleaned, _ = run_checks(raw)
    assert cleaned["orders"].set_index("id").loc[1, "payment_method"] == "UPI"


def test_curated_daily_summary_counts_only_completed_orders_as_revenue():
    cleaned, _ = run_checks(make_raw())
    daily = transform.build_curated(cleaned)["daily_sales_summary"]
    assert len(daily) == 1
    row = daily.iloc[0]
    assert row["orders"] == 2
    assert row["revenue"] == 30          # the cancelled 10 rupee order is not revenue
    assert row["cancelled_orders"] == 1


def test_duplicate_rows_are_removed_and_reported():
    raw = make_raw()
    raw["orders"] = pd.concat([raw["orders"], raw["orders"].iloc[[0]]], ignore_index=True)
    cleaned, results = run_checks(raw)
    assert len(cleaned["orders"]) == 2
    assert "duplicate_rows_removed_in_cleaning" in failed(results, "warning")


# --------------------------------------------------------------------- quality checks
def test_wrong_order_total_is_caught():
    raw = make_raw()
    raw["orders"].loc[0, "total"] = 99.0
    _, results = run_checks(raw)
    assert "order_total_equals_sum_of_lines" in failed(results)


def test_invalid_status_is_caught():
    raw = make_raw()
    raw["orders"].loc[0, "status"] = "Delivered"
    _, results = run_checks(raw)
    assert "valid_order_status" in failed(results)


def test_unknown_payment_method_is_caught():
    raw = make_raw()
    raw["orders"].loc[0, "payment_method"] = "Bitcoin"
    _, results = run_checks(raw)
    assert "valid_payment_method" in failed(results)


def test_negative_stock_is_caught():
    raw = make_raw()
    raw["stock_log"].loc[1, "quantity_after"] = -5
    _, results = run_checks(raw)
    assert "stock_never_negative" in failed(results)


def test_reused_upi_reference_is_caught():
    raw = make_raw()
    extra = pd.DataFrame({"id": [3], "order_id": [2], "event": ["UPI reference submitted"],
                          "amount": [10.0], "reference": ["123456789012"],
                          "created_at": ["2026-07-01 05:00:30"]})
    raw["payment_log"] = pd.concat([raw["payment_log"], extra], ignore_index=True)
    _, results = run_checks(raw)
    assert "upi_references_unique" in failed(results)


def test_order_ready_before_preparing_is_caught():
    raw = make_raw()
    raw["order_status_log"].loc[2, "changed_at"] = "2026-07-01 03:00:00"   # Ready too early
    _, results = run_checks(raw)
    assert "status_times_in_order" in failed(results)


def test_order_without_items_is_caught():
    raw = make_raw()
    raw["order_items"] = raw["order_items"].iloc[:1]
    _, results = run_checks(raw)
    assert "orders_have_items" in failed(results)


def test_completed_order_without_completed_event_is_caught():
    raw = make_raw()
    raw["order_status_log"] = raw["order_status_log"][raw["order_status_log"]["status"] != "Completed"]
    _, results = run_checks(raw)
    assert "final_status_has_matching_event" in failed(results)


def test_missing_rows_compared_with_the_source_are_caught():
    raw = make_raw()
    counts = {t: len(df) for t, df in raw.items()}
    counts["orders"] = 3     # the source has 3 orders, but only 2 reached the data lake
    _, results = run_checks(raw, counts)
    assert "row_count_matches_source:orders" in failed(results)


def test_orders_at_night_raise_a_warning_not_an_error():
    raw = make_raw()
    raw["orders"].loc[0, "created_at"] = "2026-07-01 20:00:00"   # 01:30 IST
    raw["order_status_log"].loc[0, "changed_at"] = "2026-07-01 20:00:00"
    _, results = run_checks(raw)
    assert "orders_within_opening_hours" in failed(results, "warning")
    assert "orders_within_opening_hours" not in failed(results, "error")


# --------------------------------------------------------------------- extraction and privacy
@pytest.fixture
def source_db(tmp_path):
    """A real (empty) app database with the real table definitions."""
    database.DB_NAME = str(tmp_path / "app.db")
    database.create_tables()
    conn = sqlite3.connect(database.DB_NAME)
    conn.execute("INSERT INTO users (name, email, password_hash, role) "
                 "VALUES ('Real Name', 'real@example.com', 'SECRET-HASH', 'user')")
    conn.commit()
    yield conn
    conn.close()


def test_names_emails_and_password_hashes_are_never_extracted(source_db):
    users = extract.extract_lookups(source_db)["users"]
    assert set(users.columns) == {"id", "role", "no_show_count", "created_at"}
    assert "SECRET-HASH" not in users.to_string()
    assert "real@example.com" not in users.to_string()


def test_orders_near_midnight_utc_belong_to_the_next_indian_day(source_db):
    # 20:00 UTC on 30 June is 01:30 IST on 1 July
    source_db.execute(
        "INSERT INTO orders (order_code, user_id, total, payment_method, created_at) "
        "VALUES ('A001', 1, 10, 'Cash', '2026-06-30 20:00:00')")
    source_db.commit()
    assert extract.list_days(source_db) == ["2026-07-01"]
    assert len(extract.extract_day(source_db, "2026-07-01")["orders"]) == 1
    assert len(extract.extract_day(source_db, "2026-06-30")["orders"]) == 0
