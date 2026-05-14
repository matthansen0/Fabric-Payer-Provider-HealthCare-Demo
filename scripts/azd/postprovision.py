#!/usr/bin/env python3
"""
Post-provision bootstrap for azd.

Goals:
1) Create Foundry Project under the AI Services Hub
2) Create (or find) Fabric workspace and assign capacity
3) Optionally run Healthcare_Launcher notebook job if notebook exists

This script is intentionally idempotent and soft-fail: it logs actionable
manual fallback steps when an API shape differs by tenant/preview version.
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple

try:
    import requests
except ImportError:
    print("[ERROR] Missing dependency: requests")
    print("        Install with: python3 -m pip install requests")
    sys.exit(1)


@dataclass
class Cfg:
    subscription_id: str
    resource_group: str
    location: str
    hub_name: str
    project_name: str
    fabric_capacity_id: str
    fabric_workspace_name: str
    notebook_name: str
    turbo_deploy: bool
    turbo_setup_sku: str
    turbo_scale_down_sku: str
    notebook_poll_seconds: int
    notebook_max_minutes: int
    deploy_foundry_models: bool
    foundry_chat_deployment_name: str
    foundry_chat_model_name: str
    foundry_chat_model_version: str
    foundry_embedding_deployment_name: str
    foundry_embedding_model_name: str
    foundry_embedding_model_version: str


TERMINAL_STATES = {
    "completed",
    "succeeded",
    "success",
    "failed",
    "cancelled",
    "canceled",
    "error",
    "timedout",
    "timeout",
}


def run(cmd: list[str], check: bool = True) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"Command failed: {' '.join(cmd)}\n{p.stderr.strip()}")
    return p.stdout.strip()


def get_token(resource: str) -> str:
    return run(
        [
            "az",
            "account",
            "get-access-token",
            "--resource",
            resource,
            "--query",
            "accessToken",
            "-o",
            "tsv",
        ]
    )


def load_cfg() -> Cfg:
    # azd environment values are exported as env vars at hook runtime
    subscription_id = os.getenv("AZURE_SUBSCRIPTION_ID", "").strip()
    resource_group = os.getenv("AZURE_RESOURCE_GROUP", "").strip()
    location = os.getenv("LOCATION", "swedencentral").strip()
    hub_name = os.getenv("HUB_NAME", "").strip()
    project_name = os.getenv("PROJECT_NAME", "HealthcareDemo-HLS").strip()
    fabric_capacity_id = os.getenv("FABRIC_CAPACITY_ID", "").strip()

    # User-settable values (azd env set ...)
    fabric_workspace_name = os.getenv("FABRIC_WORKSPACE_NAME", "HealthcareDemo-WS").strip()
    notebook_name = os.getenv("FABRIC_LAUNCHER_NOTEBOOK_NAME", "Healthcare_Launcher").strip()
    turbo_deploy = os.getenv("TURBO_DEPLOY", "false").strip().lower() == "true"
    turbo_setup_sku = os.getenv("TURBO_SETUP_SKU", "F256").strip() or "F256"
    turbo_scale_down_sku = os.getenv("TURBO_SCALE_DOWN_SKU", "F64").strip() or "F64"
    notebook_poll_seconds = int(os.getenv("NOTEBOOK_RUN_POLL_SECONDS", "30"))
    notebook_max_minutes = int(os.getenv("NOTEBOOK_RUN_MAX_MINUTES", "240"))
    deploy_foundry_models = os.getenv("DEPLOY_FOUNDRY_MODELS", "true").strip().lower() == "true"
    foundry_chat_deployment_name = os.getenv("FOUNDRY_CHAT_DEPLOYMENT_NAME", "gpt-4o").strip()
    foundry_chat_model_name = os.getenv("FOUNDRY_CHAT_MODEL_NAME", "gpt-4o").strip()
    foundry_chat_model_version = os.getenv("FOUNDRY_CHAT_MODEL_VERSION", "2024-11-20").strip()
    foundry_embedding_deployment_name = os.getenv(
        "FOUNDRY_EMBEDDING_DEPLOYMENT_NAME",
        "text-embedding-ada-002",
    ).strip()
    foundry_embedding_model_name = os.getenv(
        "FOUNDRY_EMBEDDING_MODEL_NAME",
        "text-embedding-ada-002",
    ).strip()
    foundry_embedding_model_version = os.getenv(
        "FOUNDRY_EMBEDDING_MODEL_VERSION",
        "2",
    ).strip()

    missing = [
        name
        for name, val in [
            ("AZURE_SUBSCRIPTION_ID", subscription_id),
            ("AZURE_RESOURCE_GROUP", resource_group),
            ("HUB_NAME", hub_name),
            ("FABRIC_CAPACITY_ID", fabric_capacity_id),
        ]
        if not val
    ]
    if missing:
        raise ValueError(
            "Missing required environment values from azd outputs: " + ", ".join(missing)
        )

    return Cfg(
        subscription_id=subscription_id,
        resource_group=resource_group,
        location=location,
        hub_name=hub_name,
        project_name=project_name,
        fabric_capacity_id=fabric_capacity_id,
        fabric_workspace_name=fabric_workspace_name,
        notebook_name=notebook_name,
        turbo_deploy=turbo_deploy,
        turbo_setup_sku=turbo_setup_sku,
        turbo_scale_down_sku=turbo_scale_down_sku,
        notebook_poll_seconds=notebook_poll_seconds,
        notebook_max_minutes=notebook_max_minutes,
        deploy_foundry_models=deploy_foundry_models,
        foundry_chat_deployment_name=foundry_chat_deployment_name,
        foundry_chat_model_name=foundry_chat_model_name,
        foundry_chat_model_version=foundry_chat_model_version,
        foundry_embedding_deployment_name=foundry_embedding_deployment_name,
        foundry_embedding_model_name=foundry_embedding_model_name,
        foundry_embedding_model_version=foundry_embedding_model_version,
    )


def foundry_project_ensure(cfg: Cfg) -> None:
    _ensure_hub_project_management_enabled(cfg)

    mgmt_token = get_token("https://management.azure.com")

    if not _foundry_project_location_supported(cfg.location):
        print("[WARN] Foundry project ARM resource type is not available in this region.")
        print(f"       Region: {cfg.location}")
        print("       Skipping project ARM create to avoid false failures.")
        print("       Manual fallback: ai.azure.com -> + Create project -> use existing hub")
        return

    body = {
        "location": cfg.location,
        "identity": {"type": "SystemAssigned"},
        "properties": {
            "displayName": cfg.project_name,
            "description": "Healthcare Demo project (azd)"
        },
    }
    # Prefer provider-reported versions for this cloud/tenant and keep static fallbacks.
    provider_versions = _get_supported_foundry_project_api_versions()
    api_versions = provider_versions + [
        "2026-03-01",
        "2025-12-01",
        "2025-09-01",
        "2025-06-01",
        "2024-10-01",
        "2024-06-01-preview",
        "2023-10-01-preview",
    ]
    # Dedupe while preserving order.
    api_versions = list(dict.fromkeys(api_versions))
    last_resp = None
    for api_version in api_versions:
        url = (
            "https://management.azure.com/subscriptions/"
            f"{cfg.subscription_id}/resourceGroups/{cfg.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{cfg.hub_name}"
            f"/projects/{cfg.project_name}?api-version={api_version}"
        )
        r = requests.put(
            url,
            headers={
                "Authorization": f"Bearer {mgmt_token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=60,
        )
        last_resp = r
        if r.status_code in (200, 201, 202):
            print(f"[OK] Foundry project ensured: {cfg.project_name}")
            return

        # Region/type unsupported; no need to continue trying older API versions.
        if r.status_code == 400:
            try:
                err = r.json().get("error", {})
                code = str(err.get("code", "")).strip()
                if code == "NoRegisteredProviderFound":
                    break
            except Exception:
                pass

    print("[WARN] Could not create Foundry project via ARM API.")
    if last_resp is not None:
        print(f"       HTTP {last_resp.status_code}: {last_resp.text[:200]}")
    print("       Manual fallback: ai.azure.com -> + Create project -> use existing hub")


def _ensure_hub_project_management_enabled(cfg: Cfg) -> None:
    ai_services_id = os.getenv("AI_SERVICES_ID", "").strip()
    if not ai_services_id:
        ai_services_id = (
            f"/subscriptions/{cfg.subscription_id}/resourceGroups/{cfg.resource_group}"
            f"/providers/Microsoft.CognitiveServices/accounts/{cfg.hub_name}"
        )

    try:
        run(
            [
                "az",
                "resource",
                "update",
                "--ids",
                ai_services_id,
                "--api-version",
                "2025-06-01",
                "--set",
                "properties.allowProjectManagement=true",
            ],
            check=True,
        )
        print("[OK] Hub project management enabled")
    except Exception as ex:
        print("[WARN] Could not enable hub project management automatically.")
        print(f"       {ex}")


def _get_supported_foundry_project_api_versions() -> list[str]:
    try:
        raw = run(
            [
                "az",
                "provider",
                "show",
                "-n",
                "Microsoft.CognitiveServices",
                "--query",
                "resourceTypes[?resourceType=='accounts/projects'].apiVersions | [0]",
                "-o",
                "json",
            ],
            check=True,
        )
        data = json.loads(raw) if raw else []
        if isinstance(data, list):
            return [str(v).strip() for v in data if str(v).strip()]
    except Exception:
        pass
    return []


def ensure_foundry_model_deployments(cfg: Cfg) -> None:
    if not cfg.deploy_foundry_models:
        print("[INFO] Skipping Foundry model deployments (DEPLOY_FOUNDRY_MODELS=false)")
        return

    try:
        raw = run(
            [
                "az",
                "cognitiveservices",
                "account",
                "deployment",
                "list",
                "-g",
                cfg.resource_group,
                "-n",
                cfg.hub_name,
                "-o",
                "json",
            ],
            check=True,
        )
        existing = json.loads(raw) if raw else []
    except Exception as ex:
        print("[WARN] Could not list existing Foundry model deployments.")
        print(f"       {ex}")
        return

    existing_names = {str(item.get("name", "")).strip() for item in existing if isinstance(item, dict)}
    desired = [
        (
            cfg.foundry_chat_deployment_name,
            cfg.foundry_chat_model_name,
            cfg.foundry_chat_model_version,
        ),
        (
            cfg.foundry_embedding_deployment_name,
            cfg.foundry_embedding_model_name,
            cfg.foundry_embedding_model_version,
        ),
    ]

    for deployment_name, model_name, model_version in desired:
        if deployment_name in existing_names:
            print(f"[OK] Foundry model deployment exists: {deployment_name}")
            continue

        try:
            run(
                [
                    "az",
                    "cognitiveservices",
                    "account",
                    "deployment",
                    "create",
                    "-g",
                    cfg.resource_group,
                    "-n",
                    cfg.hub_name,
                    "--deployment-name",
                    deployment_name,
                    "--model-name",
                    model_name,
                    "--model-version",
                    model_version,
                    "--model-format",
                    "OpenAI",
                    "--sku-name",
                    "Standard",
                    "--sku-capacity",
                    "1",
                    "-o",
                    "json",
                ],
                check=True,
            )
            print(f"[OK] Foundry model deployed: {deployment_name}")
        except Exception as ex:
            print(f"[WARN] Could not deploy model '{deployment_name}'.")
            print(f"       {ex}")


def _signed_in_user_object_id() -> Optional[str]:
    try:
        user_oid = run(["az", "ad", "signed-in-user", "show", "--query", "id", "-o", "tsv"], check=True)
        return user_oid.strip() or None
    except Exception:
        return None


def _role_assignment_exists(scope: str, principal_id: str, role_name: str) -> bool:
    try:
        count = run(
            [
                "az",
                "role",
                "assignment",
                "list",
                "--scope",
                scope,
                "--assignee-object-id",
                principal_id,
                "--query",
                f"[?roleDefinitionName=='{role_name}'] | length(@)",
                "-o",
                "tsv",
            ],
            check=True,
        )
        return count.strip() not in ("", "0")
    except Exception:
        return False


def _ensure_role_assignment(scope: str, principal_id: str, principal_type: str, role_name: str) -> None:
    if _role_assignment_exists(scope, principal_id, role_name):
        print(f"[OK] Azure role exists: {role_name} -> {principal_id}")
        return

    try:
        run(
            [
                "az",
                "role",
                "assignment",
                "create",
                "--assignee-object-id",
                principal_id,
                "--assignee-principal-type",
                principal_type,
                "--role",
                role_name,
                "--scope",
                scope,
                "-o",
                "json",
            ],
            check=True,
        )
        print(f"[OK] Azure role assigned: {role_name} -> {principal_id}")
    except Exception as ex:
        print(f"[WARN] Could not assign Azure role '{role_name}' to {principal_id}.")
        print(f"       {ex}")


def ensure_azure_role_assignments() -> None:
    search_scope = os.getenv("SEARCH_SERVICE_ID", "").strip()
    ai_services_scope = os.getenv("AI_SERVICES_ID", "").strip()
    search_principal_id = os.getenv("SEARCH_SERVICE_PRINCIPAL_ID", "").strip()
    user_principal_id = _signed_in_user_object_id()

    if search_scope and search_principal_id:
        for role_name in (
            "Search Index Data Contributor",
            "Search Index Data Reader",
            "Search Service Contributor",
        ):
            _ensure_role_assignment(search_scope, search_principal_id, "ServicePrincipal", role_name)

    if search_scope and user_principal_id:
        for role_name in (
            "Search Index Data Contributor",
            "Search Index Data Reader",
            "Search Service Contributor",
        ):
            _ensure_role_assignment(search_scope, user_principal_id, "User", role_name)

    if ai_services_scope and search_principal_id:
        for role_name in (
            "Cognitive Services OpenAI User",
            "Cognitive Services OpenAI Contributor",
        ):
            _ensure_role_assignment(ai_services_scope, search_principal_id, "ServicePrincipal", role_name)


def ensure_fabric_workspace_role_assignments(workspace_id: str) -> None:
    token = get_token("https://api.fabric.microsoft.com")
    r = fabric_api("GET", f"workspaces/{workspace_id}/roleAssignments", token)
    if r.status_code != 200:
        print(f"[WARN] Could not read Fabric workspace role assignments (HTTP {r.status_code}).")
        return

    existing = set()
    for item in r.json().get("value", []):
        principal = item.get("principal") or {}
        principal_id = str(principal.get("id", "")).strip()
        role = str(item.get("role", "")).strip()
        if principal_id and role:
            existing.add((principal_id, role))

    desired = []
    search_principal_id = os.getenv("SEARCH_SERVICE_PRINCIPAL_ID", "").strip()
    ai_principal_id = os.getenv("AI_SERVICES_PRINCIPAL_ID", "").strip()
    if search_principal_id:
        desired.append((search_principal_id, "Contributor"))
    if ai_principal_id:
        desired.append((ai_principal_id, "Contributor"))

    for principal_id, role in desired:
        if (principal_id, role) in existing:
            print(f"[OK] Fabric workspace role exists: {role} -> {principal_id}")
            continue

        body = {
            "principal": {"id": principal_id, "type": "ServicePrincipal"},
            "role": role,
        }
        resp = fabric_api("POST", f"workspaces/{workspace_id}/roleAssignments", token, body)
        if resp.status_code in (200, 201, 202, 204):
            print(f"[OK] Fabric workspace role assigned: {role} -> {principal_id}")
        else:
            print(f"[WARN] Could not assign Fabric workspace role '{role}' to {principal_id}.")
            print(f"       HTTP {resp.status_code}: {resp.text[:200]}")


def _foundry_project_location_supported(location: str) -> bool:
    if not location:
        return True

    try:
        raw = run(
            [
                "az",
                "provider",
                "show",
                "-n",
                "Microsoft.CognitiveServices",
                "--query",
                "resourceTypes[?resourceType=='accounts/projects'].locations | [0]",
                "-o",
                "json",
            ],
            check=True,
        )
        locations = json.loads(raw) if raw else []
        if isinstance(locations, list):
            wanted = location.replace(" ", "").lower()
            for loc in locations:
                candidate = str(loc).replace(" ", "").lower()
                if candidate == wanted:
                    return True
            return False
    except Exception:
        # If provider metadata lookup fails, do not block project creation attempts.
        return True

    return True


def fabric_api(method: str, path: str, token: str, body: Optional[dict] = None) -> requests.Response:
    url = f"https://api.fabric.microsoft.com/v1/{path.lstrip('/')}"
    return requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )


def powerbi_api(method: str, path: str, token: str, body: Optional[dict] = None) -> requests.Response:
    url = f"https://api.powerbi.com/v1.0/myorg/{path.lstrip('/')}"
    return requests.request(
        method,
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json=body,
        timeout=60,
    )


def _response_error_code(resp: requests.Response) -> str:
    try:
        return str(resp.json().get("errorCode", "")).strip()
    except Exception:
        return ""


def _convert_ipynb_to_fabric_py(ipynb_path: Path) -> str:
    raw = json.loads(ipynb_path.read_text(encoding="utf-8-sig"))
    lines = ["# Fabric notebook source"]

    for cell in raw.get("cells", []):
        cell_type = cell.get("cell_type", "code")
        source = cell.get("source", [])
        content = "".join(source) if isinstance(source, list) else str(source)
        content = content.rstrip("\n")
        language = "markdown" if cell_type == "markdown" else "python"

        lines.append("")
        lines.append(f'# METADATA **{{"language":"{language}"}}**')
        lines.append("")
        if cell_type == "markdown":
            lines.append('# MARKDOWN **{"language":"markdown"}**')
            lines.append("")
            for line in content.split("\n"):
                lines.append(f"# {line}")
        else:
            lines.append(f'# CELL **{{"language":"{language}"}}**')
            lines.append("")
            lines.append(content)

    return "\n".join(lines) + "\n"


def _find_notebook_id(token: str, workspace_id: str, notebook_name: str) -> Optional[str]:
    r = fabric_api("GET", f"workspaces/{workspace_id}/items?type=Notebook", token)
    if r.status_code != 200:
        return None
    for it in r.json().get("value", []):
        if it.get("displayName") == notebook_name:
            return it.get("id")
    return None


def _get_item_definition_parts(token: str, workspace_id: str, item_id: str) -> list:
    r = fabric_api("POST", f"workspaces/{workspace_id}/items/{item_id}/getDefinition", token, {})
    if r.status_code == 200:
        payload = r.json() if r.content else {}
        return ((payload.get("definition") or {}).get("parts") or [])

    if r.status_code == 202:
        op_url = r.headers.get("Location") or r.headers.get("location")
        retry_after = int((r.headers.get("Retry-After") or "5").strip() or "5")
        if not op_url:
            return []
        for _ in range(12):
            time.sleep(max(retry_after, 2))
            op = requests.get(
                op_url,
                headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
                timeout=60,
            )
            if op.status_code != 200:
                continue
            payload = op.json() if op.content else {}
            parts = ((payload.get("definition") or {}).get("parts") or [])
            if parts:
                return parts
        return []

    return []


def _notebook_has_content(token: str, workspace_id: str, notebook_id: str) -> bool:
    parts = _get_item_definition_parts(token, workspace_id, notebook_id)
    if not parts:
        return False
    for part in parts:
        path = str(part.get("path", "")).strip().lower()
        payload = str(part.get("payload", ""))
        if path in ("notebook-content.py", "notebook-content.ipynb") and len(payload) > 32:
            return True
    return False


def _delete_notebook_item(token: str, workspace_id: str, notebook_id: str) -> None:
    d = fabric_api("DELETE", f"workspaces/{workspace_id}/items/{notebook_id}", token)
    if d.status_code not in (200, 202, 204):
        print(f"[WARN] Could not delete invalid notebook item (HTTP {d.status_code}).")


def _wait_for_notebook_with_content(
    token: str,
    workspace_id: str,
    notebook_name: str,
    attempt_label: str,
    poll_count: int = 6,
    poll_interval: float = 3.0,
) -> Optional[str]:
    """
    Poll until a notebook with the given name appears in the workspace AND has content.
    Returns the notebook ID on success, or None if it never materialises.
    """
    for _ in range(poll_count):
        notebook_id = _find_notebook_id(token, workspace_id, notebook_name)
        if notebook_id:
            if _notebook_has_content(token, workspace_id, notebook_id):
                print(f"[OK] Imported notebook '{notebook_name}' ({attempt_label}).")
                return notebook_id
            # Exists but empty — delete and stop polling; caller will decide next step.
            print(
                f"[INFO] {attempt_label}: notebook appeared but definition is empty; "
                "deleting and retrying next attempt..."
            )
            _delete_notebook_item(token, workspace_id, notebook_id)
            return None
        time.sleep(poll_interval)
    return None


def _post_with_name_retry(
    path: str, token: str, body: dict, label: str
) -> Optional[requests.Response]:
    """POST with retries specifically for ItemDisplayNameNotAvailableYet 409s."""
    r = None
    for submit_try in range(1, 7):
        r = fabric_api("POST", path, token, body)
        err_code = _response_error_code(r)
        if r.status_code == 409 and err_code == "ItemDisplayNameNotAvailableYet":
            wait_sec = min(5 * submit_try, 30)
            print(
                f"[INFO] {label}: display name still reserving after delete; "
                f"retrying in {wait_sec}s..."
            )
            time.sleep(wait_sec)
            continue
        break
    return r


def _try_update_definition(token: str, workspace_id: str, notebook_id: str, notebook_b64: str) -> bool:
    """
    Two-step approach recommended by Microsoft for CI/CD:
    call POST /items/{id}/updateDefinition with the full ipynb payload.
    Returns True if the definition was accepted and has content afterwards.
    """
    body = {
        "definition": {
            "format": "ipynb",
            "parts": [
                {
                    "path": "artifact.content.ipynb",
                    "payload": notebook_b64,
                    "payloadType": "InlineBase64",
                }
            ],
        }
    }
    r = fabric_api(
        "POST",
        f"workspaces/{workspace_id}/items/{notebook_id}/updateDefinition",
        token,
        body,
    )
    if r.status_code not in (200, 202):
        print(
            f"[INFO] updateDefinition failed: HTTP {r.status_code} "
            f"{_response_error_code(r) or ''}".rstrip()
        )
        return False

    if r.status_code == 202:
        op_url = r.headers.get("Location") or r.headers.get("location")
        retry_after = int((r.headers.get("Retry-After") or "5").strip() or "5")
        if op_url:
            for _ in range(12):
                time.sleep(max(retry_after, 2))
                op = requests.get(
                    op_url,
                    headers={
                        "Authorization": f"Bearer {token}",
                        "Content-Type": "application/json",
                    },
                    timeout=60,
                )
                if op.status_code == 200:
                    break

    return _notebook_has_content(token, workspace_id, notebook_id)


def try_import_launcher_notebook(cfg: Cfg, token: str, workspace_id: str) -> Tuple[bool, bool]:
    """
    Try to import Healthcare_Launcher notebook automatically.

    Attempts (in order):
      1. Single-step POST /items with format=ipynb + artifact.content.ipynb part
         (correct Import path per Microsoft API guidance).
      2. Two-step: create empty notebook then POST /items/{id}/updateDefinition
         (recommended by Microsoft as most reliable for CI/CD).
      3. Legacy .platform + notebook-content.py parts format (fallback).

    Returns:
      (imported, feature_unavailable)
    """
    repo_root = Path(__file__).resolve().parents[2]
    launcher_path = repo_root / "Healthcare_Launcher.ipynb"
    if not launcher_path.exists():
        print(f"[WARN] Launcher notebook file not found on disk: {launcher_path}")
        return False, False

    notebook_b64 = base64.b64encode(launcher_path.read_bytes()).decode("utf-8")
    print(f"[INFO] Encoded notebook (ipynb format): {len(notebook_b64)} bytes of Base64")

    notebook_py = _convert_ipynb_to_fabric_py(launcher_path)
    notebook_py_b64 = base64.b64encode(notebook_py.encode("utf-8")).decode("utf-8")
    print(f"[INFO] Encoded notebook (py format): {len(notebook_py_b64)} bytes of Base64")

    platform = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/gitIntegration/platformProperties/2.0.0/schema.json",
        "metadata": {"type": "Notebook", "displayName": cfg.notebook_name},
        "config": {"version": "2.0", "logicalId": str(uuid.uuid4())},
    }
    platform_b64 = base64.b64encode(json.dumps(platform, indent=2).encode("utf-8")).decode("utf-8")
    print(f"[INFO] Encoded platform metadata: {len(platform_b64)} bytes of Base64")

    saw_feature_unavailable = False

    # ── Attempt 1: single-step POST with format=ipynb + artifact.content.ipynb ──
    # Microsoft confirmed: to trigger the "Import" code path (vs the empty "Create"
    # code path), the definition must include format="ipynb" and the part path must
    # be "artifact.content.ipynb".
    attempt1_label = "attempt 1 (ipynb import)"
    stale_id = _find_notebook_id(token, workspace_id, cfg.notebook_name)
    if stale_id and not _notebook_has_content(token, workspace_id, stale_id):
        print(f"[INFO] Existing empty notebook found. Replacing for {attempt1_label}...")
        _delete_notebook_item(token, workspace_id, stale_id)

    print(f"[INFO] Import {attempt1_label}: POST workspaces/{workspace_id}/items")
    print(f"       Definition parts: artifact.content.ipynb  (format=ipynb)")
    r1 = _post_with_name_retry(
        f"workspaces/{workspace_id}/items",
        token,
        {
            "type": "Notebook",
            "displayName": cfg.notebook_name,
            "definition": {
                "format": "ipynb",
                "parts": [
                    {
                        "path": "artifact.content.ipynb",
                        "payload": notebook_b64,
                        "payloadType": "InlineBase64",
                    }
                ],
            },
        },
        attempt1_label,
    )
    if r1 is not None and r1.status_code in (200, 201, 202):
        print(f"[INFO] HTTP {r1.status_code} — waiting for notebook to materialise...")
        nb_id = _wait_for_notebook_with_content(token, workspace_id, cfg.notebook_name, attempt1_label)
        if nb_id:
            return True, False
        print(f"[INFO] {attempt1_label} accepted but notebook did not materialise with content.")
    elif r1 is not None:
        err_code = _response_error_code(r1)
        if err_code == "FeatureNotAvailable":
            saw_feature_unavailable = True
        else:
            print(f"[INFO] {attempt1_label} failed: HTTP {r1.status_code} {err_code or ''}".rstrip())

    # ── Attempt 2: two-step create-then-updateDefinition (Microsoft CI/CD pattern) ──
    attempt2_label = "attempt 2 (create + updateDefinition)"
    stale_id = _find_notebook_id(token, workspace_id, cfg.notebook_name)
    if stale_id:
        if _notebook_has_content(token, workspace_id, stale_id):
            print(f"[OK] Notebook already has content after attempt 1 cleanup; done.")
            return True, False
        _delete_notebook_item(token, workspace_id, stale_id)

    print(f"[INFO] Import {attempt2_label}: creating empty notebook shell...")
    r2 = _post_with_name_retry(
        f"workspaces/{workspace_id}/items",
        token,
        {"type": "Notebook", "displayName": cfg.notebook_name},
        attempt2_label,
    )
    if r2 is not None and r2.status_code in (200, 201, 202):
        # Wait for the empty notebook to appear
        nb_id = None
        for _ in range(8):
            nb_id = _find_notebook_id(token, workspace_id, cfg.notebook_name)
            if nb_id:
                break
            time.sleep(3)

        if nb_id:
            print(f"[INFO] {attempt2_label}: shell created ({nb_id}). Pushing definition...")
            if _try_update_definition(token, workspace_id, nb_id, notebook_b64):
                print(f"[OK] Imported notebook '{cfg.notebook_name}' ({attempt2_label}).")
                return True, False
            print(f"[INFO] {attempt2_label}: updateDefinition did not produce content.")
            _delete_notebook_item(token, workspace_id, nb_id)
        else:
            print(f"[INFO] {attempt2_label}: empty shell never appeared in workspace.")
    elif r2 is not None:
        err_code = _response_error_code(r2)
        if err_code == "FeatureNotAvailable":
            saw_feature_unavailable = True
        else:
            print(f"[INFO] {attempt2_label} failed: HTTP {r2.status_code} {err_code or ''}".rstrip())

    # ── Attempt 3: legacy .platform + notebook-content.py format ──
    attempt3_label = "attempt 3 (.platform + notebook-content.py)"
    stale_id = _find_notebook_id(token, workspace_id, cfg.notebook_name)
    if stale_id and not _notebook_has_content(token, workspace_id, stale_id):
        _delete_notebook_item(token, workspace_id, stale_id)

    print(f"[INFO] Import {attempt3_label}: POST workspaces/{workspace_id}/items")
    print(f"       Definition parts: .platform, notebook-content.py")
    r3 = _post_with_name_retry(
        f"workspaces/{workspace_id}/items",
        token,
        {
            "type": "Notebook",
            "displayName": cfg.notebook_name,
            "definition": {
                "parts": [
                    {"path": ".platform", "payload": platform_b64, "payloadType": "InlineBase64"},
                    {"path": "notebook-content.py", "payload": notebook_py_b64, "payloadType": "InlineBase64"},
                ]
            },
        },
        attempt3_label,
    )
    if r3 is not None and r3.status_code in (200, 201, 202):
        print(f"[INFO] HTTP {r3.status_code} — waiting for notebook to materialise...")
        nb_id = _wait_for_notebook_with_content(token, workspace_id, cfg.notebook_name, attempt3_label)
        if nb_id:
            return True, False
        print(f"[INFO] {attempt3_label} accepted but notebook did not materialise with content.")
    elif r3 is not None:
        err_code = _response_error_code(r3)
        if err_code == "FeatureNotAvailable":
            saw_feature_unavailable = True
        else:
            print(f"[INFO] {attempt3_label} failed: HTTP {r3.status_code} {err_code or ''}".rstrip())

    return False, saw_feature_unavailable


def fabric_workspace_ensure(cfg: Cfg) -> Optional[str]:
    token = get_token("https://api.fabric.microsoft.com")

    # 1) Find workspace by name if it exists
    r = fabric_api("GET", "workspaces", token)
    if r.status_code == 200:
        for ws in r.json().get("value", []):
            if ws.get("displayName") == cfg.fabric_workspace_name:
                ws_id = ws.get("id")
                print(f"[OK] Fabric workspace exists: {cfg.fabric_workspace_name} ({ws_id})")
                assign_capacity(cfg, token, ws_id)
                return ws_id

    # 2) Create workspace
    create_payloads = [
        {"displayName": cfg.fabric_workspace_name, "capacityId": cfg.fabric_capacity_id},
        {"displayName": cfg.fabric_workspace_name},
    ]
    ws_id = None
    for payload in create_payloads:
        c = fabric_api("POST", "workspaces", token, payload)
        if c.status_code in (200, 201, 202):
            data = c.json() if c.content else {}
            ws_id = data.get("id")
            if ws_id:
                break
        else:
            print(f"[INFO] workspace create attempt failed: HTTP {c.status_code}")

    if not ws_id:
        print("[WARN] Could not create Fabric workspace automatically.")
        print("       Manual fallback: app.fabric.microsoft.com -> New workspace")
        print(f"       Name: {cfg.fabric_workspace_name}")
        print(f"       Capacity: {cfg.fabric_capacity_id}")
        return None

    print(f"[OK] Fabric workspace created: {cfg.fabric_workspace_name} ({ws_id})")
    assign_capacity(cfg, token, ws_id)
    return ws_id


def _extract_capacity_name_from_arm_id(capacity_id_or_arm_id: str) -> str:
    marker = "/providers/Microsoft.Fabric/capacities/"
    if marker in capacity_id_or_arm_id:
        return capacity_id_or_arm_id.split(marker, 1)[1].strip("/")
    return ""


def _resolve_capacity_guid(cfg: Cfg, token: str) -> Optional[str]:
    raw = (cfg.fabric_capacity_id or "").strip()
    if not raw:
        return None

    # If already GUID-like, use directly.
    if len(raw) == 36 and raw.count("-") == 4:
        return raw

    desired_name = _extract_capacity_name_from_arm_id(raw)
    caps = fabric_api("GET", "capacities", token)
    if caps.status_code != 200:
        print(f"[WARN] Could not list Fabric capacities to resolve GUID (HTTP {caps.status_code}).")
        return None

    values = caps.json().get("value", [])
    if desired_name:
        for cap in values:
            if str(cap.get("displayName", "")).strip().lower() == desired_name.lower():
                cid = str(cap.get("id", "")).strip()
                if cid:
                    return cid

    # Fallback: exact id match (some tenants may return ARM-like IDs).
    for cap in values:
        cid = str(cap.get("id", "")).strip()
        if cid == raw:
            return cid
    return None


def _verify_workspace_on_dedicated_capacity(workspace_id: str, expected_capacity_id: Optional[str]) -> bool:
    pbi_token = get_token("https://analysis.windows.net/powerbi/api")
    r = powerbi_api("GET", f"groups/{workspace_id}", pbi_token)
    if r.status_code != 200:
        print(f"[WARN] Could not verify workspace capacity via Power BI API (HTTP {r.status_code}).")
        return False
    payload = r.json() if r.content else {}
    on_dedicated = bool(payload.get("isOnDedicatedCapacity"))
    current_capacity_id = str(payload.get("capacityId", "")).strip()
    if not on_dedicated:
        return False
    if expected_capacity_id and current_capacity_id and current_capacity_id != expected_capacity_id:
        print(
            "[WARN] Workspace is on dedicated capacity but not expected target: "
            f"{current_capacity_id} != {expected_capacity_id}"
        )
        return False
    return True


def assign_capacity(cfg: Cfg, token: str, workspace_id: str) -> None:
    target_capacity_id = _resolve_capacity_guid(cfg, token)
    if not target_capacity_id:
        print("[WARN] Could not resolve Fabric capacity GUID from configured FABRIC_CAPACITY_ID.")
        print("       Manual fallback: assign workspace to desired capacity in Fabric UI.")
        return

    attempts = [
        (
            "POST",
            f"workspaces/{workspace_id}/assignToCapacity",
            {"capacityId": target_capacity_id},
        ),
        (
            "PATCH",
            f"workspaces/{workspace_id}",
            {"capacityId": target_capacity_id},
        ),
    ]
    for method, path, body in attempts:
        r = fabric_api(method, path, token, body)
        if r.status_code in (200, 201, 202, 204):
            if _verify_workspace_on_dedicated_capacity(workspace_id, target_capacity_id):
                print(f"[OK] Workspace assigned to capacity: {target_capacity_id}")
                return

    # Fallback through Power BI endpoint, then verify.
    pbi_token = get_token("https://analysis.windows.net/powerbi/api")
    pbi_assign = powerbi_api(
        "POST",
        f"groups/{workspace_id}/AssignToCapacity",
        pbi_token,
        {"capacityId": target_capacity_id},
    )
    if pbi_assign.status_code in (200, 202):
        if _verify_workspace_on_dedicated_capacity(workspace_id, target_capacity_id):
            print(f"[OK] Workspace assigned to capacity: {target_capacity_id}")
            return

    print("[WARN] Could not assign workspace to capacity using API variants.")
    print("       Manual fallback: Fabric workspace settings -> License info -> assign capacity")


def run_launcher_notebook_if_present(cfg: Cfg, workspace_id: str) -> Tuple[Optional[str], Optional[str]]:
    token = get_token("https://api.fabric.microsoft.com")

    notebook_id = _find_notebook_id(token, workspace_id, cfg.notebook_name)
    if notebook_id and not _notebook_has_content(token, workspace_id, notebook_id):
        print(f"[WARN] Notebook '{cfg.notebook_name}' exists but is empty. Re-importing...")
        _delete_notebook_item(token, workspace_id, notebook_id)
        notebook_id = None

    items = fabric_api("GET", f"workspaces/{workspace_id}/items?type=Notebook", token)
    if items.status_code != 200:
        print("[WARN] Could not query notebooks in workspace.")
        return None, None

    if not notebook_id:
        for it in items.json().get("value", []):
            if it.get("displayName") == cfg.notebook_name:
                notebook_id = it.get("id")
                break

    if not notebook_id:
        print(f"[INFO] Notebook '{cfg.notebook_name}' not found in workspace. Trying auto-import...")
        imported, feature_unavailable = try_import_launcher_notebook(cfg, token, workspace_id)

        if imported:
            items = fabric_api("GET", f"workspaces/{workspace_id}/items?type=Notebook", token)
            if items.status_code == 200:
                for it in items.json().get("value", []):
                    if it.get("displayName") == cfg.notebook_name:
                        notebook_id = it.get("id")
                        break

        if not notebook_id:
            print(f"[WARN] Notebook '{cfg.notebook_name}' is still missing in workspace.")
            if feature_unavailable:
                print("       Tenant/API returned 'FeatureNotAvailable' for notebook create/import.")
                print("       One-time manual step: Fabric workspace -> Import -> Notebook -> Healthcare_Launcher.ipynb")
            else:
                print("       Auto-import attempt failed due to API shape/permissions in this tenant.")
                print("       Manual fallback: Fabric workspace -> Import -> Notebook -> Healthcare_Launcher.ipynb")
            print("       Then rerun: python3 scripts/azd/postprovision.py")
            return None, None

    # Try two known job endpoint shapes
    attempts = [
        (
            "POST",
            f"workspaces/{workspace_id}/items/{notebook_id}/jobs/instances?jobType=RunNotebook",
            {},
        ),
        (
            "POST",
            f"workspaces/{workspace_id}/notebooks/{notebook_id}/jobs/instances?jobType=RunNotebook",
            {},
        ),
    ]

    for method, path, body in attempts:
        r = fabric_api(method, path, token, body)
        if r.status_code in (200, 201, 202):
            payload = r.json() if r.content else {}
            job_id = payload.get("id")
            if job_id:
                print(f"[OK] Notebook run started: {cfg.notebook_name} (job: {job_id})")
                return notebook_id, job_id

            # Some tenants only return a Location header for async jobs.
            job_id = _extract_job_id_from_location_header(r)
            if job_id:
                print(f"[OK] Notebook run started: {cfg.notebook_name} (job: {job_id})")
                return notebook_id, job_id

            # Last-resort: infer job id from most recent RunNotebook job instance.
            job_id = _find_latest_run_notebook_job_id(token, workspace_id, notebook_id)
            if job_id:
                print(f"[OK] Notebook run started: {cfg.notebook_name} (job: {job_id})")
                return notebook_id, job_id

            print(f"[OK] Notebook run submitted: {cfg.notebook_name} (job id not returned by API)")
            print("[INFO] Skipping status wait because this tenant response omitted job id.")
            return notebook_id, None

    print("[WARN] Could not start notebook run via API variants.")
    print("       Manual fallback: open workspace notebook and click Run All.")
    return notebook_id, None


def extract_status(payload: dict) -> str:
    for key in ("status", "state", "lifecycleState", "executionState", "jobState"):
        val = payload.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip().lower()
    # Some APIs return nested status object
    status_obj = payload.get("status")
    if isinstance(status_obj, dict):
        for key in ("state", "status"):
            val = status_obj.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip().lower()
    return "unknown"


def _extract_job_id_from_location_header(resp: requests.Response) -> Optional[str]:
    location = resp.headers.get("Location") or resp.headers.get("location")
    if not location:
        return None
    m = re.search(r"/jobs/instances/([0-9a-fA-F-]{36})", location)
    if m:
        return m.group(1)
    return None


def _job_list_sort_key(job: dict) -> str:
    for key in (
        "lastUpdatedTimeUtc",
        "lastUpdatedTime",
        "endTimeUtc",
        "startTimeUtc",
        "startTime",
        "createdDateTime",
        "createdTimeUtc",
    ):
        val = job.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def _find_latest_run_notebook_job_id(token: str, workspace_id: str, notebook_id: str) -> Optional[str]:
    paths = [
        f"workspaces/{workspace_id}/items/{notebook_id}/jobs/instances?jobType=RunNotebook",
        f"workspaces/{workspace_id}/items/{notebook_id}/jobs/instances",
        f"workspaces/{workspace_id}/notebooks/{notebook_id}/jobs/instances?jobType=RunNotebook",
        f"workspaces/{workspace_id}/notebooks/{notebook_id}/jobs/instances",
    ]
    candidates = []
    for path in paths:
        r = fabric_api("GET", path, token)
        if r.status_code != 200:
            continue
        payload = r.json() if r.content else {}
        values = payload.get("value") if isinstance(payload, dict) else None
        if isinstance(values, list):
            candidates.extend([j for j in values if isinstance(j, dict)])

    if not candidates:
        return None

    run_jobs = [j for j in candidates if str(j.get("jobType", "")).lower() == "runnotebook"]
    if run_jobs:
        candidates = run_jobs

    candidates.sort(key=_job_list_sort_key, reverse=True)
    for job in candidates:
        jid = str(job.get("id", "")).strip()
        if jid:
            return jid
    return None


def get_job_status(token: str, workspace_id: str, notebook_id: str, job_id: str) -> Optional[dict]:
    paths = [
        f"workspaces/{workspace_id}/items/{notebook_id}/jobs/instances/{job_id}",
        f"workspaces/{workspace_id}/notebooks/{notebook_id}/jobs/instances/{job_id}",
        f"workspaces/{workspace_id}/items/{notebook_id}/jobs/instances/{job_id}?jobType=RunNotebook",
    ]
    for path in paths:
        r = fabric_api("GET", path, token)
        if r.status_code == 200:
            return r.json() if r.content else {}
    return None


def wait_for_notebook_completion(
    cfg: Cfg,
    workspace_id: str,
    notebook_id: str,
    job_id: str,
) -> Optional[str]:
    token = get_token("https://api.fabric.microsoft.com")
    max_seconds = cfg.notebook_max_minutes * 60
    interval = max(cfg.notebook_poll_seconds, 10)

    print("[INFO] Waiting for notebook completion...")
    print(f"       Poll every {interval}s, timeout {cfg.notebook_max_minutes} minutes")
    print(f"       Tracking notebook job {job_id} for {cfg.notebook_name}")

    start = time.time()
    while True:
        elapsed = int(time.time() - start)
        elapsed_minutes = elapsed // 60
        status_payload = get_job_status(token, workspace_id, notebook_id, job_id)

        if status_payload is None:
            print(
                f"[STATUS] Working on {cfg.notebook_name} | running for {elapsed_minutes} minute(s)"
                f" | unable to fetch job status yet"
            )
        else:
            status = extract_status(status_payload)
            print(
                f"[STATUS] Working on {cfg.notebook_name} | running for {elapsed_minutes} minute(s)"
                f" | notebook job status: {status}"
            )
            if status in TERMINAL_STATES:
                return status

        if elapsed >= max_seconds:
            print("[WARN] Timed out waiting for notebook completion.")
            return None

        time.sleep(interval)


def scale_capacity_sku(cfg: Cfg, sku_name: str) -> bool:
    print(f"[INFO] Scaling Fabric capacity to {sku_name}...")
    try:
        # Use ARM resource update to avoid endpoint variations in az rest body contracts.
        run(
            [
                "az",
                "resource",
                "update",
                "--ids",
                cfg.fabric_capacity_id,
                "--api-version",
                "2023-11-01",
                "--set",
                f"sku.name={sku_name}",
                "sku.tier=Fabric",
            ]
        )
        print(f"[OK] Capacity scaled to {sku_name}")
        return True
    except Exception as ex:
        print(f"[WARN] Could not scale capacity automatically: {ex}")
        print("       Manual fallback command:")
        print(
            "       az resource update --ids "
            f"\"{cfg.fabric_capacity_id}\" --api-version 2023-11-01 "
            f"--set sku.name={sku_name} sku.tier=Fabric"
        )
        return False


def main() -> int:
    try:
        cfg = load_cfg()
    except Exception as ex:
        print(f"[ERROR] Config error: {ex}")
        return 1

    print("=== AZD post-provision bootstrap ===")
    print(f"Subscription: {cfg.subscription_id}")
    print(f"Region:       {cfg.location}")
    print(f"Hub:          {cfg.hub_name}")
    print(f"Project:      {cfg.project_name}")
    print(f"Workspace:    {cfg.fabric_workspace_name}")
    print(f"Turbo:        {cfg.turbo_deploy}")
    if cfg.turbo_deploy:
        print(f"Turbo SKU:    {cfg.turbo_setup_sku} -> {cfg.turbo_scale_down_sku}")

    # Verify az exists early
    try:
        run(["az", "version"], check=True)
    except Exception:
        print("[ERROR] Azure CLI not found. Install az CLI before running postprovision.")
        return 1

    print("[STEP] Ensuring Foundry project")
    foundry_project_ensure(cfg)
    print("[STEP] Ensuring Foundry model deployments")
    ensure_foundry_model_deployments(cfg)
    print("[STEP] Ensuring Azure role assignments")
    ensure_azure_role_assignments()
    print("[STEP] Ensuring Fabric workspace")
    ws_id = fabric_workspace_ensure(cfg)
    if ws_id:
        print("[STEP] Ensuring Fabric workspace role assignments")
        ensure_fabric_workspace_role_assignments(ws_id)
        print(f"[STEP] Starting launcher notebook {cfg.notebook_name} if present")
        notebook_id, job_id = run_launcher_notebook_if_present(cfg, ws_id)
        if notebook_id and job_id:
            final_status = wait_for_notebook_completion(cfg, ws_id, notebook_id, job_id)
            if final_status:
                print(f"[INFO] Notebook run finished with status: {final_status}")

            if cfg.turbo_deploy:
                # Scale down after notebook reaches terminal state (success or failure).
                if final_status is not None:
                    scale_capacity_sku(cfg, cfg.turbo_scale_down_sku)
                else:
                    print("[WARN] Turbo mode enabled but notebook status timed out.")
                    print("       Leaving capacity at turbo SKU to avoid premature scale-down.")
                    print("       Re-run postprovision later or scale manually.")
        elif cfg.turbo_deploy:
            print("[WARN] Turbo mode enabled but notebook did not start.")
            print("       Capacity remains at turbo SKU until notebook run completes.")
            print("       After manual run, execute:")
            print("       python3 scripts/azd/postprovision.py")

    print("=== Done ===")
    return 0


if __name__ == "__main__":
    sys.exit(main())
