# Automation Scripts

This folder contains scripts for automating parts of the Foundry/Fabric deployment process.

## Scripts
- `verify_and_reset_indexer.sh`: Automates Azure AI Search indexer verification and reset.

## Manual Steps Required
The following steps must be performed manually in the Foundry portal:

1. Create and publish the Fabric Data Agent.
2. Create a Knowledge Source (OneLake) and point it to your Lakehouse folder.
3. Create a Knowledge Base and link it to the Knowledge Source.
4. Connect the Fabric Data Agent to Foundry.
5. Create the Orchestrator Agent and add the Fabric Data Agent as a tool/knowledge source.
6. Test the agent with sample queries.

See AZD_AUTOMATION_GUIDE.md for detailed instructions.
