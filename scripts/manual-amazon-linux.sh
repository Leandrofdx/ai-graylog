#!/usr/bin/env bash
# Comandos manuais para Amazon Linux 2023 — cole tudo de uma vez na EC2.
set -euo pipefail

PUBLIC_IP="${PUBLIC_IP:-$(curl -fsS https://checkip.amazonaws.com | tr -d '\r\n')}"
REPO_DIR="${HOME}/ai-graylog"

echo "==> IP: ${PUBLIC_IP}"

echo "==> Docker..."
sudo dnf install -y --allowerasing --setopt=install_weak_deps=False docker
sudo systemctl enable --now docker
sudo usermod -aG docker ec2-user || true

echo "==> Compose plugin..."
sudo mkdir -p /usr/local/lib/docker/cli-plugins
ARCH="$(uname -m)"
case "$ARCH" in
  aarch64|arm64) ARCH=aarch64 ;;
  *) ARCH=x86_64 ;;
esac
sudo curl -fsSL \
  "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${ARCH}" \
  -o /usr/local/lib/docker/cli-plugins/docker-compose
sudo chmod +x /usr/local/lib/docker/cli-plugins/docker-compose

echo "==> OpenSearch sysctl..."
sudo sysctl -w vm.max_map_count=262144
echo "vm.max_map_count=262144" | sudo tee /etc/sysctl.d/99-graylog-opensearch.conf >/dev/null

echo "==> Git + clone..."
if ! command -v git >/dev/null 2>&1; then
  sudo dnf install -y --setopt=install_weak_deps=False --exclude=curl git
fi
rm -rf "${REPO_DIR}"
git clone https://github.com/Leandrofdx/ai-graylog.git "${REPO_DIR}"
cd "${REPO_DIR}"

cp -n .env.example .env || cp .env.example .env
sed -i "s|^GRAYLOG_HTTP_EXTERNAL_URI=.*|GRAYLOG_HTTP_EXTERNAL_URI=http://${PUBLIC_IP}:9000/|" .env
sed -i 's|^OPENSEARCH_JAVA_OPTS=.*|OPENSEARCH_JAVA_OPTS=-Xms512m -Xmx512m|' .env
if grep -q 'change-me-to-at-least-16-chars' .env; then
  SECRET="$(openssl rand -base64 32 | tr -d '\n=/' | head -c 48)"
  sed -i "s|^GRAYLOG_PASSWORD_SECRET=.*|GRAYLOG_PASSWORD_SECRET=${SECRET}|" .env
fi

echo "==> Subindo stack (pode demorar no pull das imagens)..."
sudo docker compose up -d --build

echo
sudo docker compose ps
echo
echo "Graylog: http://${PUBLIC_IP}:9000  (admin/admin)"
echo "App:     http://${PUBLIC_IP}:8080"
echo "Teste:   curl -s http://127.0.0.1:8080/health"
