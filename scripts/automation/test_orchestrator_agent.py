#!/usr/bin/env python3
"""
Automates testing of the Foundry Orchestrator Agent by sending sample queries and printing responses.
Requires: azure-ai-projects, azure-identity, azure-ai-agents Python SDKs (preview).
"""
import os
from azure.ai.projects import AIProjectClient
from azure.identity import DefaultAzureCredential

PROJECT_ENDPOINT = os.environ["AZURE_AI_FOUNDRY_PROJECT_ENDPOINT"]
PROJECT_NAME = os.environ["PROJECT_NAME"]
AGENT_NAME = os.environ.get("ORCHESTRATOR_AGENT_NAME", "HealthcareOrchestratorAgent")

credential = DefaultAzureCredential()
client = AIProjectClient(endpoint=PROJECT_ENDPOINT, credential=credential)

# Sample queries to test the agent
QUERIES = [
    "What are the CHF management guidelines?",
    "Total claims by payer?",
    "Readmission rates for CHF patients and what do guidelines recommend?"
]

def test_agent():
    agent = client.agents.get(PROJECT_NAME, AGENT_NAME)
    for query in QUERIES:
        print(f"[TEST] Query: {query}")
        response = agent.run(query)
        print(f"[RESPONSE] {response}")

if __name__ == "__main__":
    test_agent()
