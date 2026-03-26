"""
Action Group Tool: Web Search via Tavily API.

Uses raw HTTP requests instead of the tavily-python SDK to avoid
native binary dependencies (tiktoken) that break on Lambda Linux runtime.
"""
import json
import os
import logging
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TAVILY_API_KEY = os.environ["TAVILY_API_KEY"]
TAVILY_URL = "https://api.tavily.com/search"


def search_web(query: str, max_results: int = 5) -> dict:
    payload = {
        "api_key": TAVILY_API_KEY,
        "query": query,
        "search_depth": "advanced",
        "max_results": max_results,
        "include_answer": True,
        "include_raw_content": False,
    }
    resp = requests.post(TAVILY_URL, json=payload, timeout=30)
    resp.raise_for_status()
    response = resp.json()

    results = []
    for r in response.get("results", []):
        results.append({
            "title": r.get("title", ""),
            "url": r.get("url", ""),
            "content": r.get("content", "")[:500],
        })

    return {
        "answer": response.get("answer", ""),
        "results": results,
    }


def lambda_handler(event, context):
    logger.info("Web search invoked: %s", json.dumps(event))

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
        result = {"error": "No query parameter provided."}
    else:
        try:
            result = search_web(query)
        except Exception as exc:
            logger.exception("Tavily search failed")
            result = {"error": f"Web search failed: {exc}"}

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
