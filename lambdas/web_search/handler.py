"""
Action Group Tool: Web Search via Tavily API.

Called by the Bedrock Agent when it needs current information from the web.
The agent passes a ``query`` parameter; this function calls the Tavily search
API and returns a structured dict with an AI-generated answer and the top
individual results.

Bedrock Agent action-group request / response envelope format:
  https://docs.aws.amazon.com/bedrock/latest/userguide/agents-lambda.html
"""
import json
import os
import logging
from tavily import TavilyClient

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Initialised once at cold-start so the API key is read only once.
tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])


def search_web(query: str, max_results: int = 5) -> dict:
    """Perform a deep web search and return a structured result dict.

    Args:
        query: The natural-language search query.
        max_results: Maximum number of individual web results to return.

    Returns:
        {
            "answer": "<Tavily AI-generated summary answer>",
            "results": [
                {"title": ..., "url": ..., "content": ...},
                ...
            ]
        }
    """
    response = tavily.search(
        query=query,
        search_depth="advanced",
        max_results=max_results,
        include_answer=True,
        include_raw_content=False,
    )

    results = []
    for r in response.get("results", []):
        results.append(
            {
                "title": r.get("title", ""),
                "url": r.get("url", ""),
                # Truncate individual snippets to keep the payload manageable
                "content": r.get("content", "")[:500],
            }
        )

    return {
        "answer": response.get("answer", ""),
        "results": results,
    }


def lambda_handler(event, context):
    """Bedrock Agent action-group entry point."""
    logger.info("Web search invoked: %s", json.dumps(event))

    action = event.get("actionGroup", "")
    api_path = event.get("apiPath", "")
    http_method = event.get("httpMethod", "GET")
    parameters = event.get("parameters", [])

    # Extract the mandatory ``query`` parameter sent by the agent
    query = ""
    for param in parameters:
        if param.get("name") == "query":
            query = param.get("value", "")
            break

    if not query:
        result = {"error": "No query parameter provided. Pass 'query' to use this tool."}
    else:
        try:
            result = search_web(query)
        except Exception as exc:
            logger.exception("Tavily search failed")
            result = {"error": f"Web search failed: {exc}"}

    # Bedrock Agent expects this exact response envelope
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
