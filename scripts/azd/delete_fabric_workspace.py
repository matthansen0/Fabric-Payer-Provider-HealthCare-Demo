#!/usr/bin/env python3
"""
Delete Fabric workspace by name using Fabric REST API.

The workspace name must be provided explicitly via --workspace-name or
FABRIC_WORKSPACE_NAME. This avoids accidental deletion of the active workspace.
"""
import argparse
import os
import sys
import requests

def get_token(resource: str) -> str:
    import subprocess
    result = subprocess.run([
        "az", "account", "get-access-token",
        "--resource", resource,
        "--query", "accessToken",
        "-o", "tsv"
    ], capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Failed to get access token: {result.stderr.strip()}")
        sys.exit(1)
    return result.stdout.strip()

def fabric_api(method: str, path: str, token: str, body=None):
    url = f"https://api.fabric.microsoft.com/v1/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return requests.request(method, url, headers=headers, json=body, timeout=60)


def parse_args():
    parser = argparse.ArgumentParser(description="Delete a Fabric workspace by exact name")
    parser.add_argument(
        "--workspace-name",
        help="Exact Fabric workspace display name to delete",
    )
    parser.add_argument(
        "--what-if",
        action="store_true",
        help="Only print what would be deleted",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    workspace_name = (args.workspace_name or os.getenv("FABRIC_WORKSPACE_NAME", "")).strip()
    if not workspace_name:
        print("[ERROR] Workspace name is required. Use --workspace-name or set FABRIC_WORKSPACE_NAME.")
        sys.exit(1)
    token = get_token("https://api.fabric.microsoft.com")
    # Find workspace by name
    r = fabric_api("GET", "workspaces", token)
    if r.status_code != 200:
        print(f"[ERROR] Failed to list Fabric workspaces: HTTP {r.status_code}")
        sys.exit(1)
    ws_id = None
    for ws in r.json().get("value", []):
        if ws.get("displayName") == workspace_name:
            ws_id = ws.get("id")
            break
    if not ws_id:
        print(f"[INFO] Fabric workspace '{workspace_name}' not found. Nothing to delete.")
        sys.exit(0)
    if args.what_if:
        print(f"[INFO] Would delete Fabric workspace '{workspace_name}' (id: {ws_id})")
        sys.exit(0)
    # Attempt to delete workspace
    delr = fabric_api("DELETE", f"workspaces/{ws_id}", token)
    if delr.status_code in (200, 202, 204):
        print(f"[OK] Fabric workspace '{workspace_name}' deleted (id: {ws_id})")
        sys.exit(0)
    print(f"[ERROR] Failed to delete Fabric workspace: HTTP {delr.status_code} {delr.text}")
    sys.exit(1)

if __name__ == "__main__":
    main()
