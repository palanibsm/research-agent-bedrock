"""
CDK Stack: ResearchAgentStack

Provisions the complete infrastructure for the Multi-Source Technical Research
Analyst Telegram Bot:

  - S3 bucket for document storage
  - IAM roles for Lambda functions and the Bedrock Agent
  - Four Lambda functions (webhook handler + three action-group tools)
  - REST API Gateway exposing POST /webhook
  - Bedrock Agent (Claude 3.5 Sonnet) with three action groups
  - Bedrock Agent Alias ("live")
  - Lambda resource-based policies allowing the Bedrock Agent to invoke tools
"""
import os
import json
import aws_cdk as cdk
from aws_cdk import (
    Stack,
    Duration,
    RemovalPolicy,
    CfnOutput,
    aws_s3 as s3,
    aws_iam as iam,
    aws_lambda as lambda_,
    aws_apigateway as apigw,
    aws_bedrock as bedrock,
    aws_ssm as ssm,
)
from constructs import Construct


# ---------------------------------------------------------------------------
# Helper: generate a minimal OpenAPI 3.0 schema for a single-query action group
# ---------------------------------------------------------------------------

def _action_schema(action_group_name: str, path: str) -> str:
    """Return a JSON OpenAPI 3.0 schema string for an action group tool.

    Each tool exposes a single GET endpoint that accepts one required query
    string parameter named ``query``.
    """
    schema = {
        "openapi": "3.0.0",
        "info": {
            "title": action_group_name,
            "version": "1.0",
            "description": f"API schema for the {action_group_name} action group",
        },
        "paths": {
            path: {
                "get": {
                    "operationId": action_group_name.lower().replace(" ", "_"),
                    "description": f"Execute {action_group_name} with a search query",
                    "parameters": [
                        {
                            "name": "query",
                            "in": "query",
                            "required": True,
                            "description": "The natural-language search query string",
                            "schema": {"type": "string"},
                        }
                    ],
                    "responses": {
                        "200": {
                            "description": "Successful response with search results",
                            "content": {
                                "application/json": {
                                    "schema": {
                                        "type": "object",
                                        "properties": {
                                            "result": {
                                                "type": "string",
                                                "description": "JSON-encoded search results",
                                            }
                                        },
                                    }
                                }
                            },
                        }
                    },
                }
            }
        },
    }
    return json.dumps(schema)


# ---------------------------------------------------------------------------
# Bedrock Agent system prompt
# ---------------------------------------------------------------------------

AGENT_INSTRUCTION = """\
You are an expert Technical Research Analyst. When a user sends you a research \
query, you MUST follow these instructions precisely.

STEP 1 — GATHER INFORMATION
Search ALL THREE sources for every query:
  1. WebSearchActionGroup  — search the live web for current articles, docs, \
and announcements.
  2. S3SearchActionGroup   — search the internal document library for relevant \
technical guides stored in S3.
  3. KBSearchActionGroup   — perform a semantic/vector search on the knowledge \
base for conceptually similar content.

Do not skip any source. If a tool returns an error, note the failure and \
continue with the remaining sources.

STEP 2 — SYNTHESISE AND WRITE THE REPORT
After collecting results from all three sources, synthesise the information \
into a comprehensive, email-ready research report using EXACTLY the following \
Markdown structure:

---

**SUBJECT:** [Concise descriptive subject line for the research topic]

## Executive Summary

[2–3 sentences covering the most important takeaway from all sources combined.]

## Key Findings

- [Most important finding — include specific technical detail]
- [Second finding]
- [Third finding]
- [Fourth finding]
- [Fifth finding — add more bullet points as warranted by the research]

## Detailed Analysis

[3–4 paragraphs of in-depth technical analysis that synthesises all three \
sources. Include specific version numbers, CLI commands, configuration \
snippets, or architecture decisions where appropriate. Highlight agreements \
and contradictions between sources. Provide actionable recommendations.]

## Sources & References

**Web Sources:**
- [Article / page title] — [URL]

**Internal Documents (S3):**
- [Filename] — [One-sentence description of what was found]

**Knowledge Base:**
- [Passage excerpt (first 80 chars)] — relevance score: [score]

---
*Report generated by the Technical Research Analyst — powered by Amazon Bedrock*

---

STYLE GUIDELINES
- Write in a professional, precise tone suitable for senior software engineers \
and cloud architects.
- Prioritise accuracy over brevity.
- Always include concrete, actionable recommendations.
- Cite every piece of information to the source it came from.\
"""


