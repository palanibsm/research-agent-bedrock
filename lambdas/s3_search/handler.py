"""
Action Group Tool: S3 Document Search.

Called by the Bedrock Agent to search technical documents stored in S3 under
the ``docs/`` prefix.  Uses simple term-frequency scoring so the most relevant
documents surface first; the top 3 matches are returned with a contextual
snippet for each.

Bedrock Agent action-group request / response envelope format:
  https://docs.aws.amazon.com/bedrock/latest/userguide/agents-lambda.html
"""
import json
import os
import logging
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

s3 = boto3.client("s3")
S3_BUCKET = os.environ["S3_BUCKET_NAME"]


def list_documents() -> list[str]:
    """Return all object keys stored under the ``docs/`` prefix."""
    response = s3.list_objects_v2(Bucket=S3_BUCKET, Prefix="docs/")
    return [obj["Key"] for obj in response.get("Contents", [])]


def search_documents(query: str) -> dict:
    """Keyword-frequency search across all S3 text documents.

    Scoring: for each document the total occurrences of every query term
    (case-insensitive) are summed.  Documents with a score > 0 are included.
    A contextual snippet of up to ~800 characters is extracted starting at the
    first occurrence of any query term.

    Args:
        query: Natural-language search query.

    Returns:
        {"matches": [{"document": ..., "relevance_score": ..., "snippet": ...}, ...]}
        Up to 3 documents sorted by descending relevance score.
    """
    query_terms = query.lower().split()
    doc_keys = list_documents()
    matches = []

    for key in doc_keys:
        try:
            obj = s3.get_object(Bucket=S3_BUCKET, Key=key)
            content = obj["Body"].read().decode("utf-8")
            content_lower = content.lower()

            score = sum(content_lower.count(term) for term in query_terms)
            if score == 0:
                continue

            # Extract a snippet anchored at the first matching term
            snippet = ""
            for term in query_terms:
                idx = content_lower.find(term)
                if idx != -1:
                    start = max(0, idx - 100)
                    end = min(len(content), idx + 700)
                    snippet = content[start:end].strip()
                    break

            matches.append(
                {
                    "document": key.split("/")[-1],
                    "relevance_score": score,
                    "snippet": snippet,
                }
            )
        except Exception as exc:
            logger.warning("Could not read %s: %s", key, exc)

    matches.sort(key=lambda x: x["relevance_score"], reverse=True)
    return {"matches": matches[:3]}


def lambda_handler(event, context):
    """Bedrock Agent action-group entry point."""
    logger.info("S3 search invoked: %s", json.dumps(event))

    action = event.get("actionGroup", "")
    api_path = event.get("apiPath", "")
    http_method = event.get("httpMethod", "GET")
    parameters = event.get("parameters", [])

    query = ""
    for param in parameters:
        if param.get("name") == "query":
            query = param.get("value", "")
            break

    if not query:
        result = {"error": "No query parameter provided. Pass 'query' to use this tool."}
    else:
        try:
            result = search_documents(query)
        except Exception as exc:
            logger.exception("S3 search failed")
            result = {"error": f"S3 search failed: {exc}"}

    return {
        "messageVersion": "1.0",
        "response": {
            "actionGroup": action,
            "apiPath": api_path,
            "httpMethod": http_method,
            "httpStatusCode": 200,
            "responseBody": {
                "application/json": {
                    "body": json.dumps(result)
                }
            },
        },
    }
