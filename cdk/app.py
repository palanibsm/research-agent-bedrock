#!/usr/bin/env python3
"""CDK app entry point for the Research Analyst Bot stack."""
import os
from pathlib import Path
from dotenv import load_dotenv
import aws_cdk as cdk
from stacks.research_agent_stack import ResearchAgentStack

# Load .env from the repo root (one level above the cdk/ directory)
load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env")

app = cdk.App()

ResearchAgentStack(
    app,
    "ResearchAgentStack",
    env=cdk.Environment(
        account="568386354757",
        region="ap-southeast-1",
    ),
)

app.synth()
