"""
Telegram webhook handler — receives messages and routes to AWS Bedrock or Azure OpenAI agent.
Provider is stored in SSM Parameter Store and can be switched live via /switch command.
"""
import json
import os
import time
import logging
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TELEGRAM_BOT_TOKEN     = os.environ["TELEGRAM_BOT_TOKEN"]
BEDROCK_AGENT_ID       = os.environ["BEDROCK_AGENT_ID"]
BEDROCK_AGENT_ALIAS_ID = os.environ["BEDROCK_AGENT_ALIAS_ID"]
AZURE_AGENT_FUNCTION   = os.environ.get("AZURE_AGENT_FUNCTION", "research-agent-azure-agent")
SSM_PROVIDER_KEY       = os.environ.get("SSM_PROVIDER_KEY", "/research-agent/provider")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"

REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")
bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
lambda_client   = boto3.client("lambda",                region_name=REGION)
ssm             = boto3.client("ssm",                   region_name=REGION)

# ── SSM-backed provider with 60-second in-process cache ──────────────────────

_provider_cache: dict = {"value": None, "expires": 0}

def get_provider() -> str:
    now = time.time()
    if _provider_cache["value"] and now < _provider_cache["expires"]:
        return _provider_cache["value"]
    try:
        resp = ssm.get_parameter(Name=SSM_PROVIDER_KEY)
        provider = resp["Parameter"]["Value"].lower().strip()
    except ssm.exceptions.ParameterNotFound:
        provider = "aws"
        ssm.put_parameter(Name=SSM_PROVIDER_KEY, Value=provider, Type="String")
    _provider_cache["value"] = provider
    _provider_cache["expires"] = now + 60
    return provider

def set_provider(provider: str) -> None:
    ssm.put_parameter(Name=SSM_PROVIDER_KEY, Value=provider, Type="String", Overwrite=True)
    _provider_cache["value"] = provider
    _provider_cache["expires"] = time.time() + 60


# ── Telegram helper ───────────────────────────────────────────────────────────

def send_telegram_message(chat_id: str, text: str):
    url = f"{TELEGRAM_API}/sendMessage"
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}, timeout=10).raise_for_status()


# ── Agent invocation ──────────────────────────────────────────────────────────

def invoke_bedrock_agent(session_id: str, query: str) -> str:
    response = bedrock_runtime.invoke_agent(
        agentId=BEDROCK_AGENT_ID,
        agentAliasId=BEDROCK_AGENT_ALIAS_ID,
        sessionId=session_id,
        inputText=query,
    )
    result = ""
    for event in response["completion"]:
        if "chunk" in event:
            result += event["chunk"]["bytes"].decode("utf-8")
    return result

def invoke_azure_agent(query: str) -> str:
    response = lambda_client.invoke(
        FunctionName=AZURE_AGENT_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps({"query": query}),
    )
    result = json.loads(response["Payload"].read())
    if "error" in result:
        raise RuntimeError(f"Azure agent error: {result['error']}")
    return result.get("report", "No report generated.")


# ── Provider labels ───────────────────────────────────────────────────────────

PROVIDER_LABELS = {
    "aws":   "AWS Bedrock — Claude 3.5 Sonnet",
    "azure": "Azure OpenAI — GPT-4o",
}

def label(provider: str) -> str:
    return PROVIDER_LABELS.get(provider, provider.upper())


# ── Command handlers ──────────────────────────────────────────────────────────

def handle_start(chat_id: str):
    provider = get_provider()
    send_telegram_message(chat_id,
        f"👋 *Welcome to Research Analyst Bot!*\n\n"
        f"Active provider: *{label(provider)}*\n\n"
        f"*Commands:*\n"
        f"  /switch — toggle between AWS Bedrock and Azure OpenAI\n"
        f"  /provider — show current active provider\n\n"
        f"Send any technical research question to get a comprehensive report.\n"
        f"_Example: What are the best practices for containerizing microservices?_"
    )

def handle_provider(chat_id: str):
    provider = get_provider()
    send_telegram_message(chat_id, f"🔌 Current provider: *{label(provider)}*")

def handle_switch(chat_id: str):
    current = get_provider()
    new_provider = "azure" if current == "aws" else "aws"
    set_provider(new_provider)
    send_telegram_message(chat_id,
        f"✅ *Provider switched!*\n\n"
        f"From: {label(current)}\n"
        f"To:   *{label(new_provider)}*\n\n"
        f"Your next research query will use *{label(new_provider)}*."
    )


# ── Lambda handler ────────────────────────────────────────────────────────────

def lambda_handler(event, context):
    chat_id = ""
    try:
        body    = json.loads(event.get("body", "{}"))
        message = body.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text    = message.get("text", "").strip()

        if not chat_id or not text:
            return {"statusCode": 200, "body": "ok"}

        # ── Commands ──────────────────────────────────────────────────────────
        if text.startswith("/start"):
            handle_start(chat_id)
            return {"statusCode": 200, "body": "ok"}

        if text.startswith("/switch"):
            handle_switch(chat_id)
            return {"statusCode": 200, "body": "ok"}

        if text.startswith("/provider"):
            handle_provider(chat_id)
            return {"statusCode": 200, "body": "ok"}

        # ── Research query ────────────────────────────────────────────────────
        provider = get_provider()
        send_telegram_message(chat_id,
            f"🔍 Researching via *{label(provider)}*... Please wait 30-60 seconds."
        )

        if provider == "azure":
            report = invoke_azure_agent(query=text)
        else:
            report = invoke_bedrock_agent(session_id=chat_id, query=text)

        send_telegram_message(chat_id, report)

    except Exception as e:
        logger.exception("Error processing webhook")
        try:
            send_telegram_message(chat_id, f"❌ An error occurred: {str(e)}")
        except Exception:
            pass

    return {"statusCode": 200, "body": "ok"}
