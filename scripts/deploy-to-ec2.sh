#!/usr/bin/env bash
# Do seu Mac: copia o projeto para a EC2 e roda o bootstrap.
# Uso:
#   ./scripts/deploy-to-ec2.sh ubuntu@IP_PUBLICO
#   ./scripts/deploy-to-ec2.sh ubuntu@IP_PUBLICO ~/.ssh/minha-chave.pem
set -euo pipefail

TARGET="${1:-}"
KEY="${2:-}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [[ -z "$TARGET" ]]; then
  echo "Uso: $0 ubuntu@IP_PUBLICO [caminho/chave.pem]" >&2
  exit 1
fi

SSH_OPTS=(-o StrictHostKeyChecking=accept-new)
if [[ -n "$KEY" ]]; then
  SSH_OPTS+=(-i "$KEY")
fi

REMOTE_DIR="~/ai-graylog"

echo "==> Enviando arquivos para ${TARGET}:${REMOTE_DIR}"
ssh "${SSH_OPTS[@]}" "$TARGET" "mkdir -p ${REMOTE_DIR}"
rsync -az --delete \
  --exclude '.env' \
  --exclude '.git' \
  --exclude '.DS_Store' \
  --exclude '*.log' \
  -e "ssh ${SSH_OPTS[*]}" \
  "$ROOT/" "${TARGET}:${REMOTE_DIR}/"

echo "==> Executando bootstrap na EC2..."
ssh "${SSH_OPTS[@]}" "$TARGET" "bash ${REMOTE_DIR}/scripts/bootstrap-ec2.sh"

echo "==> Deploy concluído."
