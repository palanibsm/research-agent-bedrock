#!/usr/bin/env python3
"""Quick test: invoke the active provider agent directly without Telegram."""
import os, sys, json, boto3
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env.local")
load_dotenv(Path(__file__).parent.parent / ".env")

PROVIDER = os.environ.get("PROVIDER", "aws")
REGION = os.environ.get("AWS_REGION", "ap-southeast-1")
query = " ".join(sys.argv[1:]) or "What are Docker best practices?"

print(f"Testing provider: {PROVIDER}")
print(f"Query: {query}\n")

lc = boto3.client("lambda", region_name=REGION)

if PROVIDER == "azure":
    r = lc.invoke(
        FunctionName="research-agent-azure-agent",
        InvocationType="RequestResponse",
        Payload=json.dumps({"query": query}),
    )
    result = json.loads(r["Payload"].read())
    print(result.get("report") or result.get("error"))
else:
    import boto3 as b3
    client = b3.client("bedrock-agent-runtime", region_name=REGION)
    AGENT_ID = os.environ.get("BEDROCK_AGENT_ID", "")
    ALIAS_ID = os.environ.get("BEDROCK_AGENT_ALIAS_ID", "")
    resp = client.invoke_agent(agentId=AGENT_ID, agentAliasId=ALIAS_ID, sessionId="test-cli", inputText=query)
    result = ""
    for event in resp["completion"]:
        if "chunk" in event:
            result += event["chunk"]["bytes"].decode()
    print(result)
