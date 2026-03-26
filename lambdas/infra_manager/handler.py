"""
Infrastructure Manager Lambda.
Handles async deactivate/activate for AWS and Azure resources.
Called asynchronously by webhook_handler; sends Telegram updates directly.

Actions: deactivate_aws, activate_aws, deactivate_azure, activate_azure
"""
import os
import json
import time
import logging
import boto3
import requests

logger = logging.getLogger()
logger.setLevel(logging.INFO)

TELEGRAM_BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
TELEGRAM_API = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}"
DYNAMODB_TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "research-agent-state")
REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-southeast-1")
S3_BUCKET = os.environ.get("S3_BUCKET_NAME", "")
AGENT_ROLE_ARN = os.environ.get("BEDROCK_AGENT_ROLE_ARN", "")

# Azure management
AZURE_TENANT_ID = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
AZURE_SUBSCRIPTION_ID = os.environ.get("AZURE_SUBSCRIPTION_ID", "")
AZURE_RESOURCE_GROUP = os.environ.get("AZURE_RESOURCE_GROUP", "")
AZURE_OPENAI_RESOURCE = os.environ.get("AZURE_OPENAI_RESOURCE_NAME", "")
AZURE_OPENAI_DEPLOYMENT = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o")

ddb = boto3.resource("dynamodb", region_name=REGION)
table = ddb.Table(DYNAMODB_TABLE)
bedrock_agent = boto3.client("bedrock-agent", region_name=REGION)
oss = boto3.client("opensearchserverless", region_name=REGION)


# ── Telegram ──────────────────────────────────────────────────────────────────

def tg(chat_id: str, text: str):
    try:
        requests.post(f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
            timeout=10).raise_for_status()
    except Exception as e:
        logger.warning("Telegram send failed: %s", e)


# ── DynamoDB state helpers ────────────────────────────────────────────────────

def get_state() -> dict:
    resp = table.get_item(Key={"pk": "config"})
    return resp.get("Item", {})

def set_state(**kwargs):
    expr = "SET " + ", ".join(f"#{k}=:{k}" for k in kwargs)
    names = {f"#{k}": k for k in kwargs}
    values = {f":{k}": v for k, v in kwargs.items()}
    table.update_item(
        Key={"pk": "config"},
        UpdateExpression=expr,
        ExpressionAttributeNames=names,
        ExpressionAttributeValues=values,
    )


# ── AWS Deactivate ────────────────────────────────────────────────────────────

def deactivate_aws(chat_id: str):
    tg(chat_id, "🔴 *Deactivating AWS resources...*\nThis will delete the Bedrock Knowledge Base and OpenSearch Serverless collection.")

    state = get_state()
    kb_id = state.get("knowledge_base_id", os.environ.get("KNOWLEDGE_BASE_ID", ""))
    agent_id = state.get("bedrock_agent_id", os.environ.get("BEDROCK_AGENT_ID", ""))
    alias_id = state.get("bedrock_agent_alias_id", os.environ.get("BEDROCK_AGENT_ALIAS_ID", ""))

    # Step 1: Delete Bedrock Agent Alias
    if agent_id and alias_id:
        try:
            tg(chat_id, "⏳ Deleting Bedrock Agent alias...")
            bedrock_agent.delete_agent_alias(agentId=agent_id, agentAliasId=alias_id)
            time.sleep(3)
        except Exception as e:
            logger.warning("Delete agent alias: %s", e)

    # Step 2: Delete Bedrock Agent
    if agent_id:
        try:
            tg(chat_id, "⏳ Deleting Bedrock Agent...")
            bedrock_agent.delete_agent(agentId=agent_id, skipResourceInUseCheck=True)
            time.sleep(5)
        except Exception as e:
            logger.warning("Delete agent: %s", e)

    # Step 3: Delete data sources + Knowledge Base
    if kb_id:
        try:
            tg(chat_id, "⏳ Deleting Bedrock Knowledge Base...")
            ds_resp = bedrock_agent.list_data_sources(knowledgeBaseId=kb_id)
            for ds in ds_resp.get("dataSourceSummaries", []):
                bedrock_agent.delete_data_source(knowledgeBaseId=kb_id, dataSourceId=ds["dataSourceId"])
            time.sleep(3)
            bedrock_agent.delete_knowledge_base(knowledgeBaseId=kb_id)
            time.sleep(5)
        except Exception as e:
            logger.warning("Delete KB: %s", e)

    # Step 4: Delete OpenSearch Serverless collections
    try:
        tg(chat_id, "⏳ Deleting OpenSearch Serverless collection (this saves ~$50/month)...")
        colls = oss.list_collections(filters={"status": ["ACTIVE", "CREATING"]})
        for c in colls.get("collectionSummaries", []):
            if "bedrock" in c.get("name", "").lower() or "kb" in c.get("name", "").lower():
                oss.delete_collection(id=c["id"])
                tg(chat_id, f"  Deleted collection: `{c['name']}`")
    except Exception as e:
        logger.warning("Delete OpenSearch: %s", e)

    # Update state
    set_state(aws_status="inactive", provider="azure")
    tg(chat_id,
        "✅ *AWS deactivated!*\n\n"
        "Deleted: Bedrock Agent, Knowledge Base, OpenSearch Serverless\n"
        "Kept: Lambda functions, API Gateway, S3 bucket (minimal cost)\n\n"
        "Provider switched to *Azure OpenAI*.\n"
        "Use /activateaws to restore AWS resources when needed."
    )


