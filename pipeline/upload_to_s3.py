"""
upload_to_s3.py - copies your local data lake to an S3 bucket.

    python upload_to_s3.py --bucket YOUR-BUCKET-NAME
    python upload_to_s3.py --bucket YOUR-BUCKET-NAME --dry-run    (only shows what would happen)

Only new or changed files are uploaded, so running it twice is safe and fast.
Your AWS keys come from `aws configure`. Never type keys into this file.
"""
import argparse
import hashlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3
from botocore.exceptions import ClientError, NoCredentialsError

import config


def local_md5(path):
    """Fingerprint of a file. S3 uses the same fingerprint (ETag) for small files."""
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def existing_objects(s3, bucket, prefix):
    """What is already in the bucket: {key: fingerprint}."""
    found = {}
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            found[obj["Key"]] = obj["ETag"].strip('"')
    return found


def main():
    parser = argparse.ArgumentParser(description="Upload the data lake to S3")
    parser.add_argument("--bucket", required=True, help="your S3 bucket name")
    parser.add_argument("--region", default="ap-southeast-2", help="bucket region (Sydney by default)")
    parser.add_argument("--prefix", default="canteen-cravs", help="folder inside the bucket")
    parser.add_argument("--lake", default=str(config.LAKE), help="local data lake folder")
    parser.add_argument("--dry-run", action="store_true", help="show what would be uploaded")
    args = parser.parse_args()

    lake = Path(args.lake)
    if not lake.exists():
        raise SystemExit(f"No data lake at {lake}. Run run_pipeline.py first.")
    files = [p for p in lake.rglob("*") if p.is_file()]

    s3 = boto3.client("s3", region_name=args.region)
    try:
        existing = existing_objects(s3, args.bucket, args.prefix + "/")
    except NoCredentialsError:
        raise SystemExit("No AWS credentials found. Run `aws configure` first.")
    except ClientError as err:
        code = err.response["Error"]["Code"]
        raise SystemExit(
            f"Could not read the bucket ({code}). Check the bucket name, the region "
            f"(--region) and that your IAM user has the S3 policy attached.")

    to_upload, skipped = [], 0
    for path in files:
        key = f"{args.prefix}/{path.relative_to(lake).as_posix()}"
        if existing.get(key) == local_md5(path):
            skipped += 1  # already in S3 and identical
        else:
            to_upload.append((path, key))

    size_mb = sum(p.stat().st_size for p, _ in to_upload) / 1e6
    print(f"{len(files)} local files: {len(to_upload)} to upload ({size_mb:.1f} MB), "
          f"{skipped} already up to date.")
    if args.dry_run or not to_upload:
        print("Dry run: nothing uploaded." if args.dry_run else "Nothing to do.")
        return

    def upload(item):
        path, key = item
        s3.upload_file(str(path), args.bucket, key)

    with ThreadPoolExecutor(max_workers=8) as pool:  # several files at once
        list(pool.map(upload, to_upload))
    print(f"Uploaded {len(to_upload)} files to s3://{args.bucket}/{args.prefix}/")


if __name__ == "__main__":
    main()
