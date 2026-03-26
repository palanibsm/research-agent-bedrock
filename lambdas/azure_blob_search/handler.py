"""
Azure Blob Storage document search — keyword-frequency matching.
Uses Azure Blob Storage REST API directly (no SDK, no native binary deps).
"""
import os
import json
import logging
import hashlib
import hmac
import base64
import datetime
import urllib.parse
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

AZURE_STORAGE_ACCOUNT = os.environ.get("AZURE_STORAGE_ACCOUNT_NAME", "")
AZURE_STORAGE_KEY = os.environ.get("AZURE_STORAGE_ACCOUNT_KEY", "")
AZURE_CONTAINER = os.environ.get("AZURE_STORAGE_CONTAINER_NAME", "research-docs")


def _get_auth_header(method: str, resource: str, content_type: str = "") -> dict:
    """Generate Azure Blob Storage shared key auth header."""
    date_str = datetime.datetime.utcnow().strftime("%a, %d %b %Y %H:%M:%S GMT")
    string_to_sign = (
        f"{method}\n\n\n\n\n{content_type}\n\n\n\n\n\n\n"
        f"x-ms-date:{date_str}\nx-ms-version:2020-04-08\n"
        f"/{AZURE_STORAGE_ACCOUNT}{resource}"
    )
    key = base64.b64decode(AZURE_STORAGE_KEY)
    sig = base64.b64encode(
        hmac.new(key, string_to_sign.encode("utf-8"), hashlib.sha256).digest()
    ).decode()
    return {
        "x-ms-date": date_str,
        "x-ms-version": "2020-04-08",
        "Authorization": f"SharedKey {AZURE_STORAGE_ACCOUNT}:{sig}",
    }


def _list_blobs() -> list[str]:
    """List all blobs in the container."""
    resource = f"/{AZURE_CONTAINER}"
    url = f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net{resource}?restype=container&comp=list"
    headers = _get_auth_header("GET", f"/{AZURE_CONTAINER}\ncomp:list\nrestype:container")
    resp = requests.get(url, headers=headers, timeout=15)
    if not resp.ok:
        logger.warning("List blobs failed: %s", resp.text[:200])
        return []
    # Parse XML response for blob names
    import xml.etree.ElementTree as ET
    root = ET.fromstring(resp.text)
    return [b.find("Name").text for b in root.iter("Blob") if b.find("Name") is not None]


def _get_blob(name: str) -> str:
    """Download a blob's text content."""
    resource = f"/{AZURE_CONTAINER}/{urllib.parse.quote(name)}"
    url = f"https://{AZURE_STORAGE_ACCOUNT}.blob.core.windows.net{resource}"
    headers = _get_auth_header("GET", resource)
    resp = requests.get(url, headers=headers, timeout=15)
    resp.raise_for_status()
    return resp.text


def search_documents(query: str) -> dict:
    """Keyword-frequency search across all blobs in the container."""
    query_terms = query.lower().split()
    blob_names = _list_blobs()
    matches = []

    for name in blob_names:
        try:
            content = _get_blob(name)
            content_lower = content.lower()
            score = sum(content_lower.count(term) for term in query_terms)
            if score > 0:
                snippet = ""
                for term in query_terms:
                    idx = content_lower.find(term)
                    if idx != -1:
                        snippet = content[max(0, idx - 100): idx + 700]
                        break
                matches.append({"document": name, "relevance_score": score, "snippet": snippet})
        except Exception as e:
            logger.warning("Could not read blob %s: %s", name, e)

    matches.sort(key=lambda x: x["relevance_score"], reverse=True)
    return {"matches": matches[:3], "source": "Azure Blob Storage"}


def lambda_handler(event, context):
    """Action group tool entry point (also callable directly)."""
    action = event.get("actionGroup", "")
    api_path = event.get("apiPath", "")
    parameters = event.get("parameters", [])
    query = next((p["value"] for p in parameters if p["name"] == "query"), "")

    if not query:
        result = {"error": "No query provided"}
    else:
        result = search_documents(query)

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action,
            "apiPath": api_path,
            "httpMethod": event.get("httpMethod", "GET"),
            "httpStatusCode": 200,
            "responseBody": {"application/json": {"body": json.dumps(result)}},
        },
    }
