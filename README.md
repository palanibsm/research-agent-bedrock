# Technical Research Analyst Bot — AWS Bedrock + Azure OpenAI + Telegram

A production-ready, multi-source AI research assistant delivered as a Telegram bot.
Send it any technical question; it searches the live web (Tavily), a document
library, and a Knowledge Base, then returns a polished email-ready Markdown report.

Supports **two AI providers** switchable live from Telegram — no redeployment needed:

| Provider | Model | Document Store | Switch command |
|---|---|---|---|
| AWS Bedrock | Claude 3.5 Sonnet | S3 + Bedrock Knowledge Base | `/switch` |
| Azure OpenAI | GPT-4o | Azure Blob Storage | `/switch` |

---

## Architecture

```
Telegram User
     │
     │  commands · research query
     ▼
Telegram Bot API
     │  HTTPS POST (webhook)
     ▼
API Gateway ──► Lambda: webhook_handler
                    │
                    │  reads DynamoDB (research-agent-state)
                    │
                    ├── PROVIDER=aws ──► Bedrock Agent (Claude 3.5 Sonnet)
                    │                       ├── Lambda: web_search      → Tavily API
                    │                       ├── Lambda: s3_search       → S3 docs/
                    │                       └── Lambda: kb_search       → Bedrock KB
                    │
                    └── PROVIDER=azure ──► Lambda: azure_agent (GPT-4o)
                                              ├── Lambda: web_search    → Tavily API
                                              └── Lambda: azure_blob_search → Azure Blob Storage
                    │
                    ▼
          email-ready Markdown report → Telegram User

Async infra management:
webhook_handler ──► Lambda: infra_manager (15-min timeout)
                        ├── deactivate_aws  → deletes Bedrock Agent + KB + OpenSearch (~$50/month savings)
                        ├── activate_aws    → recreates OpenSearch + KB + Agent (~8-12 min)
                        ├── deactivate_azure → marks Azure inactive
                        └── activate_azure  → marks Azure active
```

---

## Telegram Commands

| Command | Description |
|---|---|
| `/start` | Welcome message showing active provider, status, and all commands |
| `/provider` | Show the currently active AI provider and status of each |
| `/switch` | Toggle between AWS Bedrock and Azure OpenAI instantly |
| `/deactivateaws` | Delete all AWS AI resources (saves ~$50/month OpenSearch cost) |
| `/activateaws` | Recreate AWS AI resources (~8-12 minutes) |
| `/deactivateazure` | Disable Azure OpenAI provider |
| `/activateazure` | Re-enable Azure OpenAI provider |
| _(any text)_ | Research query — returns a full email-ready report |

---

## Prerequisites

