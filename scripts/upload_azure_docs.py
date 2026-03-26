#!/usr/bin/env python3
"""Upload sample documents to Azure Blob Storage."""
import os, sys, hashlib, hmac, base64, datetime, urllib.parse
from pathlib import Path
import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")
load_dotenv(Path(__file__).parent.parent / ".env")

ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
KEY     = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")
CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "research-docs")

if not ACCOUNT or not KEY:
    print("ERROR: AZURE_STORAGE_ACCOUNT_NAME and AZURE_STORAGE_ACCOUNT_KEY must be set in .env.local")
    sys.exit(1)

def auth_header(method, resource, content_type="text/plain", content_length=0):
    date_str = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    content_len_str = str(content_length) if content_length else ""
    string_to_sign = (
        f"{method}\n\n\n{content_len_str}\n\n{content_type}\n\n\n\n\n\n\n"
        f"x-ms-blob-type:BlockBlob\nx-ms-date:{date_str}\nx-ms-version:2020-04-08\n"
        f"/{ACCOUNT}{resource}"
    )
    key = base64.b64decode(KEY)
    sig = base64.b64encode(hmac.new(key, string_to_sign.encode(), hashlib.sha256).digest()).decode()
    return {
        "x-ms-date": date_str,
        "x-ms-version": "2020-04-08",
        "x-ms-blob-type": "BlockBlob",
        "Content-Type": content_type,
        "Authorization": f"SharedKey {ACCOUNT}:{sig}",
    }

# Create container if not exists
container_url = f"https://{ACCOUNT}.blob.core.windows.net/{CONTAINER}?restype=container"
date_str = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
string_to_sign = f"PUT\n\n\n\n\n\n\n\n\n\n\n\nx-ms-date:{date_str}\nx-ms-version:2020-04-08\n/{ACCOUNT}/{CONTAINER}\nrestype:container"
key_bytes = base64.b64decode(KEY)
sig = base64.b64encode(hmac.new(key_bytes, string_to_sign.encode(), hashlib.sha256).digest()).decode()
r = requests.put(container_url, headers={
    "x-ms-date": date_str, "x-ms-version": "2020-04-08",
    "Authorization": f"SharedKey {ACCOUNT}:{sig}",
})
print(f"Container: {r.status_code} ({'created' if r.status_code == 201 else 'already exists' if r.status_code == 409 else 'error'})")

# Upload docs
docs_dir = Path(__file__).parent.parent / "sample_docs"
for doc in docs_dir.glob("*"):
    if not doc.is_file():
        continue
    content = doc.read_bytes()
    resource = f"/{CONTAINER}/{doc.name}"
    url = f"https://{ACCOUNT}.blob.core.windows.net{resource}"
    headers = auth_header("PUT", resource, "text/plain", len(content))
    r = requests.put(url, headers=headers, data=content, timeout=30)
    status = "OK" if r.status_code in (200, 201) else "FAIL"
    print(f"  {status} {doc.name} ({r.status_code})")

print("\nDone.")