# ── AWS Activate ──────────────────────────────────────────────────────────────

def activate_aws(chat_id: str):
    tg(chat_id,
        "🟢 *Activating AWS resources...*\n"
        "Creating OpenSearch Serverless + Bedrock KB.\n"
        "⚠️ This takes 8-12 minutes. I'll send updates."
    )

    state = get_state()
    agent_role_arn = AGENT_ROLE_ARN or state.get("bedrock_agent_role_arn", "")
    collection_name = "research-agent-kb"

    # Step 1: Create encryption security policy
    try:
        tg(chat_id, "⏳ Creating OpenSearch security policies...")
        oss.create_security_policy(
            name="research-agent-enc",
            type="encryption",
            policy=json.dumps({
                "Rules": [{"Resource": [f"collection/{collection_name}"], "ResourceType": "collection"}],
                "AWSOwnedKey": True,
            }),
        )
    except oss.exceptions.ConflictException:
        pass
    except Exception as e:
        logger.warning("Enc policy: %s", e)

    # Network policy
    try:
        oss.create_security_policy(
            name="research-agent-net",
            type="network",
            policy=json.dumps([{
                "Rules": [
                    {"Resource": [f"collection/{collection_name}"], "ResourceType": "collection"},
                    {"Resource": [f"collection/{collection_name}"], "ResourceType": "dashboard"},
                ],
                "AllowFromPublic": True,
            }]),
        )
    except oss.exceptions.ConflictException:
        pass
    except Exception as e:
        logger.warning("Net policy: %s", e)

    # Data access policy
    try:
        oss.create_access_policy(
            name="research-agent-data",
            type="data",
            policy=json.dumps([{
                "Rules": [
                    {
                        "Resource": [f"collection/{collection_name}"],
                        "Permission": ["aoss:CreateCollectionItems", "aoss:DeleteCollectionItems", "aoss:UpdateCollectionItems", "aoss:DescribeCollectionItems"],
                        "ResourceType": "collection",
                    },
                    {
                        "Resource": [f"index/{collection_name}/*"],
                        "Permission": ["aoss:CreateIndex", "aoss:DeleteIndex", "aoss:UpdateIndex", "aoss:DescribeIndex", "aoss:ReadDocument", "aoss:WriteDocument"],
                        "ResourceType": "index",
                    },
                ],
                "Principal": [agent_role_arn] if agent_role_arn else ["*"],
            }]),
        )
    except oss.exceptions.ConflictException:
        pass
    except Exception as e:
        logger.warning("Data policy: %s", e)

    # Step 2: Create collection
    try:
        tg(chat_id, "⏳ Creating OpenSearch Serverless collection (5-8 min)...")
        resp = oss.create_collection(name=collection_name, type="VECTORSEARCH")
        collection_id = resp["createCollectionDetail"]["id"]
    except oss.exceptions.ConflictException:
        colls = oss.list_collections()
        collection_id = next(
            (c["id"] for c in colls["collectionSummaries"] if c["name"] == collection_name), None
        )
        if not collection_id:
            tg(chat_id, "❌ Could not create or find the OpenSearch collection.")
            return
    except Exception as e:
        tg(chat_id, f"❌ Failed to create OpenSearch collection: {e}")
        return

    # Poll until active
    for _ in range(40):
        time.sleep(15)
        try:
            status = oss.batch_get_collection(ids=[collection_id])["collectionDetails"][0]["status"]
            if status == "ACTIVE":
                tg(chat_id, "✅ OpenSearch collection is active!")
                break
            elif status == "FAILED":
                tg(chat_id, "❌ OpenSearch collection creation failed.")
                return
        except Exception as e:
            logger.warning("Poll collection: %s", e)
    else:
        tg(chat_id, "❌ OpenSearch collection timed out.")
        return

    collection_endpoint = oss.batch_get_collection(ids=[collection_id])["collectionDetails"][0]["collectionEndpoint"]

    # Step 3: Create Bedrock Knowledge Base
    try:
        tg(chat_id, "⏳ Creating Bedrock Knowledge Base...")
        kb_resp = bedrock_agent.create_knowledge_base(
            name="ResearchAnalystKB",
            roleArn=agent_role_arn,
            knowledgeBaseConfiguration={
                "type": "VECTOR",
                "vectorKnowledgeBaseConfiguration": {
                    "embeddingModelArn": f"arn:aws:bedrock:{REGION}::foundation-model/cohere.embed-english-v3",
                },
            },
            storageConfiguration={
                "type": "OPENSEARCH_SERVERLESS",
                "opensearchServerlessConfiguration": {
                    "collectionArn": f"arn:aws:aoss:{REGION}:{boto3.client('sts').get_caller_identity()['Account']}:collection/{collection_id}",
                    "vectorIndexName": "research-agent-index",
                    "fieldMapping": {
                        "vectorField": "embedding",
                        "textField": "text",
                        "metadataField": "metadata",
                    },
                },
            },
        )
        kb_id = kb_resp["knowledgeBase"]["knowledgeBaseId"]
        tg(chat_id, f"✅ Knowledge Base created: `{kb_id}`")
    except Exception as e:
        tg(chat_id, f"❌ Failed to create Knowledge Base: {e}")
        return

    # Step 4: Create data source + sync
    try:
        tg(chat_id, "⏳ Creating data source and syncing S3 documents...")
        ds_resp = bedrock_agent.create_data_source(
            knowledgeBaseId=kb_id,
            name="S3DocsSource",
            dataSourceConfiguration={
                "type": "S3",
                "s3Configuration": {"bucketArn": f"arn:aws:s3:::{S3_BUCKET}", "inclusionPrefixes": ["docs/"]},
            },
        )
        ds_id = ds_resp["dataSource"]["dataSourceId"]
        bedrock_agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
        tg(chat_id, "⏳ Ingestion started. Waiting...")
        time.sleep(30)
    except Exception as e:
        logger.warning("Data source: %s", e)

    # Step 5: Recreate Bedrock Agent
    try:
        tg(chat_id, "⏳ Creating Bedrock Agent...")
        with open("/var/task/agent_instruction.txt") as f:
            instruction = f.read()
    except Exception:
        instruction = "You are an expert Technical Research Analyst. Search all sources and produce a comprehensive email-ready report."

    try:
        agent_resp = bedrock_agent.create_agent(
            agentName="ResearchAnalystAgent",
            agentResourceRoleArn=agent_role_arn,
            foundationModel="anthropic.claude-3-5-sonnet-20240620-v1:0",
            instruction=instruction,
            idleSessionTTLInSeconds=1800,
        )
        new_agent_id = agent_resp["agent"]["agentId"]
        time.sleep(10)
        bedrock_agent.prepare_agent(agentId=new_agent_id)
        time.sleep(15)
        alias_resp = bedrock_agent.create_agent_alias(
            agentId=new_agent_id,
            agentAliasName="live",
        )
        new_alias_id = alias_resp["agentAlias"]["agentAliasId"]
        tg(chat_id, f"✅ Bedrock Agent created: `{new_agent_id}` / alias: `{new_alias_id}`")
    except Exception as e:
        tg(chat_id, f"⚠️ Agent creation failed: {e}\nYou may need to run `cdk deploy` to recreate the agent.")
        new_agent_id = ""
        new_alias_id = ""

    # Update Lambda env vars for webhook
    try:
        lambda_client = boto3.client("lambda", region_name=REGION)
        webhook_conf = lambda_client.get_function_configuration(FunctionName="research-agent-webhook")
        env = webhook_conf["Environment"]["Variables"]
        if new_agent_id:
            env["BEDROCK_AGENT_ID"] = new_agent_id
        if new_alias_id:
            env["BEDROCK_AGENT_ALIAS_ID"] = new_alias_id
        lambda_client.update_function_configuration(
            FunctionName="research-agent-webhook",
            Environment={"Variables": env},
        )
    except Exception as e:
        logger.warning("Update webhook env: %s", e)

    # Update state
    set_state(
        aws_status="active",
        provider="aws",
        knowledge_base_id=kb_id,
        bedrock_agent_id=new_agent_id or "",
        bedrock_agent_alias_id=new_alias_id or "",
    )

    tg(chat_id,
        "✅ *AWS fully activated!*\n\n"
        f"Knowledge Base: `{kb_id}`\n"
        f"Agent ID: `{new_agent_id}`\n\n"
        "Provider switched to *AWS Bedrock*.\n"
        "⚠️ Update `BEDROCK_AGENT_ID` and `BEDROCK_AGENT_ALIAS_ID` in `.env.local` and redeploy if needed."
    )


