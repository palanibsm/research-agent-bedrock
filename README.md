# Technical Research Analyst Bot — AWS Bedrock + Telegram

A production-ready, multi-source AI research assistant delivered as a Telegram
bot.  Send it any technical question; it searches the live web (Tavily), an S3
document library, and a Bedrock Knowledge Base, then returns a polished,
email-ready Markdown report.

---

## Architecture

```
Telegram User
     │
     │  sends message
     ▼
Telegram Bot API
     │
     │  HTTPS POST (webhook)
     ▼
API Gateway  ──►  Lambda: webhook_handler
                       │
                       │  invoke_agent()
                       ▼
              Bedrock Agent  (Claude 3.5 Sonnet)
              ┌────────────────────────────────────┐
              │  Action Group 1: WebSearchAG        │
              │    └─► Lambda: web_search           │
              │          └─► Tavily Search API      │
              │                                    │
              │  Action Group 2: S3SearchAG         │
              │    └─► Lambda: s3_search            │
              │          └─► S3 (docs/ prefix)      │
              │                                    │
              │  Action Group 3: KBSearchAG         │
              │    └─► Lambda: kb_search            │
              │          └─► Bedrock Knowledge Base │
              └────────────────────────────────────┘
                       │
                       │  email-ready Markdown report
                       ▼
              Lambda: webhook_handler
                       │
                       │  sendMessage
                       ▼
              Telegram User
```

---

## Prerequisites

