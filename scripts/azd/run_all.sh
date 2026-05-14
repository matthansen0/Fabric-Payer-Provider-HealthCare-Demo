#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT_DIR"

ENV_NAME="healthcare-demo"
TURBO=false
SKIP_INSTALL=false
ASSIST_MANUAL=true

usage() {
  cat <<'EOF'
Usage: scripts/azd/run_all.sh [options]

Options:
  --env-name <name>      azd environment name (default: healthcare-demo)
  --turbodeploy          Use F256 for setup, then auto-scale to F64 after notebook run
  --skip-install         Skip prereq installer
  --no-assist-manual     Skip assisted manual checkpoint flow
  -h, --help             Show help
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-name)
      ENV_NAME="$2"
      shift 2
      ;;
    --turbodeploy)
      TURBO=true
      shift
      ;;
    --skip-install)
      SKIP_INSTALL=true
      shift
      ;;
    --no-assist-manual)
      ASSIST_MANUAL=false
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      usage
      exit 1
      ;;
  esac
done

if [[ "$SKIP_INSTALL" != true ]]; then
  bash scripts/azd/install_prereqs.sh
fi

for cmd in az azd python3; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[error] Missing command: $cmd"
    exit 1
  fi
done

if ! az account show >/dev/null 2>&1; then
  echo "[error] Not logged in to Azure CLI. Run: az login"
  exit 1
fi

if ! azd auth login --check-status >/dev/null 2>&1; then
  echo "[error] Not logged in to azd. Run: azd auth login --use-device-code"
  exit 1
fi

if ! azd env select "$ENV_NAME" >/dev/null 2>&1; then
  echo "[info] Creating azd environment: $ENV_NAME"
  azd env new "$ENV_NAME"
fi

echo "[info] Using native azd prompts for missing environment values."

cleanup_stale_fabric_workspace() {
  local ws_name="$1"
  if [[ -z "$ws_name" ]]; then
    return 0
  fi
  
  echo "[cleanup] Checking for stale Fabric workspace: $ws_name"
  python3 scripts/azd/delete_fabric_workspace.py \
    --workspace-name "$ws_name" 2>/dev/null || true
}

set_if_missing() {
  local key="$1"
  local value="$2"
  if [[ -z "$(get_env_value_safe "$key")" ]]; then
    azd env set "$key" "$value" >/dev/null
    echo "[env] $key=$value"
  fi
}

set_config_if_missing() {
  local key="$1"
  local value="$2"
  if [[ -z "$(azd env config get "$key" 2>/dev/null || true)" ]]; then
    azd env config set "$key" "$value" >/dev/null
    echo "[config] $key=$value"
  fi
}

get_env_value_safe() {
  local key="$1"
  local out=""
  out="$(azd env get-value "$key" 2>/dev/null || true)"
  if [[ "$out" == *"ERROR: key not found in environment values"* ]]; then
    echo ""
    return 0
  fi
  echo "$out"
}

is_valid_admins_json_array() {
  local value="$1"
  [[ -n "$value" ]] || return 1
  echo "$value" | jq -e 'type == "array" and length > 0 and all(.[]; type == "string" and length > 3)' >/dev/null 2>&1
}

run_postprovision() {
  set -a
  eval "$(azd env get-values)"
  set +a
  python3 scripts/azd/postprovision.py
}

show_manual_checkpoint_help() {
  local ws_name notebook_name ws_id env_name
  ws_name="$(get_env_value_safe FABRIC_WORKSPACE_NAME)"
  notebook_name="$(get_env_value_safe FABRIC_LAUNCHER_NOTEBOOK_NAME)"
  env_name="$(get_env_value_safe AZURE_ENV_NAME)"
  ws_name="${ws_name:-HealthcareDemo-WS}"
  notebook_name="${notebook_name:-Healthcare_Launcher}"
  env_name="${env_name:-healthcare-demo}"

  WS_NAME="$ws_name" ws_id="$(python3 - <<'PY'
import json, subprocess, sys, requests
import os

def run(cmd):
    return subprocess.check_output(cmd, text=True).strip()

try:
    token = run([
        'az', 'account', 'get-access-token',
        '--resource', 'https://api.fabric.microsoft.com',
        '--query', 'accessToken', '-o', 'tsv'
    ])
    ws_name = os.getenv('WS_NAME', '').strip() or 'HealthcareDemo-WS'
    h = {'Authorization': f'Bearer {token}', 'Content-Type': 'application/json'}
    r = requests.get('https://api.fabric.microsoft.com/v1/workspaces', headers=h, timeout=30)
    r.raise_for_status()
    for ws in r.json().get('value', []):
        if ws.get('displayName') == ws_name:
            print(ws.get('id', ''))
            sys.exit(0)
except Exception:
    pass
print('')
PY
)"

  echo ""
  echo "╔════════════════════════════════════════════════════════════════╗"
  echo "║                    MANUAL IMPORT REQUIRED                      ║"
  echo "╚════════════════════════════════════════════════════════════════╝"
  echo ""
  echo "STEP 1: Open Fabric workspace"
  if [[ -n "$ws_id" ]]; then
    echo "  URL: https://app.fabric.microsoft.com/groups/$ws_id/list"
  else
    echo "  Workspace name: $ws_name"
  fi
  echo ""
  echo "STEP 2: Import the notebook"
  echo "  a) Click 'Import' button in workspace"
  echo "  b) Select 'Notebook' option"
  echo "  c) Upload: Healthcare_Launcher.ipynb (from repo root)"
  echo "  d) Wait for import to complete"
  echo ""
  echo "STEP 3: Verify notebook is NOT empty"
  echo "  a) Open the imported notebook in Fabric editor"
  echo "  b) Confirm it contains cells (should see code/markdown)"
  echo "  c) Close the notebook"
  echo ""
  echo "STEP 4: Run the post-provision automation"
  echo "  Command: set -a && eval \"\$(azd env get-values)\" && set +a && python3 scripts/azd/postprovision.py"
  echo ""
  echo "  This will automatically:"
  echo "    • Start the notebook execution"
  echo "    • Monitor progress (~90 minutes)"
  echo "    • Display status every 30 seconds"
  echo "    • Complete when notebook finishes"
  echo ""
  echo "Press Enter after import to continue, or Ctrl+C to exit."
}

