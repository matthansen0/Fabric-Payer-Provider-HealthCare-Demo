#!/usr/bin/env bash
set -euo pipefail

info() { echo "[prereq] $*"; }
warn() { echo "[prereq][warn] $*"; }

need_sudo=false
if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
  need_sudo=true
fi

run_root() {
  if [[ "$need_sudo" == true ]]; then
    sudo "$@"
  else
    "$@"
  fi
}

ensure_cmd() {
  local cmd="$1"
  command -v "$cmd" >/dev/null 2>&1
}

install_jq() {
  if ensure_cmd jq; then
    info "jq already installed"
    return
  fi
  info "Installing jq"
  run_root apt-get update -y
  run_root apt-get install -y jq
}

install_az_cli() {
  if ensure_cmd az; then
    info "Azure CLI already installed"
    return
  fi
  info "Installing Azure CLI"
  curl -sL https://aka.ms/InstallAzureCLIDeb | run_root bash
}

install_azd() {
  if ensure_cmd azd; then
    info "azd already installed"
    return
  fi
  info "Installing azd"
  curl -fsSL https://aka.ms/install-azd.sh | bash
  if ! ensure_cmd azd; then
    warn "azd installed but not on PATH in current shell. Restart shell and re-run if needed."
  fi
}

install_python_requests() {
  if python3 - <<'PY' >/dev/null 2>&1
import requests
PY
  then
    info "python requests already installed"
    return
  fi
  info "Installing python dependency: requests"
  python3 -m pip install --user requests
}

main() {
  info "Checking/installing CLI prerequisites"
  install_jq
  install_az_cli
  install_azd
  install_python_requests
  info "Prerequisite check complete"
  info "Next: run 'az login' and 'azd auth login --use-device-code'"
}

main "$@"
