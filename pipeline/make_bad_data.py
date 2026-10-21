"""
make_bad_data.py - makes a BROKEN copy of the simulated database, so you can
prove that the pipeline's quality checks catch problems.

    python make_bad_data.py
    python run_pipeline.py --source ../backend/canteen_bad.db --lake ../data_lake_test

The real database and your real data lake are not touched.
"""
import shutil
import sqlite3

import config

BAD_DB = config.PROJECT_ROOT / "backend" / "canteen_bad.db"
shutil.copy(config.SOURCE_DB, BAD_DB)
conn = sqlite3.connect(BAD_DB)

# 1. Wrong totals (the total no longer matches the items)
conn.execute("UPDATE orders SET total = total + 500 WHERE id IN (10, 11, 12, 13, 14)")
# 2. A status that does not exist
conn.execute("UPDATE orders SET status = 'Delivered' WHERE id IN (20, 21, 22)")
# 3. Impossible stock
conn.execute("UPDATE stock_log SET quantity_after = -5 WHERE id = 100")
# 4. The same UPI reference used on two different orders
ref = conn.execute(
    "SELECT reference FROM payment_log WHERE reference IS NOT NULL ORDER BY id LIMIT 1"
).fetchone()[0]
conn.execute(
    "UPDATE payment_log SET reference = ? WHERE id = "
    "(SELECT id FROM payment_log WHERE reference IS NOT NULL ORDER BY id LIMIT 1 OFFSET 5)",
    (ref,),
)
# 5. An order that became Ready BEFORE it was Preparing
conn.execute(
    "UPDATE order_status_log SET changed_at = '2026-06-30 00:00:00' "
    "WHERE status = 'Ready' AND order_id = 30"
)
conn.commit()
conn.close()
print(f"Created {BAD_DB.name} with 5 kinds of deliberate errors.")
