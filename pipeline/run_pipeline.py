"""
run_pipeline.py - the Canteen Cravs data pipeline.

    SOURCE DB --extract--> RAW --clean--> CLEANED --quality checks--> CURATED

Run from the pipeline folder:

    python run_pipeline.py                     process every day
    python run_pipeline.py --date 2026-07-15   extract just one day
    python run_pipeline.py --lake data_lake_test --source ../backend/canteen_bad.db
"""
import argparse
import sys
import time
from datetime import datetime
from pathlib import Path

import pandas as pd

import config
import extract
import quality
import transform


def save(df, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(path, index=False)


def read_raw(raw_dir, table):
    files = sorted((raw_dir / table).rglob("*.parquet"))
    if not files:
        raise SystemExit(f"No raw data for {table}. Run the pipeline without --date first.")
    return pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)


def main():
    parser = argparse.ArgumentParser(description="Canteen Cravs data pipeline")
    parser.add_argument("--source", default=str(config.SOURCE_DB), help="app database to read")
    parser.add_argument("--lake", default=str(config.LAKE), help="data lake folder to write")
    parser.add_argument("--date", help="extract only this day (YYYY-MM-DD)")
    args = parser.parse_args()

    lake = Path(args.lake)
    raw_dir, cleaned_dir, curated_dir = lake / "raw", lake / "cleaned", lake / "curated"
    started = time.time()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"Pipeline run {run_id}")
    print(f"  source: {args.source}\n  lake:   {lake}")

    # ------------------------------------------------ 1. EXTRACT  ->  RAW
    conn = extract.connect(args.source)
    days = [args.date] if args.date else extract.list_days(conn)
    print(f"\n[1/4] Extracting {len(days)} day(s) into raw/ ...")
    for n, day in enumerate(days, start=1):
        tables = extract.extract_day(conn, day)
        for name, df in tables.items():
            if not df.empty:  # re-running a day simply overwrites its file (safe to repeat)
                part = "log_date" if name == "stock_log" else "order_date"
                save(df, raw_dir / name / f"{part}={day}" / "data.parquet")
        if n % 15 == 0 or n == len(days):
            print(f"      {n}/{len(days)} days done")
    for name, df in extract.extract_lookups(conn).items():
        save(df, raw_dir / name / "data.parquet")
    source_counts = extract.count_rows(conn)
    conn.close()

    # ------------------------------------------------ 2. TRANSFORM
    print("\n[2/4] Cleaning ...")
    raw = {t: read_raw(raw_dir, t) for t in config.DAILY_TABLES + config.LOOKUP_TABLES}
    cleaned = transform.clean(raw)
    print(f"      {len(cleaned['orders'])} orders cleaned "
          f"({len(raw['orders']) - len(cleaned['orders'])} duplicates removed)")

    # ------------------------------------------------ 3. QUALITY CHECKS
    print("\n[3/4] Quality checks:")
    results = quality.run_checks(raw, cleaned, source_counts, full_run=args.date is None)
    quality.print_results(results)
    errors, warnings = quality.summarize(results)
    status = "FAILED" if errors else ("PASSED_WITH_WARNINGS" if warnings else "PASSED")

    run_info = {
        "run_id": run_id, "status": status, "days_extracted": len(days),
        "source_rows": source_counts,
        "raw_rows": {t: len(df) for t, df in raw.items()},
        "failed_errors": len(errors), "warnings": len(warnings),
        "seconds": round(time.time() - started, 1),
    }
    report_path = lake / "reports" / f"run_{run_id}.json"
    quality.write_report(report_path, run_info, results)

    if errors:
        print(f"\nPipeline FAILED: {len(errors)} quality check(s) failed.")
        print("Nothing was published to cleaned/ or curated/. Report:", report_path)
        sys.exit(1)

    # ------------------------------------------------ 4. LOAD  ->  CLEANED + CURATED
    print("\n[4/4] Publishing ...")
    for name, df in cleaned.items():
        save(df, cleaned_dir / name / "data.parquet")
    curated = transform.build_curated(cleaned)
    for name, df in curated.items():
        save(df, curated_dir / name / "data.parquet")
        print(f"      curated/{name}: {len(df)} rows")

    print(f"\nPipeline {status} in {time.time() - started:.1f}s. Report: {report_path}")


if __name__ == "__main__":
    main()
