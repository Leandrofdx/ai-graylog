#!/usr/bin/env bash
# Bootstrap Graylog + sample-app em Ubuntu EC2 (teste barato).
# Uso (na EC2, como ubuntu ou com sudo):
#   curl -fsSL ... | bash
#   ou: bash scripts/bootstrap-ec2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

echo "==> Detectando IP público..."
PUBLIC_IP="$(curl -fsS --max-time 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
if [[ -z "${PUBLIC_IP}" ]]; then
  PUBLIC_IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '\n' || true)"
fi
if [[ -z "${PUBLIC_IP}" ]]; then
  PUBLIC_IP="127.0.0.1"
  echo "!! Não achei IP público; usando 127.0.0.1"
else
  echo "    IP: ${PUBLIC_IP}"
fi

echo "==> vm.max_map_count (OpenSearch)..."
$SUDO sysctl -w vm.max_map_count=262144 >/dev/null
echo "vm.max_map_count=262144" | $SUDO tee /etc/sysctl.d/99-graylog-opensearch.conf >/dev/null

echo "==> Instalando Docker (se necessário)..."
if ! command -v docker >/dev/null 2>&1; then
  $SUDO apt-get update -y
  $SUDO apt-get install -y ca-certificates curl gnupg
  $SUDO install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | $SUDO gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  $SUDO chmod a+r /etc/apt/keyrings/docker.gpg
  echo \
    "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
    $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
    $SUDO tee /etc/apt/sources.list.d/docker.list >/dev/null
  $SUDO apt-get update -y
  $SUDO apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  $SUDO systemctl enable --now docker
  if id ubuntu >/dev/null 2>&1; then
    $SUDO usermod -aG docker ubuntu || true
  fi
  if [[ -n "${SUDO_USER:-}" ]]; then
    $SUDO usermod -aG docker "$SUDO_USER" || true
  fi
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin não encontrado." >&2
  exit 1
fi

echo "==> Preparando .env..."
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

# Instância fraca (t3.medium): heap menor
if grep -q '^OPENSEARCH_JAVA_OPTS=' .env; then
  sed -i 's|^OPENSEARCH_JAVA_OPTS=.*|OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m|' .env
else
  echo 'OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m' >> .env
fi

if grep -q '^GRAYLOG_HTTP_EXTERNAL_URI=' .env; then
  sed -i "s|^GRAYLOG_HTTP_EXTERNAL_URI=.*|GRAYLOG_HTTP_EXTERNAL_URI=http://${PUBLIC_IP}:9000/|" .env
else
  echo "GRAYLOG_HTTP_EXTERNAL_URI=http://${PUBLIC_IP}:9000/" >> .env
fi

# Garante secret >= 16 chars se ainda for o placeholder
if grep -q 'change-me-to-at-least-16-chars' .env; then
  SECRET="$(openssl rand -base64 32 | tr -d '\n=/' | head -c 48)"
  sed -i "s|^GRAYLOG_PASSWORD_SECRET=.*|GRAYLOG_PASSWORD_SECRET=${SECRET}|" .env
fi

echo "==> Subindo stack..."
$SUDO docker compose up -d --build

echo
echo "============================================"
echo " Pronto."
echo " Graylog:  http://${PUBLIC_IP}:9000  (admin/admin)"
echo " App:      http://${PUBLIC_IP}:8080"
echo " Libere no SG: 22 (seu IP), 9000 e 8080 (seu IP)"
echo "============================================"
