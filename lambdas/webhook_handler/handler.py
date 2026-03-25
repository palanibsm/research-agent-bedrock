"""
Telegram webhook handler — receives messages and invokes Bedrock Agent.

Flow:
  Telegram → API Gateway (POST /webhook) → this Lambda
  → Bedrock Agent (invoke_agent) → streamed response
  → send_telegram_message back to user
"""
import json
import os
import logging
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
BEDROCK_AGENT_ID = os.environ["BEDROCK_AGENT_ID"]
BEDROCK_AGENT_ALIAS_ID = os.environ["BEDROCK_AGENT_ALIAS_ID"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

bedrock_agent_runtime = boto3.client(
    "bedrock-agent-runtime",
    region_name=os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1"),
)


def send_telegram_message(chat_id: str, text: str) -> None:
    """Send a message back to the Telegram user.

    Telegram enforces a 4096-character limit per message, so long reports are
    split into <=4000-character chunks and sent sequentially.
    """
    url = f"{TELEGRAM_API}/sendMessage"
    max_len = 4000
    chunks = [text[i : i + max_len] for i in range(0, len(text), max_len)]
    for chunk in chunks:
        payload = {
            "chat_id": chat_id,
            "text": chunk,
            "parse_mode": "Markdown",
        }
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()


def invoke_bedrock_agent(session_id: str, query: str) -> str:
    """Invoke the Bedrock Agent and collect the streamed response into a string.

    The Bedrock Agent runtime returns an EventStream; we iterate over every
    event and concatenate the decoded bytes from each ``chunk`` event.
    """
    response = bedrock_agent_runtime.invoke_agent(
        agentId=BEDROCK_AGENT_ID,
        agentAliasId=BEDROCK_AGENT_ALIAS_ID,
        sessionId=session_id,
        inputText=query,
    )

    completion = ""
    for event in response["completion"]:
        if "chunk" in event:
            chunk_data = event["chunk"]["bytes"]
            completion += chunk_data.decode("utf-8")

    return completion


def lambda_handler(event, context):
    """Entry point.  Must always return HTTP 200 so Telegram stops retrying."""
    chat_id = ""
    try:
        body = json.loads(event.get("body") or "{}")
        logger.info("Received update: %s", json.dumps(body))

        message = body.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text = message.get("text", "").strip()

        if not chat_id or not text:
            return {"statusCode": 200, "body": "ok"}

        # Handle /start command
        if text.startswith("/start"):
            send_telegram_message(
                chat_id,
                (
                    "*Welcome to the Technical Research Analyst Bot!*\n\n"
                    "Send me any technical research question and I will search:\n"
                    "  - The *web* (live results via Tavily)\n"
                    "  - Our *internal document library* (S3)\n"
                    "  - The *knowledge base* (semantic/vector search)\n\n"
                    "I will return a comprehensive, email-ready research report.\n\n"
                    "*Example queries:*\n"
                    "- _What are the best practices for containerising microservices?_\n"
                    "- _How does Kubernetes horizontal pod autoscaling work?_\n"
                    "- _How can I reduce AWS Lambda cold-start latency?_"
                ),
            )
            return {"statusCode": 200, "body": "ok"}

        # Ignore other bot commands
        if text.startswith("/"):
            send_telegram_message(
                chat_id,
                "Unknown command. Send a research question or /start to see usage.",
            )
            return {"statusCode": 200, "body": "ok"}

        # Acknowledge receipt so the user knows work has started
        send_telegram_message(
            chat_id,
            "Researching your query... This may take 30-60 seconds.",
        )

        # Invoke Bedrock Agent — session_id is the chat_id so context persists
        report = invoke_bedrock_agent(session_id=chat_id, query=text)

        if not report.strip():
            report = (
                "The agent returned an empty response. "
                "Please try rephrasing your query."
            )

        # Deliver the report
        send_telegram_message(chat_id, report)

    except Exception as exc:
        logger.exception("Error processing webhook")
        if chat_id:
            try:
                send_telegram_message(
                    chat_id,
                    f"An error occurred while processing your request: {exc}\n\nPlease try again.",
                )
            except Exception:
                pass  # best-effort; don't let a send failure mask the original error

    return {"statusCode": 200, "body": "ok"}
