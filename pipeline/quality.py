"""Stage 3: QUALITY CHECKS. Decide whether the data is good enough to publish."""
import json
from datetime import datetime

import config


def _result(name, severity, bad_rows, detail=""):
    bad_rows = int(bad_rows)  # plain Python number, so it can be saved as JSON
    return {"check": name, "severity": severity, "passed": bad_rows == 0,
            "bad_rows": bad_rows, "detail": detail}


def run_checks(raw, cleaned, source_counts, full_run=True):
    o, items = cleaned["orders"], cleaned["order_items"]
    stock, pay = cleaned["stock_log"], cleaned["payment_log"]
    r = []

    # --- completeness: important columns must not be empty
    required = ["id", "order_code", "user_id", "total", "status", "payment_method",
                "payment_status", "created_at_ist"]
    r.append(_result("orders_required_columns_not_null", "error",
                     o[required].isna().any(axis=1).sum()))

    # --- uniqueness
    r.append(_result("order_ids_unique", "error", o["id"].duplicated().sum()))
    r.append(_result("duplicate_rows_removed_in_cleaning", "warning",
                     len(raw["orders"]) - len(o), "duplicates were dropped during cleaning"))
    sub = pay[pay["event"] == "UPI reference submitted"]
    r.append(_result("upi_references_unique", "error", sub["reference_hash"].duplicated().sum(),
                     "the same UPI reference used on two orders"))

    # --- valid values
    r.append(_result("valid_order_status", "error", (~o["status"].isin(config.ORDER_STATUSES)).sum()))
    r.append(_result("valid_payment_method", "error",
                     (~o["payment_method"].isin(config.PAYMENT_METHODS)).sum()))
    r.append(_result("valid_payment_status", "error",
                     (~o["payment_status"].isin(config.PAYMENT_STATUSES)).sum()))
    r.append(_result("amounts_positive", "error",
                     (o["total"] <= 0).sum() + (items["quantity"] <= 0).sum() + (items["price"] <= 0).sum()))

    # --- consistency between tables
    line_sum = items.groupby("order_id")["line_total"].sum().rename("lines")
    joined = o.set_index("id")[["total"]].join(line_sum)
    r.append(_result("orders_have_items", "error", joined["lines"].isna().sum()))
    mismatch = ((joined["total"] - joined["lines"]).abs() > 0.01).sum()
    r.append(_result("order_total_equals_sum_of_lines", "error", mismatch))
    r.append(_result("no_orphan_order_items", "error", (~items["order_id"].isin(o["id"])).sum()))
    r.append(_result("no_orphan_payments", "error", (~pay["order_id"].isin(o["id"])).sum()))

    # --- timelines make sense
    r.append(_result("order_time_matches_placed_event", "error",
                     (o["placed_at"] != o["created_at_ist"]).sum()))

    def out_of_order(earlier, later):
        both = o[earlier].notna() & o[later].notna()
        return (o.loc[both, earlier] > o.loc[both, later]).sum()

    steps = [("placed_at", "preparing_at"), ("preparing_at", "ready_at"), ("ready_at", "completed_at")]
    r.append(_result("status_times_in_order", "error", sum(out_of_order(a, b) for a, b in steps),
                     "e.g. an order Ready before it was Preparing"))
    needs = {"Completed": ["ready_at", "completed_at"], "Cancelled": ["cancelled_at"],
             "Expired": ["ready_at", "expired_at"]}
    missing = sum(((o["status"] == s) & o[c].isna()).sum() for s, cols in needs.items() for c in cols)
    r.append(_result("final_status_has_matching_event", "error", missing))
    r.append(_result("paid_orders_have_payment_event", "error",
                     (o["payment_status"].isin(["Paid", "Refunded"]) & o["paid_at"].isna()).sum()))

    # --- stock and business sense
    r.append(_result("stock_never_negative", "error", (stock["quantity_after"] < 0).sum()))
    r.append(_result("orders_within_opening_hours", "warning",
                     ((o["order_hour"] < config.OPEN_HOUR) | (o["order_hour"] >= config.CLOSE_HOUR)).sum()))

    # --- reconciliation: did we get every row the source has?
    severity = "error" if full_run else "warning"
    for table, source_rows in source_counts.items():
        lake_rows = len(raw[table])
        r.append(_result(f"row_count_matches_source:{table}", severity, abs(source_rows - lake_rows),
                         f"source={source_rows}, data lake={lake_rows}"))
    return r


def summarize(results):
    failed_errors = [x for x in results if x["severity"] == "error" and not x["passed"]]
    failed_warnings = [x for x in results if x["severity"] == "warning" and not x["passed"]]
    return failed_errors, failed_warnings


def print_results(results):
    for x in results:
        status = "PASS" if x["passed"] else ("FAIL" if x["severity"] == "error" else "WARN")
        extra = "" if x["passed"] else f"  ({x['bad_rows']} bad rows) {x['detail']}"
        print(f"  [{status}] {x['check']}{extra}")


def write_report(path, run_info, results):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump({**run_info, "written_at": datetime.now().isoformat(timespec="seconds"),
                   "checks": results}, f, indent=2)
