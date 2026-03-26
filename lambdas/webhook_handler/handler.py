"""
Telegram webhook handler — provider router with DynamoDB state.
Supports live provider switching and infrastructure management commands.
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
INFRA_MANAGER_FUNCTION = os.environ.get("INFRA_MANAGER_FUNCTION", "research-agent-infra-manager")
DYNAMODB_TABLE         = os.environ.get("DYNAMODB_TABLE_NAME", "research-agent-state")

TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")

bedrock_runtime = boto3.client("bedrock-agent-runtime", region_name=REGION)
lambda_client   = boto3.client("lambda", region_name=REGION)
ddb             = boto3.resource("dynamodb", region_name=REGION)
table           = ddb.Table(DYNAMODB_TABLE)

# 60-second in-process cache for DynamoDB state
_state_cache: dict = {"data": {}, "expires": 0}


# ── DynamoDB state ────────────────────────────────────────────────────────────

def get_state() -> dict:
    now = time.time()
    if _state_cache["data"] and now < _state_cache["expires"]:
        return _state_cache["data"]
    try:
        resp = table.get_item(Key={"pk": "config"})
        state = resp.get("Item", {"pk": "config", "provider": "azure", "aws_status": "active", "azure_status": "active"})
    except Exception as e:
        logger.warning("DynamoDB get_state failed: %s", e)
        state = {"provider": os.environ.get("PROVIDER", "azure"), "aws_status": "active", "azure_status": "active"}
    _state_cache["data"] = state
    _state_cache["expires"] = now + 60
    return state

def set_provider(provider: str):
    try:
        table.update_item(
            Key={"pk": "config"},
            UpdateExpression="SET #p=:p",
            ExpressionAttributeNames={"#p": "provider"},
            ExpressionAttributeValues={":p": provider},
        )
        _state_cache["data"]["provider"] = provider
    except Exception as e:
        logger.warning("DynamoDB set_provider failed: %s", e)

def get_provider() -> str:
    return get_state().get("provider", "azure")


# ── Telegram ──────────────────────────────────────────────────────────────────

def send_telegram_message(chat_id: str, text: str):
    url = f"{TELEGRAM_API}/sendMessage"
    for chunk in [text[i:i+4000] for i in range(0, len(text), 4000)]:
        requests.post(url, json={"chat_id": chat_id, "text": chunk, "parse_mode": "Markdown"}, timeout=10).raise_for_status()


# ── Agent invocation ──────────────────────────────────────────────────────────

def invoke_bedrock_agent(session_id: str, query: str) -> str:
    # Read latest agent IDs from state (may have been updated by activate_aws)
    state = get_state()
    agent_id = state.get("bedrock_agent_id") or BEDROCK_AGENT_ID
    alias_id = state.get("bedrock_agent_alias_id") or BEDROCK_AGENT_ALIAS_ID
    response = bedrock_runtime.invoke_agent(
        agentId=agent_id,
        agentAliasId=alias_id,
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

def invoke_infra_async(action: str, chat_id: str):
    lambda_client.invoke(
        FunctionName=INFRA_MANAGER_FUNCTION,
        InvocationType="Event",
        Payload=json.dumps({"action": action, "chat_id": chat_id}),
    )


# ── Provider labels ───────────────────────────────────────────────────────────

LABELS = {
    "aws":   "AWS Bedrock — Claude 3.5 Sonnet",
    "azure": "Azure OpenAI — GPT-4o",
}
def label(p): return LABELS.get(p, p.upper())


# ── Command handlers ──────────────────────────────────────────────────────────

def handle_start(chat_id):
    state = get_state()
    p = state.get("provider", "azure")
    aws_status = state.get("aws_status", "active")
    azure_status = state.get("azure_status", "active")
    send_telegram_message(chat_id,
        f"👋 *Welcome to Research Analyst Bot!*\n\n"
        f"Active provider: *{label(p)}*\n"
        f"AWS status: {'🟢 Active' if aws_status == 'active' else '🔴 Inactive'}\n"
        f"Azure status: {'🟢 Active' if azure_status == 'active' else '🔴 Inactive'}\n\n"
        f"*Commands:*\n"
        f"  /switch — toggle active provider\n"
        f"  /provider — show current provider & status\n"
        f"  /deactivateaws — delete AWS AI resources (saves ~$50/month)\n"
        f"  /activateaws — recreate AWS AI resources\n"
        f"  /deactivateazure — disable Azure OpenAI\n"
        f"  /activateazure — enable Azure OpenAI\n\n"
        f"_Send any technical question to get a research report._"
    )

def handle_provider(chat_id):
    state = get_state()
    p = state.get("provider", "azure")
    aws_s = state.get("aws_status", "active")
    az_s  = state.get("azure_status", "active")
    send_telegram_message(chat_id,
        f"🔌 *Current provider:* {label(p)}\n\n"
        f"AWS Bedrock: {'🟢 Active' if aws_s == 'active' else '🔴 Inactive'}\n"
        f"Azure OpenAI: {'🟢 Active' if az_s == 'active' else '🔴 Inactive'}"
    )

def handle_switch(chat_id):
    state = get_state()
    current = state.get("provider", "azure")
    new_provider = "azure" if current == "aws" else "aws"
    # Check if target provider is active
    target_status = state.get(f"{new_provider}_status", "active")
    if target_status == "inactive":
        send_telegram_message(chat_id,
            f"⚠️ *{label(new_provider)} is currently inactive.*\n"
            f"Use /activate{'aws' if new_provider == 'aws' else 'azure'} to enable it first."
        )
        return
    set_provider(new_provider)
    send_telegram_message(chat_id,
        f"✅ *Provider switched!*\n\n"
        f"From: {label(current)}\n"
        f"To:   *{label(new_provider)}*"
    )

def handle_deactivate_aws(chat_id):
    state = get_state()
    if state.get("aws_status") == "inactive":
        send_telegram_message(chat_id, "ℹ️ AWS is already inactive.")
        return
    send_telegram_message(chat_id,
        "⏳ *Starting AWS deactivation...*\n"
        "Deleting Bedrock Agent, Knowledge Base, and OpenSearch Serverless.\n"
        "You'll receive updates as each step completes. (~2-3 minutes)"
    )
    invoke_infra_async("deactivate_aws", chat_id)

def handle_activate_aws(chat_id):
    state = get_state()
    if state.get("aws_status") == "active":
        send_telegram_message(chat_id, "ℹ️ AWS is already active.")
        return
    send_telegram_message(chat_id,
        "⏳ *Starting AWS activation...*\n"
        "Creating OpenSearch Serverless + Bedrock Knowledge Base.\n"
        "⚠️ This takes 8-12 minutes. You'll receive progress updates."
    )
    invoke_infra_async("activate_aws", chat_id)

def handle_deactivate_azure(chat_id):
    state = get_state()
    if state.get("azure_status") == "inactive":
        send_telegram_message(chat_id, "ℹ️ Azure is already inactive.")
        return
    send_telegram_message(chat_id, "⏳ *Deactivating Azure OpenAI...*")
    invoke_infra_async("deactivate_azure", chat_id)

def handle_activate_azure(chat_id):
    state = get_state()
    if state.get("azure_status") == "active":
        send_telegram_message(chat_id, "ℹ️ Azure is already active.")
        return
    send_telegram_message(chat_id, "⏳ *Activating Azure OpenAI...*")
    invoke_infra_async("activate_azure", chat_id)


# ── Lambda handler ────────────────────────────────────────────────────────────

COMMANDS = {
    "/start":          handle_start,
    "/provider":       handle_provider,
    "/switch":         handle_switch,
    "/deactivateaws":  handle_deactivate_aws,
    "/activateaws":    handle_activate_aws,
    "/deactivateazure": handle_deactivate_azure,
    "/activateazure":  handle_activate_azure,
}

def lambda_handler(event, context):
    chat_id = ""
    try:
        body    = json.loads(event.get("body", "{}"))
        message = body.get("message", {})
        chat_id = str(message.get("chat", {}).get("id", ""))
        text    = message.get("text", "").strip()

        if not chat_id or not text:
            return {"statusCode": 200, "body": "ok"}

        # Check for command
        for cmd, handler in COMMANDS.items():
            if text.lower().startswith(cmd):
                handler(chat_id)
                return {"statusCode": 200, "body": "ok"}

        # Research query
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
        logger.exception("Webhook error")
        try:
            send_telegram_message(chat_id, f"❌ Error: {str(e)}")
        except Exception:
            pass

    return {"statusCode": 200, "body": "ok"}