| Requirement | Version / Notes |
|---|---|
| AWS CLI | v2, configured with credentials that can create IAM, Lambda, S3, API GW, Bedrock resources |
| AWS CDK | v2 (`npm install -g aws-cdk`) |
| Python | 3.12 |
| Telegram bot token | Create via [@BotFather](https://t.me/BotFather) |
| Tavily API key | Free tier available at [tavily.com](https://tavily.com) |
| Bedrock model access | Enable `anthropic.claude-3-5-sonnet-20240620-v1:0` in the AWS Bedrock console for your region |

---

## Step-by-Step Setup

### 1. Clone and install top-level dependencies

```bash
git clone <your-repo-url> research-agent
cd research-agent
python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -r requirements.txt
# Pre-install Lambda dependencies (no Docker needed)
python -m pip install -r lambdas/web_search/requirements.txt -t lambdas/web_search/
python -m pip install -r lambdas/s3_search/requirements.txt -t lambdas/s3_search/
python -m pip install -r lambdas/kb_search/requirements.txt -t lambdas/kb_search/
python -m pip install -r lambdas/webhook_handler/requirements.txt -t lambdas/webhook_handler/
```

### 2. Create a Telegram bot

1. Open Telegram and search for **@BotFather**.
2. Send `/newbot` and follow the prompts (choose a name and username).
3. BotFather will reply with an **API token** — copy it; you'll need it shortly.

### 3. Configure .env

```bash
cp .env.example .env
```

Edit `.env.local` (or `.env`) and fill in the values you know now:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...    # from BotFather
TAVILY_API_KEY=tvly-...                 # from tavily.com
AWS_REGION=ap-southeast-1              # match your CDK deployment region
```

Leave `BEDROCK_AGENT_ID`, `BEDROCK_AGENT_ALIAS_ID`, `KNOWLEDGE_BASE_ID`, and
`WEBHOOK_URL` as their placeholder values for now — you will fill them in after
the CDK deployment.

### 4. Enable Bedrock model access

1. In the AWS Console, navigate to **Amazon Bedrock → Model access**.
2. Click **Manage model access** and enable:
   - `Anthropic Claude 3.5 Sonnet` (ID: `anthropic.claude-3-5-sonnet-20240620-v1:0`)
   - `Amazon Titan Embeddings V2` (needed for the Knowledge Base embeddings)
3. Wait for status to show **Access granted** (usually instant).

### 5. Bootstrap CDK (first time only per account/region)

```bash
cd cdk
python -m pip install -r requirements.txt
cdk bootstrap aws://YOUR_ACCOUNT_ID/ap-southeast-1
```

Replace `YOUR_ACCOUNT_ID` with the output of `aws sts get-caller-identity --query Account --output text`.

### 6. Deploy the CDK stack

```bash
# still inside cdk/
cdk deploy ResearchAgentStack
```

CDK will display a change-set and ask for confirmation.  Type `y`.  The
deployment takes 3–5 minutes.

At the end, copy the four **Outputs** printed to the terminal:

```
ResearchAgentStack.WebhookURL      = https://xxx.execute-api.ap-southeast-1.amazonaws.com/prod/webhook
ResearchAgentStack.DocsBucketName  = research-agent-docs-YOUR_ACCOUNT_ID
ResearchAgentStack.AgentId         = ABCDEF1234
ResearchAgentStack.AgentAliasId    = GHIJKL5678
```

### 7. Update .env.local with deployment outputs

Add the values from step 6 to `.env.local`:

```
S3_BUCKET_NAME=research-agent-docs-YOUR_ACCOUNT_ID
BEDROCK_AGENT_ID=ABCDEF1234
BEDROCK_AGENT_ALIAS_ID=GHIJKL5678
WEBHOOK_URL=https://xxx.execute-api.ap-southeast-1.amazonaws.com/prod/webhook
```

### 8. Re-deploy to inject the Agent IDs into the webhook Lambda

The webhook Lambda needs `BEDROCK_AGENT_ID` and `BEDROCK_AGENT_ALIAS_ID` as
environment variables.  Now that they are in `.env`, re-run the deploy:

```bash
cdk deploy ResearchAgentStack
```

This second deploy is fast (only the Lambda config changes).

### 9. Create the Bedrock Knowledge Base (manual step)

CDK support for Bedrock Knowledge Bases is limited; create it in the console:

1. Go to **Amazon Bedrock → Knowledge Bases → Create knowledge base**.
2. **Name**: `ResearchAnalystKB`
3. **IAM role**: create a new service role (let the console create it).
4. **Data source**: Amazon S3 — enter the bucket name from `.env`
   (`research-agent-docs-123456789012`) with prefix `docs/`.
5. **Embeddings model**: `Amazon Titan Embeddings V2`.
6. **Vector store**: create a new **Amazon OpenSearch Serverless** collection
   (the console handles this automatically).
7. Click through and create.  Wait 2–3 minutes for the collection to become
   active.
8. Click **Sync** to ingest documents (you can sync again later after uploading
   new docs).
9. Copy the **Knowledge base ID** (format: `XXXXXXXXXX`) from the console.

### 10. Update .env with the Knowledge Base ID and re-deploy

```
KNOWLEDGE_BASE_ID=XXXXXXXXXX
```

```bash
cdk deploy ResearchAgentStack
```

### 11. Upload sample documents to S3

```bash
cd ..   # back to repo root
python scripts/upload_sample_docs.py
```

Expected output:
```
Uploading 3 document(s) to s3://research-agent-docs-123456789012/docs/

  [OK] aws_lambda_tips.txt        ->  s3://research-agent-docs-123456789012/docs/aws_lambda_tips.txt
  [OK] docker_best_practices.txt  ->  s3://research-agent-docs-123456789012/docs/docker_best_practices.txt
  [OK] kubernetes_guide.txt       ->  s3://research-agent-docs-123456789012/docs/kubernetes_guide.txt

Done. 3 uploaded, 0 failed.
```

Then trigger a **Sync** on the Knowledge Base in the AWS Console so the new
documents are embedded and indexed.

### 12. Register the Telegram webhook

```bash
python scripts/setup_telegram_webhook.py
```

Expected output:
```
Registering webhook:
  URL: https://xxx.execute-api.us-east-1.amazonaws.com/prod/webhook

[OK] Webhook registered successfully!
     Webhook was set
```

### 13. Test the bot

Open Telegram, find your bot, and send:

```
/start
```

Then try a research query:

```
What are the best practices for containerising Python microservices?
```

The bot will reply with "Researching your query..." and then return a full
email-ready Markdown report within 30–60 seconds.

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

---

## Example Research Queries

- *What are the best practices for containerising Python microservices?*
- *How does Kubernetes Horizontal Pod Autoscaling work and when should I use it?*
- *How can I reduce AWS Lambda cold-start latency in a Java 17 function?*
- *What is the difference between Docker multi-stage builds and BuildKit cache mounts?*
- *Compare ECS Fargate vs Lambda for running API workloads — pros, cons, cost.*
- *What are the security best practices for running containers in production on AWS?*

---

## Project Structure

```
research-agent/
├── .env.example                     Template for environment variables
├── requirements.txt                 Top-level CDK/tooling dependencies
├── README.md                        This file
│
├── sample_docs/                     Seed documents uploaded to S3 + KB
│   ├── kubernetes_guide.txt
│   ├── docker_best_practices.txt
│   └── aws_lambda_tips.txt
│
├── cdk/                             AWS CDK infrastructure-as-code
│   ├── app.py                       CDK app entry point
│   ├── requirements.txt
│   └── stacks/
│       └── research_agent_stack.py  Full stack definition
│
├── lambdas/
│   ├── webhook_handler/             Receives Telegram POST, drives Bedrock Agent
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── web_search/                  Action group: Tavily web search
│   │   ├── handler.py
│   │   └── requirements.txt
│   ├── s3_search/                   Action group: S3 keyword document search
│   │   ├── handler.py
│   │   └── requirements.txt
│   └── kb_search/                   Action group: Bedrock KB semantic search
│       ├── handler.py
│       └── requirements.txt
│
└── scripts/
    ├── upload_sample_docs.py        Upload sample_docs/ to S3
    └── setup_telegram_webhook.py    Register API GW URL with Telegram
```

---

## Cost Considerations

Running this stack at low/moderate traffic volumes is inexpensive:

| Service | Cost driver | Estimate |
|---|---|---|
| Lambda | GB-seconds + requests | < $1/month at hobby usage |
| API Gateway | Requests | $3.50 per million |
| Bedrock Agent | Input/output tokens (Claude 3.5 Sonnet) | ~$0.003–$0.015 per query |
| Bedrock Knowledge Base | OpenSearch Serverless OCU-hours | ~$50/month minimum (2 OCUs always-on) |
| S3 | Storage + GET requests | Cents/month |
| Tavily | API calls | Free tier: 1 000 searches/month |

> **Note:** The OpenSearch Serverless collection required by the Bedrock
> Knowledge Base is the dominant cost driver.  If you are experimenting, delete
> the collection when not in use or use the Bedrock KB's Aurora Serverless v2
> vector store option (pay-per-query, no always-on cost).

---

## Troubleshooting

### Bot does not respond to messages
1. Check the webhook is registered: `python scripts/setup_telegram_webhook.py`
   — it will print the current URL if already set.
2. Check the webhook Lambda logs in CloudWatch:
   `aws logs tail /aws/lambda/research-agent-webhook --follow`
3. Verify `TELEGRAM_BOT_TOKEN` is correct in the Lambda environment variables
   (AWS Console → Lambda → research-agent-webhook → Configuration → Environment variables).

### "REPLACE_ME" errors in Lambda logs
You have not yet run the second `cdk deploy` after copying the Agent IDs into
`.env`.  Re-run `cdk deploy ResearchAgentStack` with the correct values in `.env`.

### Bedrock Agent returns empty response
1. Ensure the Agent status is **Prepared** in the Bedrock console
   (Bedrock → Agents → ResearchAnalystAgent).
2. Verify model access is granted for Claude 3.5 Sonnet in your region.
3. Check the action group Lambda logs for errors
   (`/aws/lambda/research-agent-web-search`, etc.).

### S3 search returns no matches
1. Confirm documents were uploaded: `aws s3 ls s3://YOUR_BUCKET/docs/`
2. Check that `S3_BUCKET_NAME` is set correctly in the s3-search Lambda.

### Knowledge Base search fails
1. Ensure the Knowledge Base sync completed (Bedrock console → Knowledge Bases
   → ResearchAnalystKB → Data source → Last sync status: **Completed**).
2. Verify `KNOWLEDGE_BASE_ID` is set in the kb-search Lambda environment variables.
3. Confirm the Lambda execution role has `bedrock:Retrieve` permission.

### CDK deployment fails with "Docker not running"
This project pre-installs Lambda dependencies locally so Docker is **not required**.
If you see a Docker error, ensure you ran the `pip install -r requirements.txt -t lambdas/<fn>/`
commands in step 1 before deploying.

### Telegram webhook returns 403
API Gateway does not require authentication by default.  If you added a usage
plan or API key, ensure the Telegram setWebhook call includes the correct header.

---

## Security Notes for Production

- **Secrets**: Move `TELEGRAM_BOT_TOKEN` and `TAVILY_API_KEY` to AWS Secrets
  Manager.  Use the Lambda Secrets Manager extension to inject them at runtime
  without cold-start overhead.
- **IAM least-privilege**: Scope the `bedrock:InvokeAgent` and `bedrock:Retrieve`
  policy statements to specific agent/KB ARNs rather than `"*"`.
- **API Gateway authorisation**: Add a Lambda authoriser that validates the
  Telegram webhook secret token (set via `setWebhook secret_token` parameter)
  to ensure only genuine Telegram requests are processed.
- **VPC**: Consider placing the tool Lambdas in a private VPC subnet if they
  need to access VPC-private resources.
