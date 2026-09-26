#!/bin/sh
set -eu

AUTH="${GRAYLOG_USER}:${GRAYLOG_PASSWORD}"
BASE="${GRAYLOG_URL}/api"
HDR='X-Requested-By: graylog-init'
CT='Content-Type: application/json'

echo "==> Aguardando Graylog API..."
i=0
until curl -fsS -u "$AUTH" -H "$HDR" "$BASE/system/lbstatus" >/dev/null 2>&1; do
  i=$((i + 1))
  if [ "$i" -gt 60 ]; then
    echo "Graylog não ficou pronto a tempo." >&2
    exit 1
  fi
  sleep 2
done

create_input() {
  title="$1"
  type="$2"
  config="$3"

  exists="$(
    curl -fsS -u "$AUTH" -H "$HDR" "$BASE/system/inputs" \
      | grep -F "\"title\":\"${title}\"" || true
  )"
  if [ -n "$exists" ]; then
    echo "• Input já existe: ${title}"
    return 0
  fi

  echo "• Criando input: ${title}"
  curl -fsS -u "$AUTH" -H "$HDR" -H "$CT" \
    -d "{\"title\":\"${title}\",\"type\":\"${type}\",\"global\":true,\"configuration\":${config}}" \
    "$BASE/system/inputs" >/dev/null
}

create_input "GELF UDP" \
  "org.graylog2.inputs.gelf.udp.GELFUDPInput" \
  '{"bind_address":"0.0.0.0","port":12201,"recv_buffer_size":262144,"decompress_size_limit":8388608}'

create_input "GELF TCP" \
  "org.graylog2.inputs.gelf.tcp.GELFTCPInput" \
  '{"bind_address":"0.0.0.0","port":12201,"recv_buffer_size":1048576,"max_message_size":2097152,"decompress_size_limit":8388608,"use_null_delimiter":true}'

create_input "GELF HTTP" \
  "org.graylog2.inputs.gelf.http.GELFHttpInput" \
  '{"bind_address":"0.0.0.0","port":12202,"recv_buffer_size":1048576,"enable_cors":true,"max_chunk_size":65536,"idle_writer_timeout":60}'

echo "==> Inputs prontos."
