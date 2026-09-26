#!/usr/bin/env bash
# HTTPS com Let's Encrypt (certificado público) via Caddy.
# Pré-requisito: um DNS A apontando para o IP público da EC2.
#
# Uso na EC2:
#   export DOMAIN=graylog.seudominio.com
#   export EMAIL=seu@email.com
#   bash scripts/enable-https-letsencrypt.sh
set -euo pipefail

REPO_DIR="${REPO_DIR:-$HOME/ai-graylog}"
cd "${REPO_DIR}"

DOMAIN="${DOMAIN:-}"
EMAIL="${EMAIL:-admin@${DOMAIN}}"

if [[ -z "${DOMAIN}" ]]; then
  echo "Defina DOMAIN, ex: export DOMAIN=graylog.exemplo.com" >&2
  exit 1
fi

if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "==> Domínio: ${DOMAIN}"
echo "==> Email:   ${EMAIL}"

# Atualiza repo
if [[ -d .git ]]; then
  git pull --ff-only origin main || true
fi

# Para o nginx self-signed se estiver no ar (profile https)
$SUDO docker compose --profile https stop nginx 2>/dev/null || true
$SUDO docker compose --profile https rm -f nginx 2>/dev/null || true

# Caddyfile
mkdir -p caddy
cat > caddy/Caddyfile <<EOF
${DOMAIN} {
        encode gzip
        reverse_proxy graylog:9000
}
EOF

# .env Graylog
if [[ ! -f .env ]]; then
  cp .env.example .env
fi
sed -i "s|^GRAYLOG_HTTP_EXTERNAL_URI=.*|GRAYLOG_HTTP_EXTERNAL_URI=https://${DOMAIN}/|" .env
if ! grep -q '^GRAYLOG_HTTP_EXTERNAL_URI=' .env; then
  echo "GRAYLOG_HTTP_EXTERNAL_URI=https://${DOMAIN}/" >> .env
fi

echo "==> Subindo Caddy (Let's Encrypt) + Graylog..."
$SUDO docker compose --profile https-le up -d --build caddy graylog

echo
echo "============================================"
echo " Aguarde ~30s para o certificado ser emitido."
echo " UI:      https://${DOMAIN}/"
echo " MCP:     https://${DOMAIN}/api/mcp"
echo
echo " SG: TCP 80 e 443 abertos para 0.0.0.0/0 (Let's Encrypt valida na 80)."
echo " DNS A: ${DOMAIN} -> IP público da EC2"
echo "============================================"
echo
echo "Teste:"
echo "  curl -fsS -u admin:admin -H 'X-Requested-By: cli' https://${DOMAIN}/api/system/lbstatus"