# ── Azure Deactivate ──────────────────────────────────────────────────────────

def deactivate_azure(chat_id: str):
    tg(chat_id, "🔴 *Deactivating Azure OpenAI...*")
    # Azure OpenAI is pay-per-use (no always-on cost).
    # We just mark it inactive so the bot won't use it.
    # If Azure management credentials are available, also delete the deployment.

    if AZURE_TENANT_ID and AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_SUBSCRIPTION_ID:
        try:
            tg(chat_id, "⏳ Deleting Azure OpenAI deployment...")
            token = _get_azure_mgmt_token()
            url = (
                f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
                f"/resourceGroups/{AZURE_RESOURCE_GROUP}"
                f"/providers/Microsoft.CognitiveServices/accounts/{AZURE_OPENAI_RESOURCE}"
                f"/deployments/{AZURE_OPENAI_DEPLOYMENT}"
                f"?api-version=2023-05-01"
            )
            resp = requests.delete(url, headers={"Authorization": f"Bearer {token}"}, timeout=30)
            if resp.status_code in (200, 202, 204):
                tg(chat_id, f"✅ Deployment `{AZURE_OPENAI_DEPLOYMENT}` deleted.")
            else:
                tg(chat_id, f"⚠️ Could not delete deployment: {resp.status_code}. Marked inactive anyway.")
        except Exception as e:
            tg(chat_id, f"⚠️ Azure deletion failed: {e}\nMarked inactive anyway.")
    else:
        tg(chat_id, "ℹ️ Azure management credentials not configured — marking inactive only.\n_(Azure OpenAI is pay-per-use, so no cost while inactive)_")

    set_state(azure_status="inactive", provider="aws")
    tg(chat_id,
        "✅ *Azure deactivated!*\n\n"
        "Provider switched to *AWS Bedrock*.\n"
        "Use /activateazure to restore Azure OpenAI."
    )


