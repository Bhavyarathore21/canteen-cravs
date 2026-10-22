"""
run_analysis.py - runs every query in analysis.sql, prints the answers and saves
each result as a CSV file in data_lake/analysis/ (the dashboard will read these).

    python run_analysis.py              run all queries
    python run_analysis.py --only 05    run only queries whose name contains "05"
"""
import argparse
import re
from pathlib import Path

import duckdb
import pandas as pd

HERE = Path(__file__).resolve().parent
DB_FILE = HERE / "canteen_dw.duckdb"
OUT_DIR = HERE.parent / "data_lake" / "analysis"

parser = argparse.ArgumentParser(description="Run the Canteen Cravs analysis queries")
parser.add_argument("--only", help="run only queries whose name contains this text")
args = parser.parse_args()

if not DB_FILE.exists():
    raise SystemExit("Warehouse not found. Run build_warehouse.py first.")

con = duckdb.connect(str(DB_FILE), read_only=True)
text = (HERE / "analysis.sql").read_text(encoding="utf-8")

# The file is split at every "-- name: something" line
parts = re.split(r"^-- name:\s*(\S+)\s*$", text, flags=re.M)
queries = list(zip(parts[1::2], parts[2::2]))

pd.set_option("display.width", 220)
pd.set_option("display.max_columns", 30)
OUT_DIR.mkdir(parents=True, exist_ok=True)

ran = 0
for name, sql in queries:
    if args.only and args.only not in name:
        continue
    question = next((l[3:].strip() for l in sql.splitlines() if l.startswith("-- Q")), "")
    print(f"\n=== {name} ===")
    if question:
        print(question)
    df = con.execute(sql).df()
    print(df.to_string(index=False))
    df.to_csv(OUT_DIR / f"{name}.csv", index=False)
    ran += 1

print(f"\nRan {ran} queries. CSV files saved in {OUT_DIR}")
con.close()