set_if_missing TURBO_SETUP_SKU "F256"
set_if_missing TURBO_SCALE_DOWN_SKU "F64"
set_if_missing NOTEBOOK_RUN_POLL_SECONDS "30"
set_if_missing NOTEBOOK_RUN_MAX_MINUTES "240"
set_if_missing FABRIC_WORKSPACE_NAME "HealthcareDemo-WS"
set_if_missing FABRIC_LAUNCHER_NOTEBOOK_NAME "Healthcare_Launcher"

# Fabric capacity requires at least one capacity admin (UPN/email).
DEFAULT_ADMIN_UPN=""
AZ_ACCOUNT_USER_TYPE="$(az account show --query user.type -o tsv 2>/dev/null || true)"
if [[ "$AZ_ACCOUNT_USER_TYPE" == "user" ]]; then
  DEFAULT_ADMIN_UPN="$(az account show --query user.name -o tsv 2>/dev/null || true)"
fi

if [[ -n "$DEFAULT_ADMIN_UPN" ]]; then
  set_if_missing FABRIC_CAPACITY_ADMINS "[\"$DEFAULT_ADMIN_UPN\"]"
fi

ADMINS_ARRAY_JSON="$(get_env_value_safe FABRIC_CAPACITY_ADMINS)"
if ! is_valid_admins_json_array "$ADMINS_ARRAY_JSON"; then
  if [[ -n "$DEFAULT_ADMIN_UPN" ]]; then
    ADMINS_ARRAY_JSON="[\"$DEFAULT_ADMIN_UPN\"]"
    azd env set FABRIC_CAPACITY_ADMINS "$ADMINS_ARRAY_JSON" >/dev/null
    echo "[env] FABRIC_CAPACITY_ADMINS=$ADMINS_ARRAY_JSON"
  else
    echo "[error] FABRIC_CAPACITY_ADMINS is missing or invalid."
    echo "        Expected JSON array, for example: [\"you@contoso.com\"]"
    exit 1
  fi
fi

# Keep azd config in sync and overwrite bad values from previous runs.
azd env config set infra.parameters.fabricCapacityAdmins "$ADMINS_ARRAY_JSON" >/dev/null
echo "[config] infra.parameters.fabricCapacityAdmins=$ADMINS_ARRAY_JSON"

if [[ -z "$(get_env_value_safe FABRIC_CAPACITY_ADMINS)" ]]; then
  echo "[error] Missing FABRIC_CAPACITY_ADMINS in azd env."
  echo "        Fabric capacity deployment requires at least one admin UPN/email."
  echo "        Example: azd env set FABRIC_CAPACITY_ADMINS '[\"you@contoso.com\"]'"
  exit 1
fi

if [[ "$TURBO" == true ]]; then
  azd env set TURBO_DEPLOY "true" >/dev/null
  azd env set FABRIC_CAPACITY_SKU "F256" >/dev/null
  echo "[mode] TURBO enabled: provisioning capacity at F256"
else
  azd env set TURBO_DEPLOY "false" >/dev/null
  azd env set FABRIC_CAPACITY_SKU "F64" >/dev/null
  echo "[mode] Standard mode: provisioning capacity at F64"
fi

echo "[run] Starting azd up (this may take a long time)..."
azd up

# Clean up any stale Fabric workspace from previous runs
FABRIC_WS_NAME="$(get_env_value_safe FABRIC_WORKSPACE_NAME)"
if [[ -n "$FABRIC_WS_NAME" ]]; then
  echo "[cleanup] Removing stale Fabric workspace if it exists..."
  cleanup_stale_fabric_workspace "$FABRIC_WS_NAME"
fi

echo "[done] azd up complete"
if [[ "$TURBO" == true ]]; then
  echo "[done] Turbo mode requested. Post-provision script will scale down to F64 after notebook reaches terminal status."
fi

echo "[run] Running post-provision bootstrap..."
POSTPROVISION_LOG="$(mktemp)"
set +e
run_postprovision | tee "$POSTPROVISION_LOG"
POSTPROVISION_EXIT=${PIPESTATUS[0]}
set -e

# Always require manual notebook import for this tenant
NOTEBOOK_MISSING_MSG="not found in workspace"
if grep -q "$NOTEBOOK_MISSING_MSG" "$POSTPROVISION_LOG"; then
  show_manual_checkpoint_help
  read -r
  echo ""
  echo "[run] Re-running post-provision bootstrap after manual import..."
  echo ""
  run_postprovision
fi