# Dependencies are pre-installed locally into each Lambda folder via:
#   pip install -r lambdas/<fn>/requirements.txt -t lambdas/<fn>/
# No Docker bundling is needed.


# ---------------------------------------------------------------------------
# Stack
# ---------------------------------------------------------------------------

class ResearchAgentStack(Stack):
    def __init__(self, scope: Construct, construct_id: str, **kwargs):
        super().__init__(scope, construct_id, **kwargs)

        runtime = lambda_.Runtime.PYTHON_3_12

        # ── S3 Bucket ──────────────────────────────────────────────────────
        docs_bucket = s3.Bucket(
            self,
            "DocsBucket",
            bucket_name=f"research-agent-docs-{self.account}",
            removal_policy=RemovalPolicy.DESTROY,
            auto_delete_objects=True,
            versioned=False,
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            encryption=s3.BucketEncryption.S3_MANAGED,
        )

        # ── Bedrock Agent IAM Role ─────────────────────────────────────────
        # The Bedrock service assumes this role when orchestrating the agent.
        agent_role = iam.Role(
            self,
            "BedrockAgentRole",
            assumed_by=iam.ServicePrincipal("bedrock.amazonaws.com"),
            description="Execution role for the Research Analyst Bedrock Agent",
        )
        agent_role.add_managed_policy(
            iam.ManagedPolicy.from_aws_managed_policy_name("AmazonBedrockFullAccess")
        )
        # Allow the agent role to invoke the foundation model directly
        agent_role.add_to_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeModel"],
                resources=[
                    f"arn:aws:bedrock:{self.region}::foundation-model/anthropic.claude-3-5-sonnet-20240620-v1:0"
                ],
            )
        )

        # ── Lambda: web-search-tool ────────────────────────────────────────
        # NOTE: TAVILY_API_KEY is passed as a plain environment variable here
        # for simplicity.  In production, store it in AWS Secrets Manager and
        # use secretsmanager.Secret + ecs_patterns or Lambda's native secrets
        # support.
        web_search_fn = lambda_.Function(
            self,
            "WebSearchFn",
            function_name="research-agent-web-search",
            runtime=runtime,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambdas/web_search"),
            timeout=Duration.seconds(30),
            memory_size=256,
            description="Action group tool: web search via Tavily API",
            environment={
                # TODO (production): replace with Secrets Manager reference
                "TAVILY_API_KEY": os.environ.get("TAVILY_API_KEY", "REPLACE_ME"),
            },
        )

        # ── Lambda: s3-search-tool ─────────────────────────────────────────
        s3_search_fn = lambda_.Function(
            self,
            "S3SearchFn",
            function_name="research-agent-s3-search",
            runtime=runtime,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambdas/s3_search"),
            timeout=Duration.seconds(30),
            memory_size=256,
            description="Action group tool: keyword search over S3 documents",
            environment={
                "S3_BUCKET_NAME": docs_bucket.bucket_name,
            },
        )
        # Grant read-only access to the documents bucket
        docs_bucket.grant_read(s3_search_fn)

        # ── Lambda: kb-search-tool ─────────────────────────────────────────
        kb_search_fn = lambda_.Function(
            self,
            "KBSearchFn",
            function_name="research-agent-kb-search",
            runtime=runtime,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambdas/kb_search"),
            timeout=Duration.seconds(30),
            memory_size=256,
            description="Action group tool: semantic search via Bedrock Knowledge Base",
            environment={
                # KNOWLEDGE_BASE_ID is created manually after the KB is set up
                # in the AWS console; update .env and redeploy to inject the value.
                "KNOWLEDGE_BASE_ID": os.environ.get("KNOWLEDGE_BASE_ID", "REPLACE_ME"),
            },
        )
        kb_search_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:Retrieve"],
                resources=["*"],  # scope to the KB ARN once known
            )
        )

        # ── SSM Parameter: provider switch ────────────────────────────────
        provider_param = ssm.StringParameter(
            self, "ProviderParam",
            parameter_name="/research-agent/provider",
            string_value=os.environ.get("PROVIDER", "azure"),
            description="Active AI provider: 'aws' (Bedrock) or 'azure' (Azure OpenAI). Update via Telegram /switch command.",
        )

        # ── Lambda: webhook-handler ────────────────────────────────────────
        # BEDROCK_AGENT_ID and BEDROCK_AGENT_ALIAS_ID are available only after
        # the first deployment.  On the initial deploy they are set to
        # REPLACE_ME; after copying the CDK outputs into .env, re-run
        # `cdk deploy` to inject the real values.
        webhook_fn = lambda_.Function(
            self,
            "WebhookFn",
            function_name="research-agent-webhook",
            runtime=runtime,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambdas/webhook_handler"),
            timeout=Duration.minutes(5),
            memory_size=512,
            description="Receives Telegram webhook calls and drives the Bedrock Agent",
            environment={
                # TODO (production): store in Secrets Manager
                "TELEGRAM_BOT_TOKEN": os.environ.get("TELEGRAM_BOT_TOKEN", "REPLACE_ME"),
                "BEDROCK_AGENT_ID": os.environ.get("BEDROCK_AGENT_ID", "REPLACE_ME"),
                "BEDROCK_AGENT_ALIAS_ID": os.environ.get("BEDROCK_AGENT_ALIAS_ID", "REPLACE_ME"),
                "SSM_PROVIDER_KEY": "/research-agent/provider",
                "AZURE_AGENT_FUNCTION": "research-agent-azure-agent",
            },
        )
        webhook_fn.add_to_role_policy(
            iam.PolicyStatement(
                actions=["bedrock:InvokeAgent"],
                resources=["*"],
            )
        )
        # Allow webhook to read and update the provider SSM parameter
        provider_param.grant_read(webhook_fn)
        provider_param.grant_write(webhook_fn)

        # ── Lambda: azure-agent ────────────────────────────────────────────
        azure_agent_fn = lambda_.Function(
            self, "AzureAgentFn",
            function_name="research-agent-azure-agent",
            runtime=runtime,
            handler="handler.lambda_handler",
            code=lambda_.Code.from_asset("../lambdas/azure_agent"),
            timeout=Duration.minutes(5),
            memory_size=512,
            description="Azure OpenAI agent with GPT-4o tool calling",
            environment={
                "AZURE_OPENAI_ENDPOINT": os.environ.get("AZURE_OPENAI_ENDPOINT", "REPLACE_ME"),
                "AZURE_OPENAI_API_KEY": os.environ.get("AZURE_OPENAI_API_KEY", "REPLACE_ME"),
                "AZURE_OPENAI_DEPLOYMENT": os.environ.get("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
                "AZURE_OPENAI_API_VERSION": os.environ.get("AZURE_OPENAI_API_VERSION", "2024-02-01"),
            },
        )
        # Allow azure_agent to invoke the tool Lambdas
        azure_agent_fn.add_to_role_policy(iam.PolicyStatement(
            actions=["lambda:InvokeFunction"],
            resources=[web_search_fn.function_arn, s3_search_fn.function_arn],
        ))
        # Allow webhook to invoke azure_agent
        azure_agent_fn.grant_invoke(webhook_fn)

        # ── API Gateway ────────────────────────────────────────────────────
        api = apigw.RestApi(
            self,
            "ResearchAgentApi",
            rest_api_name="research-agent-api",
            description="Telegram webhook endpoint for the Research Analyst Bot",
            deploy_options=apigw.StageOptions(
                stage_name="prod",
                throttling_rate_limit=50,
                throttling_burst_limit=100,
            ),
            # Telegram requires HTTPS; API Gateway provides this by default.
        )

        webhook_resource = api.root.add_resource("webhook")
        webhook_resource.add_method(
            "POST",
            apigw.LambdaIntegration(webhook_fn, proxy=True),
        )

        # ── Bedrock Agent ──────────────────────────────────────────────────
        agent = bedrock.CfnAgent(
            self,
            "ResearchAgent",
            agent_name="ResearchAnalystAgent",
            agent_resource_role_arn=agent_role.role_arn,
            foundation_model="anthropic.claude-3-5-sonnet-20240620-v1:0",
            instruction=AGENT_INSTRUCTION,
            description="Multi-source technical research analyst with web, S3, and KB search",
            idle_session_ttl_in_seconds=1800,  # 30 minutes
            auto_prepare=True,
            action_groups=[
                # ── Action Group 1: Web Search ─────────────────────────────
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name="WebSearchActionGroup",
                    description=(
                        "Search the live web for current technical information, "
                        "articles, documentation, and announcements using the "
                        "Tavily search API."
                    ),
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=web_search_fn.function_arn,
                    ),
                    api_schema=bedrock.CfnAgent.APISchemaProperty(
                        payload=_action_schema("WebSearchActionGroup", "/web-search"),
                    ),
                ),
                # ── Action Group 2: S3 Document Search ────────────────────
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name="S3SearchActionGroup",
                    description=(
                        "Search the internal S3 technical document library "
                        "using keyword-frequency matching. Covers Kubernetes, "
                        "Docker, AWS Lambda, and other engineering guides."
                    ),
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=s3_search_fn.function_arn,
                    ),
                    api_schema=bedrock.CfnAgent.APISchemaProperty(
                        payload=_action_schema("S3SearchActionGroup", "/s3-search"),
                    ),
                ),
                # ── Action Group 3: Knowledge Base Search ─────────────────
                bedrock.CfnAgent.AgentActionGroupProperty(
                    action_group_name="KBSearchActionGroup",
                    description=(
                        "Perform semantic vector search on the Bedrock Knowledge "
                        "Base to retrieve conceptually similar passages from the "
                        "ingested document corpus."
                    ),
                    action_group_executor=bedrock.CfnAgent.ActionGroupExecutorProperty(
                        lambda_=kb_search_fn.function_arn,
                    ),
                    api_schema=bedrock.CfnAgent.APISchemaProperty(
                        payload=_action_schema("KBSearchActionGroup", "/kb-search"),
                    ),
                ),
            ],
        )

        # ── Bedrock Agent Alias ────────────────────────────────────────────
        # An alias is required to invoke the agent from external code.
        agent_alias = bedrock.CfnAgentAlias(
            self,
            "ResearchAgentAlias",
            agent_id=agent.attr_agent_id,
            agent_alias_name="live",
            description="Production alias for the Research Analyst Agent",
        )
        agent_alias.add_dependency(agent)

        # ── Lambda resource-based policies ─────────────────────────────────
        # Allow the Bedrock Agents service to invoke each action-group Lambda.
        for fn, label in [
            (web_search_fn, "WebSearch"),
            (s3_search_fn, "S3Search"),
            (kb_search_fn, "KBSearch"),
        ]:
            fn.add_permission(
                f"AllowBedrockAgentInvoke{label}",
                principal=iam.ServicePrincipal("bedrock.amazonaws.com"),
                action="lambda:InvokeFunction",
                source_arn=f"arn:aws:bedrock:{self.region}:{self.account}:agent/*",
            )

        # ── Stack Outputs ──────────────────────────────────────────────────
        CfnOutput(
            self,
            "WebhookURL",
            value=f"{api.url}webhook",
            description=(
                "POST this URL to Telegram setWebhook API. "
                "Also set as WEBHOOK_URL in .env, then run "
                "python scripts/setup_telegram_webhook.py"
            ),
        )
        CfnOutput(
            self,
            "DocsBucketName",
            value=docs_bucket.bucket_name,
            description=(
                "S3 bucket for research documents. "
                "Set as S3_BUCKET_NAME in .env, then run "
                "python scripts/upload_sample_docs.py"
            ),
        )
        CfnOutput(
            self,
            "AgentId",
            value=agent.attr_agent_id,
            description="Bedrock Agent ID — copy to BEDROCK_AGENT_ID in .env",
        )
        CfnOutput(
            self,
            "AgentAliasId",
            value=agent_alias.attr_agent_alias_id,
            description="Bedrock Agent Alias ID — copy to BEDROCK_AGENT_ALIAS_ID in .env",
        )
        CfnOutput(
            self,
            "WebhookLambdaArn",
            value=webhook_fn.function_arn,
            description="ARN of the Telegram webhook handler Lambda",
        )
        CfnOutput(self, "Provider",
            value=os.environ.get("PROVIDER", "aws"),
            description="Active provider: aws or azure",
        )
