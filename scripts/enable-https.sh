#!/usr/bin/env bash
# Ativa Nginx HTTPS na frente do Graylog (self-signed no IP).
# Na EC2 (já com o stack rodando ou depois do clone):
#   curl -fsSL "https://raw.githubusercontent.com/Leandrofdx/ai-graylog/main/scripts/enable-https.sh?v=1" | bash
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/ai-graylog}"
cd "${REPO_DIR}"

if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

PUBLIC_IP="${PUBLIC_IP:-}"
if [[ -z "${PUBLIC_IP}" ]]; then
  PUBLIC_IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '\r\n' || true)"
fi
PUBLIC_IP="${PUBLIC_IP:-127.0.0.1}"
export PUBLIC_IP

echo "==> IP: ${PUBLIC_IP}"

# Garante repo atualizado
if [[ -d .git ]]; then
  git pull --ff-only origin main || true
fi

chmod +x scripts/*.sh
./scripts/generate-tls-cert.sh

if [[ ! -f .env ]]; then
  cp .env.example .env
fi

sed -i "s|^GRAYLOG_HTTP_EXTERNAL_URI=.*|GRAYLOG_HTTP_EXTERNAL_URI=https://${PUBLIC_IP}/|" .env
if ! grep -q '^GRAYLOG_HTTP_EXTERNAL_URI=' .env; then
  echo "GRAYLOG_HTTP_EXTERNAL_URI=https://${PUBLIC_IP}/" >> .env
fi

echo "==> Subindo Nginx + reiniciando Graylog com EXTERNAL_URI https..."
$SUDO docker compose --profile https up -d --build nginx graylog

echo
echo "============================================"
echo " HTTPS ativo (self-signed)."
echo " UI:       https://${PUBLIC_IP}/"
echo " MCP URL:  https://${PUBLIC_IP}/api/mcp"
echo
echo " Security Group: libere TCP 443 (e pode fechar 9000 público)."
echo " No DevOps Agent use: https://${PUBLIC_IP}/api/mcp"
echo
echo " Se a AWS rejeitar cert self-signed, aí precisa domínio + Let's Encrypt."
echo "============================================"
echo
echo "Teste local:"
echo "  curl -k -s -u admin:admin -H 'X-Requested-By: cli' https://127.0.0.1/api/system/lbstatus"
