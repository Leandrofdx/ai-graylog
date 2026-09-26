#!/usr/bin/env bash
# Gera certificado TLS self-signed para o IP público (SAN=IP).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CERT_DIR="${ROOT}/nginx/certs"
mkdir -p "${CERT_DIR}"

PUBLIC_IP="${PUBLIC_IP:-}"
if [[ -z "${PUBLIC_IP}" ]]; then
  TOKEN="$(curl -fsS --max-time 2 -X PUT "http://169.254.169.254/latest/api/token" \
    -H "X-aws-ec2-metadata-token-ttl-seconds: 21600" 2>/dev/null || true)"
  if [[ -n "${TOKEN}" ]]; then
    PUBLIC_IP="$(curl -fsS --max-time 3 -H "X-aws-ec2-metadata-token: ${TOKEN}" \
      http://169.254.169.254/latest/meta-data/public-ipv4 2>/dev/null || true)"
  fi
fi
if [[ -z "${PUBLIC_IP}" ]]; then
  PUBLIC_IP="$(curl -fsS --max-time 5 https://checkip.amazonaws.com 2>/dev/null | tr -d '\r\n' || true)"
fi
PUBLIC_IP="${PUBLIC_IP:-127.0.0.1}"

echo "==> Gerando cert self-signed para IP ${PUBLIC_IP}"

OPENSSL_CONF="$(mktemp)"
cat > "${OPENSSL_CONF}" <<EOF
[req]
default_bits = 2048
prompt = no
default_md = sha256
distinguished_name = dn
x509_extensions = v3_req

[dn]
CN = ${PUBLIC_IP}

[v3_req]
subjectAltName = @alt_names
keyUsage = digitalSignature, keyEncipherment
extendedKeyUsage = serverAuth

[alt_names]
IP.1 = ${PUBLIC_IP}
DNS.1 = ${PUBLIC_IP}
EOF

openssl req -x509 -nodes -days 825 -newkey rsa:2048 \
  -keyout "${CERT_DIR}/key.pem" \
  -out "${CERT_DIR}/cert.pem" \
  -config "${OPENSSL_CONF}"

rm -f "${OPENSSL_CONF}"
chmod 644 "${CERT_DIR}/cert.pem"
chmod 600 "${CERT_DIR}/key.pem"

echo "OK: ${CERT_DIR}/cert.pem"
echo "    ${CERT_DIR}/key.pem"
