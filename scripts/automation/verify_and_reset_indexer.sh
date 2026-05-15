#!/usr/bin/env bash
# Verifies and resets Azure AI Search indexer status, then runs the indexer if needed.
# Usage: ./verify_and_reset_indexer.sh <indexer-name> <search-service-name> <resource-group>

set -euo pipefail

INDEXER_NAME="$1"
SEARCH_SERVICE_NAME="$2"
RESOURCE_GROUP="$3"

status=$(az search indexer status --name "$INDEXER_NAME" --service-name "$SEARCH_SERVICE_NAME" --resource-group "$RESOURCE_GROUP" --query "lastResult.status" -o tsv)

echo "[INFO] Indexer '$INDEXER_NAME' status: $status"

if [[ "$status" != "success" ]]; then
  echo "[INFO] Resetting and running indexer..."
  az search indexer reset --name "$INDEXER_NAME" --service-name "$SEARCH_SERVICE_NAME" --resource-group "$RESOURCE_GROUP"
  az search indexer run --name "$INDEXER_NAME" --service-name "$SEARCH_SERVICE_NAME" --resource-group "$RESOURCE_GROUP"
  echo "[INFO] Indexer reset and run triggered."
else
  echo "[INFO] Indexer is healthy. No action needed."
fi
