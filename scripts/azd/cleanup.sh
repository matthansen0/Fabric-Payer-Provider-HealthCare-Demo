#!/usr/bin/env bash
set -euo pipefail

# Robust cleanup script for azd environment and all resources
# Usage: bash scripts/azd/cleanup.sh [--env-name <name>] [--remove-env]

ENV_NAME="healthcare-demo"
REMOVE_ENV=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-name)
      ENV_NAME="$2"
      shift 2
      ;;
    --remove-env)
      REMOVE_ENV=true
      shift
      ;;
    -h|--help)
      echo "Usage: $0 [--env-name <name>] [--remove-env]"
      exit 0
      ;;
    *)
      echo "Unknown arg: $1"
      exit 1
      ;;
  esac
done

# Ensure azd and az are installed
for cmd in azd az; do
  if ! command -v "$cmd" >/dev/null 2>&1; then
    echo "[error] Missing command: $cmd"
    exit 1
  fi
done

# Select environment and get resource group
if ! azd env select "$ENV_NAME" >/dev/null 2>&1; then
  echo "[error] azd environment not found: $ENV_NAME"
  exit 1
fi

RESOURCE_GROUP="$(azd env get-value AZURE_RESOURCE_GROUP 2>/dev/null || true)"
if [[ -z "$RESOURCE_GROUP" ]]; then
  echo "[error] Could not determine resource group from azd env."
  exit 1
fi

SUBSCRIPTION_ID="$(azd env get-value AZURE_SUBSCRIPTION_ID 2>/dev/null || true)"
if [[ -z "$SUBSCRIPTION_ID" ]]; then
  echo "[error] Could not determine subscription ID from azd env."
  exit 1
fi

# Run azd down (will delete resources defined in infra)
echo "[info] Running azd down for environment: $ENV_NAME"
azd down --purge || true


# Explicitly delete the resource group to ensure all resources are purged
if az group exists --name "$RESOURCE_GROUP" --subscription "$SUBSCRIPTION_ID" >/dev/null; then
  echo "[info] Deleting resource group: $RESOURCE_GROUP"
  az group delete --name "$RESOURCE_GROUP" --subscription "$SUBSCRIPTION_ID" --yes --no-wait
  echo "[done] Cleanup initiated. Resource group deletion may take several minutes."
  echo "[info] You can check status with: az group show --name $RESOURCE_GROUP --subscription $SUBSCRIPTION_ID"
else
  echo "[info] Resource group '$RESOURCE_GROUP' was already removed."
fi

# Attempt to delete Fabric workspace via API
echo "[info] Attempting to delete Fabric workspace via API (if present)"
python3 "$(dirname "$0")/delete_fabric_workspace.py"

if [[ "$REMOVE_ENV" == true ]]; then
  echo "[info] Removing local azd environment: $ENV_NAME"
  azd env remove "$ENV_NAME" --force
  echo "[done] Local azd environment removed. Next run will require fresh names."
fi