# ── Azure Activate ────────────────────────────────────────────────────────────

def activate_azure(chat_id: str):
    tg(chat_id, "🟢 *Activating Azure OpenAI...*")

    if AZURE_TENANT_ID and AZURE_CLIENT_ID and AZURE_CLIENT_SECRET and AZURE_SUBSCRIPTION_ID:
        try:
            tg(chat_id, "⏳ Creating Azure OpenAI GPT-4o deployment...")
            token = _get_azure_mgmt_token()
            url = (
                f"https://management.azure.com/subscriptions/{AZURE_SUBSCRIPTION_ID}"
                f"/resourceGroups/{AZURE_RESOURCE_GROUP}"
                f"/providers/Microsoft.CognitiveServices/accounts/{AZURE_OPENAI_RESOURCE}"
                f"/deployments/{AZURE_OPENAI_DEPLOYMENT}"
                f"?api-version=2023-05-01"
            )
            body = {
                "sku": {"name": "Standard", "capacity": 10},
                "properties": {"model": {"format": "OpenAI", "name": "gpt-4o", "version": "2024-11-20"}},
            }
            resp = requests.put(url, headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                json=body, timeout=60)
            if resp.status_code in (200, 201):
                tg(chat_id, f"✅ Deployment `{AZURE_OPENAI_DEPLOYMENT}` created.")
            else:
                tg(chat_id, f"⚠️ Deployment response {resp.status_code}: {resp.text[:200]}")
        except Exception as e:
            tg(chat_id, f"⚠️ Azure deployment creation failed: {e}")
    else:
        tg(chat_id, "ℹ️ Azure management credentials not configured — marking active.\nEnsure the GPT-4o deployment exists in Azure portal.")

    # Test Azure OpenAI connectivity
    try:
        azure_endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").rstrip("/")
        azure_key = os.environ.get("AZURE_OPENAI_API_KEY", "")
        azure_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview")
        test_url = f"{azure_endpoint}/openai/deployments/{AZURE_OPENAI_DEPLOYMENT}/chat/completions?api-version={azure_version}"
        r = requests.post(test_url,
            headers={"api-key": azure_key, "Content-Type": "application/json"},
            json={"messages": [{"role": "user", "content": "hi"}], "max_tokens": 5},
            timeout=15)
        if r.ok:
            tg(chat_id, "✅ Azure OpenAI connection verified.")
        else:
            tg(chat_id, f"⚠️ Azure OpenAI test failed: {r.status_code}. Check credentials.")
    except Exception as e:
        tg(chat_id, f"⚠️ Azure connection test error: {e}")

    set_state(azure_status="active", provider="azure")
    tg(chat_id,
        "✅ *Azure activated!*\n\n"
        "Provider switched to *Azure OpenAI (GPT-4o)*.\n"
        "Use /deactivateazure to stop using Azure resources."
    )


def _get_azure_mgmt_token() -> str:
    """Get Azure management API access token using Service Principal."""
    url = f"https://login.microsoftonline.com/{AZURE_TENANT_ID}/oauth2/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": AZURE_CLIENT_ID,
        "client_secret": AZURE_CLIENT_SECRET,
        "resource": "https://management.azure.com/",
    }
    resp = requests.post(url, data=data, timeout=15)
    resp.raise_for_status()
    return resp.json()["access_token"]


# ── Lambda handler ────────────────────────────────────────────────────────────

ACTIONS = {
    "deactivate_aws": deactivate_aws,
    "activate_aws": activate_aws,
    "deactivate_azure": deactivate_azure,
    "activate_azure": activate_azure,
}

def lambda_handler(event, context):
    action = event.get("action", "")
    chat_id = str(event.get("chat_id", ""))
    fn = ACTIONS.get(action)
    if fn:
        try:
            fn(chat_id)
        except Exception as e:
            logger.exception("Infra action %s failed", action)
            tg(chat_id, f"❌ Infra operation failed: {e}")
    else:
        logger.error("Unknown action: %s", action)
