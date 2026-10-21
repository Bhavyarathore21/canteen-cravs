"""Settings shared by every stage of the pipeline."""
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SOURCE_DB = PROJECT_ROOT / "backend" / "canteen_sim.db"
LAKE = PROJECT_ROOT / "data_lake"  # raw/, cleaned/, curated/, reports/ live inside

LOCAL_TZ = "Asia/Kolkata"  # the app stores UTC, analysis uses Indian time

# Allowed values (the quality checks compare the data against these)
ORDER_STATUSES = {"Placed", "Preparing", "Ready", "Completed", "Cancelled", "Expired"}
PAYMENT_METHODS = {"UPI", "Cash"}
PAYMENT_STATUSES = {"Pending", "Submitted", "Paid", "Refund Pending", "Refunded"}

OPEN_HOUR = 7    # orders outside 07:00 to 19:00 IST look suspicious
CLOSE_HOUR = 19

# Tables that grow every day (extracted one day at a time) and tables that are
# small lookups (copied fully every run)
DAILY_TABLES = ["orders", "order_items", "order_status_log", "payment_log", "stock_log"]
LOOKUP_TABLES = ["users", "menu_items"]
