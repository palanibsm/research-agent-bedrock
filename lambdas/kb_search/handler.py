"""
Action Group Tool: Bedrock Knowledge Base Search.

Called by the Bedrock Agent to perform semantic (vector) search against an
Amazon Bedrock Knowledge Base.  The knowledge base is backed by OpenSearch
Serverless and is populated from the same S3 bucket as the S3 document search
tool, giving complementary keyword + semantic coverage.

Bedrock Agent action-group request / response envelope format:
  https://docs.aws.amazon.com/bedrock/latest/userguide/agents-lambda.html
"""
import json
import os
import logging
import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"),
)
KNOWLEDGE_BASE_ID = os.environ["KNOWLEDGE_BASE_ID"]


def query_knowledge_base(query: str, num_results: int = 5) -> dict:
    """Perform a semantic retrieval query against the Bedrock Knowledge Base.

    Args:
        query: Natural-language search query.
        num_results: Number of top passages to retrieve.

    Returns:
        {
            "results": [
                {"content": ..., "score": ..., "source": ...},
                ...
            ]
        }
    """
    response = bedrock_agent_runtime.retrieve(
        knowledgeBaseId=KNOWLEDGE_BASE_ID,
        retrievalQuery={"text": query},
        retrievalConfiguration={
            "vectorSearchConfiguration": {
                "numberOfResults": num_results,
            }
        },
    )

    results = []
    for result in response.get("retrievalResults", []):
        results.append(
            {
                "content": result.get("content", {}).get("text", ""),
                "score": round(result.get("score", 0.0), 4),
                "source": (
                    result.get("location", {})
                    .get("s3Location", {})
                    .get("uri", "unknown")
                ),
            }
        )

    return {"results": results}


def lambda_handler(event, context):
    """Bedrock Agent action-group entry point."""
    logger.info("KB search invoked: %s", json.dumps(event))

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
            result = query_knowledge_base(query)
        except Exception as exc:
            logger.exception("Knowledge base retrieval failed")
            result = {"error": f"Knowledge base search failed: {exc}"}

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
