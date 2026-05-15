## Troubleshooting: Fabric Capacity Admin Error

If you see an error like:

```
"At least one capacity administrator is required"
```

Or if deployment fails with a message about missing `FABRIC_CAPACITY_ADMINS`, manually set both the environment and config values:

```bash
azd env set FABRIC_CAPACITY_ADMINS '["<REPLACE_WITH_YOUR_EMAIL>"]'
azd env config set infra.parameters.fabricCapacityAdmins '["<REPLACE_WITH_YOUR_EMAIL>"]'
```

Replace `<REPLACE_WITH_YOUR_EMAIL>` with your Azure account UPN if different. Then rerun:

```bash
bash scripts/azd/run_all.sh --turbodeploy
```

This ensures the Fabric capacity deployment has a valid admin and will succeed.
# AZD End-to-End Automation Guide

This repo now includes an `azd` scaffold so one command can provision Azure + Fabric foundation and run post-provision bootstrap steps.

## What `azd up` Automates

1. Creates Azure AI Foundry hub (AI Services account)
2. Creates Azure AI Search (Basic, MSI enabled)
3. Creates Fabric capacity (`Microsoft.Fabric/capacities`)
4. Runs post-provision bootstrap script to:
   - enable AI Hub project-management capability (`allowProjectManagement`)
   - create Foundry Project under the Hub
   - create or find Fabric workspace
   - attempt workspace-to-capacity assignment
   - attempt to run `Healthcare_Launcher` notebook if it exists in the workspace

> Note: Foundry Project ARM creation is region-dependent. In regions where
> `Microsoft.CognitiveServices/accounts/projects` is not available (for example,
> some Sweden Central tenants), bootstrap now detects this and skips ARM project
> creation with clear manual fallback instructions instead of failing the run.

## One-Command Run (Recommended)

After login, run:

```bash
bash scripts/azd/run_all.sh
```

What `--turbodeploy` does (optional):

1. Provisions Fabric capacity at `F256` for faster deployment/setup
2. Starts `Healthcare_Launcher` notebook job (if notebook exists in workspace)
3. Polls status with live `[STATUS]` output every 30 seconds
4. Auto-scales capacity down to `F64` after notebook reaches terminal state

Standard mode (lower cost):

```bash
bash scripts/azd/run_all.sh
```

Recommended for budget control:

- Keep `TURBO_DEPLOY=false`
- Keep `FABRIC_CAPACITY_SKU=F64`

## Prerequisites (Before run)

1. Install and sign in
   - `az login`
   - `azd auth login --use-device-code`
2. Access prerequisites
   - PIM elevation completed (if your tenant requires it)
   - Fabric Admin / Capacity Admin rights to create capacity and workspaces
   - Subscription `Owner` or equivalent RBAC for Azure resources + role assignments
3. Register Azure providers
   - `az provider register --namespace Microsoft.CognitiveServices`
   - `az provider register --namespace Microsoft.Search`
   - `az provider register --namespace Microsoft.Fabric`
4. Configure env values
    - `scripts/azd/run_all.sh` now prompts for key names on each run.
    - Press Enter to keep existing values already saved in `azd env`.
    - You can still set values directly with `azd env set ...` before running.

## If You Prefer Raw `azd up`

## Run

```bash
azd env new healthcare-demo
azd env set HUB_NAME "hls-healthcaredemo-<suffix>"
azd env set SEARCH_SERVICE_NAME "healthcarefoundryais<suffix>"
azd env set FABRIC_CAPACITY_NAME "healthcarefabriccap<suffix>"
azd env set LOCATION "swedencentral"
azd env set FABRIC_WORKSPACE_NAME "HealthcareDemo-WS"
azd up
```

If you use `bash scripts/azd/run_all.sh`, these values are prompted interactively and saved into `azd env`.

## Post-Run Steps (After `azd up`)

1. If notebook wasn’t auto-run, import it once:
   - Fabric workspace -> Import -> Notebook -> `Healthcare_Launcher.ipynb`
   - then rerun bootstrap: `python3 scripts/azd/postprovision.py`
2. Run Foundry setup launcher:
   - open `Foundry_Launcher.ipynb`
   - run all cells (it uses Sweden Central default now)
3. If Foundry preview APIs fall back:
   - complete manual steps listed in final notebook checklist
   - especially Data Agent connection and KB source/base creation
4. Validate agent with 3 tests:
   - knowledge-only
   - data-only
   - hybrid question

## Workspace Cleanup

To delete an old Fabric workspace, use the explicit-name cleanup helper:

```bash
python3 scripts/azd/delete_fabric_workspace.py --workspace-name "HealthcareDemo-WS"
```

The script now requires an explicit workspace name to avoid deleting the active workspace by mistake.

## Status Output During Long Runs

`scripts/azd/postprovision.py` prints live progress lines like:

- `[STATUS] +300s | notebook job status: running`
- `[STATUS] +7200s | notebook job status: completed`

Polling interval and timeout are configurable via azd env vars:

- `NOTEBOOK_RUN_POLL_SECONDS` (default `30`)
- `NOTEBOOK_RUN_MAX_MINUTES` (default `240`)

Notebook job tracking is resilient to tenant differences:

- If run-start response omits job id in JSON, bootstrap now extracts it from
   the `Location` header.
- If both are missing, bootstrap queries job instances and tracks the most
   recent `RunNotebook` job automatically.

## Region Alignment for Foundry Models

For Foundry orchestrator work, keep all of these in the same Azure region:

1. AI Services Hub account
2. Foundry Project
3. Chat model deployment
4. Embedding model deployment

After `azd up`, use this to confirm the active region:

```bash
azd env get-value LOCATION
```

Then deploy both chat and embedding models into that same region/hub to avoid
cross-region availability and connection issues.

## Notes on “Whole Thing” Feasibility

- In most tenants, provisioning + workspace setup can be fully automated.
- Notebook execution is automated (import + run + status polling).
- Foundry model deployments are automated (`gpt-4o` and `text-embedding-ada-002` by default).
- Azure RBAC is automated for Search + OpenAI roles (user + Search MSI where resolvable).
- Fabric workspace Contributor grants are automated for Search MSI + Foundry Hub MSI.
- The remaining manual areas are Foundry/Fabric UI artifacts that still have tenant/UI dependencies:
   1. Fabric Data Agent artifact configuration (data sources + instructions + publish)
   2. Foundry connected resource for Fabric Data Agent
   3. Knowledge source / knowledge base setup and orchestrator agent tool wiring

Those are explicitly called out in `Foundry_Launcher.ipynb` and can be completed in the portal when needed.

## Required Fabric Tenant Settings (Manual)

These tenant-level settings must be enabled by a Fabric admin for the full demo:

1. Service principals can use Fabric APIs
2. Users can create Graph
3. Users can create Ontology
4. Enable Operations Agent

Also ensure Fabric admin/capacity permissions are active for the deployment identity before running automation.
