#!/usr/bin/env python3
"""
Upload sample documents to S3 for the Research Analyst Bot.

Usage:
    python scripts/upload_sample_docs.py

The script reads S3_BUCKET_NAME and AWS_REGION from the .env file at the
repository root, then uploads every file from the sample_docs/ directory
to the bucket under the docs/ prefix.
"""
import os
import sys
from pathlib import Path
import boto3
from botocore.exceptions import ClientError, NoCredentialsError
from dotenv import load_dotenv

# Resolve paths relative to this script so it works from any cwd
REPO_ROOT = Path(__file__).parent.parent
load_dotenv(REPO_ROOT / ".env.local")
load_dotenv(REPO_ROOT / ".env")

BUCKET = os.environ.get("S3_BUCKET_NAME", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")

if not BUCKET:
    print("ERROR: S3_BUCKET_NAME is not set in .env")
    print("  1. Run `cdk deploy` first")
    print("  2. Copy the DocsBucketName output into .env")
    sys.exit(1)

try:
    s3 = boto3.client("s3", region_name=REGION)
    # Quick connectivity check
    s3.head_bucket(Bucket=BUCKET)
except NoCredentialsError:
    print("ERROR: No AWS credentials found. Run `aws configure` or set environment variables.")
    sys.exit(1)
except ClientError as exc:
    error_code = exc.response["Error"]["Code"]
    if error_code in ("403", "NoSuchBucket"):
        print(f"ERROR: Bucket '{BUCKET}' does not exist or you lack access.")
        print("  Make sure the CDK stack has been deployed and S3_BUCKET_NAME is correct.")
    else:
        print(f"ERROR: {exc}")
    sys.exit(1)

docs_dir = REPO_ROOT / "sample_docs"
if not docs_dir.is_dir():
    print(f"ERROR: sample_docs/ directory not found at {docs_dir}")
    sys.exit(1)

doc_files = [f for f in docs_dir.iterdir() if f.is_file()]
if not doc_files:
    print(f"No files found in {docs_dir}")
    sys.exit(0)

print(f"Uploading {len(doc_files)} document(s) to s3://{BUCKET}/docs/\n")

uploaded = 0
failed = 0
for doc_file in sorted(doc_files):
    key = f"docs/{doc_file.name}"
    try:
        s3.upload_file(
            str(doc_file),
            BUCKET,
            key,
            ExtraArgs={"ContentType": "text/plain"},
        )
        print(f"  [OK] {doc_file.name}  ->  s3://{BUCKET}/{key}")
        uploaded += 1
    except ClientError as exc:
        print(f"  [FAIL] {doc_file.name}: {exc}")
        failed += 1

print(f"\nDone. {uploaded} uploaded, {failed} failed.")
if failed:
    sys.exit(1)