| Requirement | Notes |
|---|---|
| AWS CLI v2 | Configured with credentials that can create IAM, Lambda, S3, API GW, Bedrock, SSM resources |
| AWS CDK v2 | `npm install -g aws-cdk` |
| Python 3.12 | |
| Node.js 18+ | Required by CDK CLI |
| Telegram bot token | Create via [@BotFather](https://t.me/BotFather) |
| Tavily API key | Free tier at [tavily.com](https://tavily.com) |
| Azure OpenAI resource | Required for Azure provider — GPT-4o deployment (see setup step 4) |
| Azure Storage account | Required for Azure provider — Blob Storage container (see setup step 5) |

---

## Step-by-Step Setup

### 1. Clone and install dependencies

```bash
git clone https://github.com/palanibsm/research-agent-bedrock research-agent
cd research-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Pre-install Lambda dependencies locally (no Docker required)
pip install -r lambdas/web_search/requirements.txt     -t lambdas/web_search/
pip install -r lambdas/s3_search/requirements.txt      -t lambdas/s3_search/
pip install -r lambdas/kb_search/requirements.txt      -t lambdas/kb_search/
pip install -r lambdas/webhook_handler/requirements.txt -t lambdas/webhook_handler/
pip install -r lambdas/azure_agent/requirements.txt    -t lambdas/azure_agent/
pip install -r lambdas/azure_blob_search/requirements.txt -t lambdas/azure_blob_search/
pip install -r lambdas/infra_manager/requirements.txt  -t lambdas/infra_manager/
```

### 2. Create a Telegram bot

1. Open Telegram → search **@BotFather** → send `/newbot`
2. Follow the prompts and copy the **API token**

### 3. Configure environment variables

```bash
cp .env.example .env.local
```

Edit `.env.local` with values you have now:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TAVILY_API_KEY=tvly-...
AWS_REGION=ap-southeast-1
PROVIDER=azure                          # starting provider: "aws" or "azure"
```

Leave `BEDROCK_AGENT_ID`, `BEDROCK_AGENT_ALIAS_ID`, `KNOWLEDGE_BASE_ID`, and
`WEBHOOK_URL` as placeholders — fill them in after the CDK deployment.

### 4. Set up Azure OpenAI (for Azure provider)

1. Go to [portal.azure.com](https://portal.azure.com) → **Create a resource** → search **Azure OpenAI**
2. Create the resource (Region: **East US**, Pricing: **Standard S0**)
3. Once deployed → **Go to Azure OpenAI Studio** → **Deployments → Deploy base model → gpt-4o**
4. Go to your resource → **Keys and Endpoint** → copy **Key 1** and **Endpoint**
5. Add to `.env.local`:

```
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com/
AZURE_OPENAI_API_KEY=your_key_here
AZURE_OPENAI_DEPLOYMENT=gpt-4o
AZURE_OPENAI_API_VERSION=2024-12-01-preview
```

### 5. Set up Azure Blob Storage (for Azure provider document search)

1. In Azure Portal → **Create a resource** → **Storage account**
2. Create with default settings (LRS redundancy is fine)
3. Inside the storage account → **Containers** → create a container named `research-docs`
4. Go to **Access keys** → copy **Storage account name** and **key1**
5. Add to `.env.local`:

```
AZURE_STORAGE_ACCOUNT_NAME=yourstorageaccount
AZURE_STORAGE_ACCOUNT_KEY=your_access_key_here
AZURE_STORAGE_CONTAINER_NAME=research-docs
```

6. Upload sample documents:
```bash
python scripts/upload_azure_docs.py
```

### 6. Enable Bedrock model access (for AWS provider)

Model agreements must be accepted before Claude can be used. Run:

```bash
# Accept Claude 3.5 Sonnet agreement
OFFER=$(aws bedrock list-foundation-model-agreement-offers \
  --model-id anthropic.claude-3-5-sonnet-20240620-v1:0 \
  --region YOUR_REGION --query "offers[0].offerToken" --output text)

aws bedrock create-foundation-model-agreement \
  --model-id anthropic.claude-3-5-sonnet-20240620-v1:0 \
  --offer-token "$OFFER" --region YOUR_REGION
```

Repeat for the Cohere embedding model if using the Bedrock Knowledge Base:

```bash
OFFER=$(aws bedrock list-foundation-model-agreement-offers \
  --model-id cohere.embed-english-v3 \
  --region YOUR_REGION --query "offers[0].offerToken" --output text)

aws bedrock create-foundation-model-agreement \
  --model-id cohere.embed-english-v3 \
  --offer-token "$OFFER" --region YOUR_REGION
```

### 7. Bootstrap and deploy CDK

```bash
cd cdk
pip install -r requirements.txt
cdk bootstrap aws://YOUR_ACCOUNT_ID/YOUR_REGION
cdk deploy --require-approval never
```

Copy the four outputs printed at the end:

```
ResearchAgentStack.WebhookURL     = https://xxx.execute-api.REGION.amazonaws.com/prod/webhook
ResearchAgentStack.DocsBucketName = research-agent-docs-YOUR_ACCOUNT_ID
ResearchAgentStack.AgentId        = ABCDEF1234
ResearchAgentStack.AgentAliasId   = GHIJKL5678
```

### 8. Update .env.local and redeploy

```
S3_BUCKET_NAME=research-agent-docs-YOUR_ACCOUNT_ID
BEDROCK_AGENT_ID=ABCDEF1234
BEDROCK_AGENT_ALIAS_ID=GHIJKL5678
WEBHOOK_URL=https://xxx.execute-api.REGION.amazonaws.com/prod/webhook
```

```bash
cdk deploy --require-approval never   # injects Agent IDs into Lambda env vars
```

### 9. Initialize DynamoDB state

After the first deploy, seed the provider state:

```bash
python -c "
import boto3
ddb = boto3.resource('dynamodb', region_name='YOUR_REGION')
table = ddb.Table('research-agent-state')
table.put_item(Item={
    'pk': 'config',
    'provider': 'azure',          # or 'aws'
    'aws_status': 'active',
    'azure_status': 'active',
    'bedrock_agent_id': 'YOUR_AGENT_ID',
    'bedrock_agent_alias_id': 'YOUR_ALIAS_ID',
})
print('Done')
"
```

### 10. Create the Bedrock Knowledge Base (manual — console)

1. **Amazon Bedrock → Knowledge Bases → Create knowledge base**
2. Name: `ResearchAnalystKB` | IAM role: create new service role
3. Data source: **Amazon S3** → select your bucket → prefix `docs/`
4. Embeddings model: **Cohere Embed English** (Singapore) or **Amazon Titan Embeddings V2**
5. Vector store: **Quick create** (OpenSearch Serverless)
6. Create → wait ~3 min → click **Sync**
7. Copy the **Knowledge base ID** → add to `.env.local` as `KNOWLEDGE_BASE_ID`
8. Redeploy: `cdk deploy --require-approval never`

### 11. Upload sample documents

```bash
cd ..   # back to repo root
python scripts/upload_sample_docs.py   # uploads to S3
python scripts/upload_azure_docs.py    # uploads to Azure Blob Storage
```

Then trigger a **Sync** on the Knowledge Base in the AWS Console.

### 12. Register the Telegram webhook

```bash
python scripts/setup_telegram_webhook.py
```

### 13. Test the bot

Send `/start` in Telegram, then try:

```
What are the best practices for containerising Python microservices?
```

Use `/switch` to toggle between Azure OpenAI and AWS Bedrock at any time.

---

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | Yes | Token from @BotFather |
| `TAVILY_API_KEY` | Yes | API key from tavily.com |
| `AWS_REGION` | Yes | AWS region for all resources |
| `S3_BUCKET_NAME` | Yes | Set from CDK output after deploy |
| `BEDROCK_AGENT_ID` | Yes | Set from CDK output after deploy |
| `BEDROCK_AGENT_ALIAS_ID` | Yes | Set from CDK output after deploy |
| `KNOWLEDGE_BASE_ID` | Yes | Set after creating KB in console |
| `WEBHOOK_URL` | Yes | Set from CDK output; used by setup_telegram_webhook.py |
| `PROVIDER` | Yes | Starting provider: `aws` or `azure` (changeable via `/switch`) |
| `AZURE_OPENAI_ENDPOINT` | Azure only | `https://your-resource.cognitiveservices.azure.com/` |
| `AZURE_OPENAI_API_KEY` | Azure only | Key 1 from Azure portal |
| `AZURE_OPENAI_DEPLOYMENT` | Azure only | Deployment name (default: `gpt-4o`) |
| `AZURE_OPENAI_API_VERSION` | Azure only | API version (default: `2024-12-01-preview`) |
| `AZURE_STORAGE_ACCOUNT_NAME` | Azure only | Azure Storage account name |
| `AZURE_STORAGE_ACCOUNT_KEY` | Azure only | Azure Storage account access key |
| `AZURE_STORAGE_CONTAINER_NAME` | Azure only | Blob container name (default: `research-docs`) |
| `AZURE_TENANT_ID` | `/activateazure` only | Azure AD tenant ID |
| `AZURE_CLIENT_ID` | `/activateazure` only | Service principal app ID |
| `AZURE_CLIENT_SECRET` | `/activateazure` only | Service principal secret |
| `AZURE_SUBSCRIPTION_ID` | `/activateazure` only | Azure subscription ID |
| `AZURE_RESOURCE_GROUP` | `/activateazure` only | Resource group containing Azure OpenAI |
| `AZURE_OPENAI_RESOURCE_NAME` | `/activateazure` only | Azure OpenAI resource name |

---

## Project Structure

```
research-agent/
├── .env.example                       Template for all environment variables
├── .env.local                         Your local config (git-ignored)
├── requirements.txt                   Top-level CDK/tooling dependencies
├── README.md                          This file
│
├── sample_docs/                       Seed documents uploaded to S3 + KB
│   ├── kubernetes_guide.txt
│   ├── docker_best_practices.txt
│   └── aws_lambda_tips.txt
│
├── cdk/                               AWS CDK infrastructure-as-code
│   ├── app.py                         CDK app entry point
│   ├── cdk.json
│   ├── requirements.txt
│   └── stacks/
│       └── research_agent_stack.py    Full stack definition
│
├── lambdas/
│   ├── webhook_handler/               Telegram webhook — provider router + infra commands
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── azure_agent/                   Azure OpenAI GPT-4o agent (REST, no SDK)
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── azure_blob_search/             Tool: Azure Blob Storage keyword document search
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── infra_manager/                 Async infra lifecycle (activate/deactivate AWS & Azure)
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── web_search/                    Tool: Tavily web search (shared by both providers)
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── s3_search/                     Tool: S3 keyword document search (AWS path only)
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── kb_search/                     Tool: Bedrock KB semantic search (AWS only)
│       ├── handler.py
│       └── requirements.txt
│
└── scripts/
    ├── upload_sample_docs.py          Upload sample_docs/ to S3
    ├── upload_azure_docs.py           Upload sample_docs/ to Azure Blob Storage
    ├── setup_telegram_webhook.py      Register API GW URL with Telegram
    └── test_provider.py               CLI test: invoke active provider directly
```

---

## Switching Providers

The active provider is stored in **DynamoDB** table `research-agent-state` (pk=`config`).
The webhook Lambda reads it at runtime (60-second in-process cache) and writes to it on `/switch`.

```
/provider   →  Azure OpenAI — GPT-4o
/switch     →  ✅ Switched to AWS Bedrock — Claude 3.5 Sonnet
/switch     →  ✅ Switched to Azure OpenAI — GPT-4o
```

### Provider isolation

When in **Azure mode**, the bot uses only Azure services:
- Azure OpenAI (GPT-4o) for AI inference
- Azure Blob Storage for document search

When in **AWS mode**, the bot uses only AWS services:
- AWS Bedrock (Claude 3.5 Sonnet) for AI inference
- Amazon S3 + Bedrock Knowledge Base for document search

---

## Cost Management Commands

| Command | AWS cost impact | Duration |
|---|---|---|
| `/deactivateaws` | Deletes OpenSearch Serverless + KB + Agent (~$50/month saved) | ~2-3 min |
| `/activateaws` | Recreates OpenSearch + KB + Agent | ~8-12 min |
| `/deactivateazure` | Marks Azure inactive (GPT-4o is pay-per-use — no standing cost) | Instant |
| `/activateazure` | Marks Azure active again | Instant |

---

## Cost Considerations

| Service | Cost driver | Estimate |
|---|---|---|
| Lambda (all functions) | GB-seconds + requests | < $1/month at hobby usage |
| API Gateway | Requests | $3.50 per million |
| DynamoDB | PAY_PER_REQUEST | Cents/month |
| AWS Bedrock — Claude 3.5 Sonnet | Input/output tokens | ~$0.003–$0.015 per query |
| Bedrock Knowledge Base | OpenSearch Serverless OCU-hours | ~$50/month (2 OCUs always-on) |
| S3 | Storage + GET requests | Cents/month |
| Tavily | API calls | Free tier: 1,000 searches/month |
| Azure OpenAI — GPT-4o | Input/output tokens | ~$0.005–$0.02 per query |
| Azure Blob Storage | Storage + operations | Cents/month |

> Use `/deactivateaws` when not using AWS Bedrock to eliminate the $50/month OpenSearch cost.

---

## Troubleshooting

### Bot does not respond
1. Verify webhook: `python scripts/setup_telegram_webhook.py`
2. Check Lambda logs: `aws logs tail /aws/lambda/research-agent-webhook --follow --region YOUR_REGION`
3. Confirm `TELEGRAM_BOT_TOKEN` is correct in the Lambda env vars

### AWS Bedrock: accessDeniedException
Model agreements must be accepted before first use. See **Step 6** for the CLI commands to accept agreements for Claude and Cohere.

### AWS Bedrock: throttlingException / Too many tokens per day
New AWS accounts have low default daily token quotas. Options:
- Open an AWS Support case requesting a quota increase for `On-demand model inference tokens per minute`
- Use `/switch` to switch to the Azure provider immediately

### Azure: 401 PermissionDenied
- Verify the endpoint URL ends with `.cognitiveservices.azure.com/`
- Confirm the GPT-4o deployment exists in **Azure OpenAI Studio → Deployments**
- Copy the API key fresh from **Azure portal → Keys and Endpoint**

### /switch command not working
Check DynamoDB permissions on the webhook Lambda role. The CDK stack grants `dynamodb:GetItem`, `dynamodb:UpdateItem`, and `dynamodb:PutItem` on `research-agent-state` automatically.

### /activateaws takes a long time
This is expected — OpenSearch Serverless collection creation takes 8-12 minutes. The infra_manager Lambda has a 15-minute timeout and will send Telegram progress updates.

### Web search returns errors (Runtime.ImportModuleError: tiktoken)
The `tavily-python` SDK pulls in `tiktoken`, which has Windows-compiled native binaries that crash on Lambda's Linux runtime. The `web_search` Lambda uses direct REST calls to the Tavily API instead — no SDK. If you see this error after reinstalling deps, ensure you are running from the fixed `handler.py` (no `from tavily import TavilyClient`) and wipe the old packages:
```bash
find lambdas/web_search -mindepth 1 ! -name 'handler.py' ! -name 'requirements.txt' -exec rm -rf {} +
pip install -r lambdas/web_search/requirements.txt -t lambdas/web_search/
```

### CDK "Docker not running" error
This project pre-installs Lambda dependencies locally — Docker is **not required**. Run the `pip install -r ... -t lambdas/<fn>/` commands from Step 1 before deploying.

---

## Security Notes (Production)

- **Secrets**: Move `TELEGRAM_BOT_TOKEN`, `TAVILY_API_KEY`, and `AZURE_OPENAI_API_KEY` to AWS Secrets Manager
- **IAM least-privilege**: Scope `bedrock:InvokeAgent` and `bedrock:Retrieve` to specific ARNs
- **Webhook validation**: Add a Lambda authoriser that validates the Telegram `secret_token` header
- **DynamoDB access**: Restrict `/switch` to specific Telegram user IDs by checking `chat_id` in the handler
- **Azure credentials**: Store `AZURE_STORAGE_ACCOUNT_KEY` and `AZURE_OPENAI_API_KEY` in AWS Secrets Manager
