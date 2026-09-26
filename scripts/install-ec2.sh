#!/usr/bin/env bash
# Instalação completa na EC2 (Amazon Linux 2023 / AL2 / Ubuntu).
#
#   curl -fsSL "https://raw.githubusercontent.com/Leandrofdx/ai-graylog/main/scripts/install-ec2.sh?v=4" | bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/Leandrofdx/ai-graylog.git}"
REPO_DIR="${REPO_DIR:-$HOME/ai-graylog}"
BRANCH="${BRANCH:-main}"

if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "==> [1/4] Pacotes base (sem tocar no pacote curl)..."
if command -v dnf >/dev/null 2>&1; then
  if ! command -v git >/dev/null 2>&1; then
    $SUDO dnf install -y --setopt=install_weak_deps=False --exclude=curl git
  fi
elif command -v yum >/dev/null 2>&1; then
  if ! command -v git >/dev/null 2>&1; then
    $SUDO yum install -y --exclude=curl git
  fi
elif command -v apt-get >/dev/null 2>&1; then
  $SUDO apt-get update -y
  $SUDO DEBIAN_FRONTEND=noninteractive apt-get install -y git curl ca-certificates
else
  echo "Distro não suportada." >&2
  exit 1
fi

command -v curl >/dev/null
command -v git >/dev/null

echo "==> [2/4] Clonando/atualizando ${REPO_DIR}..."
if [[ -d "${REPO_DIR}/.git" ]]; then
  git -C "${REPO_DIR}" fetch origin
  git -C "${REPO_DIR}" checkout "${BRANCH}"
  git -C "${REPO_DIR}" pull --ff-only origin "${BRANCH}"
else
  rm -rf "${REPO_DIR}"
  git clone --branch "${BRANCH}" "${REPO_URL}" "${REPO_DIR}"
fi

cd "${REPO_DIR}"
chmod +x scripts/*.sh

echo "==> [3/4] Bootstrap..."
bash "${REPO_DIR}/scripts/bootstrap-ec2.sh"

echo "==> [4/4] OK — pasta: ${REPO_DIR}"
