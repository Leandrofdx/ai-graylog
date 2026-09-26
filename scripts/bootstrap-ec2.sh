#!/usr/bin/env bash
# Bootstrap Graylog + sample-app em EC2 (Amazon Linux 2/2023 ou Ubuntu).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

if [[ "$(id -u)" -ne 0 ]]; then
  SUDO="sudo"
else
  SUDO=""
fi

detect_public_ip() {
  local token="" ip=""
  token="$(curl -fsS --max-time 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null || true)"
  if [[ -n "$token" ]]; then
    ip="$(curl -fsS --max-time 3 -H "X-aws-ec2-metadata-token: ${token}" \
      http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  else
    ip="$(curl -fsS --max-time 3 http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
  if [[ -z "$ip" ]]; then
    ip="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '\r\n' || true)"
  fi
  echo "${ip:-127.0.0.1}"
}

install_compose_plugin() {
  if docker compose version >/dev/null 2>&1; then
    return 0
  fi
  echo "    Instalando Docker Compose plugin..."
  local arch
  arch="$(uname -m)"
  case "$arch" in
    x86_64|amd64) arch="x86_64" ;;
    aarch64|arm64) arch="aarch64" ;;
  esac
  $SUDO mkdir -p /usr/local/lib/docker/cli-plugins
  $SUDO curl -fsSL \
    "https://github.com/docker/compose/releases/download/v2.32.4/docker-compose-linux-${arch}" \
    -o /usr/local/lib/docker/cli-plugins/docker-compose
  $SUDO chmod +x /usr/local/lib/docker/cli-plugins/docker-compose
}

install_docker() {
  if command -v docker >/dev/null 2>&1; then
    install_compose_plugin
    return 0
  fi

  echo "==> Instalando Docker..."
  if command -v dnf >/dev/null 2>&1; then
    # Amazon Linux 2023+
    $SUDO dnf install -y docker
    $SUDO systemctl enable --now docker
  elif command -v amazon-linux-extras >/dev/null 2>&1; then
    # Amazon Linux 2
    $SUDO yum install -y docker || $SUDO amazon-linux-extras install docker -y
    $SUDO systemctl enable --now docker
  elif command -v yum >/dev/null 2>&1 && [[ -f /etc/os-release ]] && grep -qi 'amazon' /etc/os-release; then
    $SUDO yum install -y docker
    $SUDO systemctl enable --now docker
  elif command -v apt-get >/dev/null 2>&1; then
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
  else
    echo "Não sei instalar Docker nesta distro." >&2
    exit 1
  fi

  for u in ec2-user ubuntu "${SUDO_USER:-}"; do
    if [[ -n "$u" ]] && id "$u" >/dev/null 2>&1; then
      $SUDO usermod -aG docker "$u" || true
    fi
  done

  install_compose_plugin
}

echo "==> Detectando IP público..."
PUBLIC_IP="$(detect_public_ip)"
echo "    IP: ${PUBLIC_IP}"

echo "==> vm.max_map_count (OpenSearch)..."
$SUDO sysctl -w vm.max_map_count=262144 >/dev/null
$SUDO mkdir -p /etc/sysctl.d
echo "vm.max_map_count=262144" | $SUDO tee /etc/sysctl.d/99-graylog-opensearch.conf >/dev/null

install_docker

if ! $SUDO docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin não encontrado." >&2
  exit 1
fi

echo "==> Preparando .env..."
if [[ ! -f .env ]]; then
  cp .env.example .env
fi

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
