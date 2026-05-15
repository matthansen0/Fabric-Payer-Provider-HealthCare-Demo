#!/usr/bin/env python3
"""
Automates creation of a Foundry Orchestrator Agent and adds the Fabric Data Agent as a tool/knowledge source.
Requires: azure-ai-projects, azure-identity, azure-ai-agents Python SDKs (preview).
"""
import os
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential
from azure.ai.agents.models import FabricTool, Agent, AgentTool, AgentKnowledgeSource

# Set these environment variables or replace with your values
PROJECT_ENDPOINT = os.environ["AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"]  # e.g. https://<region>.api.cognitive.microsoft.com/
PROJECT_NAME = os.environ["PROJECT_NAME"]
AGENT_NAME = os.environ.get("ORCHESTRATOR_AGENT_NAME", "HealthcareOrchestratorAgent")
FABRIC_CONNECTION_ID = os.environ["FABRIC_PROJECT_CONNECTION_ID"]  # e.g. /subscriptions/.../connections/<connection-name>
MODEL_DEPLOYMENT_NAME = os.environ["FOUNDRY_MODEL_DEPLOYMENT_NAME"]

credential = DefaultAzureCredential()
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

# Create the agent
def create_agent():
    agent = Agent(
        name=AGENT_NAME,
        project_name=PROJECT_NAME,
        model_deployment_name=MODEL_DEPLOYMENT_NAME,
        description="Healthcare Orchestrator Agent (automated)",
        tools=[
            AgentTool(
                tool=FabricTool(
                    connection_id=FABRIC_CONNECTION_ID,
                    display_name="Fabric Data Agent"
                )
            )
        ],
        knowledge_sources=[
            AgentKnowledgeSource(
                connection_id=FABRIC_CONNECTION_ID,
                display_name="Fabric Data Agent Knowledge"
            )
        ]
    )
    created = client.agents.create(agent)
    print(f"[INFO] Created Orchestrator Agent: {created.name} (ID: {created.id})")

if __name__ == "__main__":
    create_agent()
