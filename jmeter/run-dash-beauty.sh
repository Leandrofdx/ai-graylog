#!/usr/bin/env bash
# Roda o plano Dash Beauty (massa variada) contra o lab local.
set -euo pipefail
cd "$(dirname "$0")"

python3 gen_dash_beauty.py

HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8080}"
THREADS="${THREADS:-6}"
LOOPS="${LOOPS:-12}"
RAMP="${RAMP:-10}"
JTL="${JTL:-results/dash-beauty-$(date +%Y%m%d-%H%M%S).jtl}"

mkdir -p results

if ! command -v jmeter >/dev/null 2>&1; then
  echo "jmeter não encontrado no PATH."
  echo "Instale (brew install jmeter) ou rode com o caminho completo."
  exit 1
fi

echo "==> ping http://${HOST}:${PORT}/health"
curl -fsS "http://${HOST}:${PORT}/health" >/dev/null

echo "==> jmeter -n -t dash-beauty.jmx  threads=${THREADS} loops=${LOOPS} ramp=${RAMP}"
jmeter -n -t dash-beauty.jmx \
  -JHOST="${HOST}" \
  -JPORT="${PORT}" \
  -Jthreads="${THREADS}" \
  -Jloops="${LOOPS}" \
  -Jramp="${RAMP}" \
  -JbrowseThreads="${BROWSE_THREADS:-4}" \
  -JbrowseLoops="${BROWSE_LOOPS:-25}" \
  -JcpThreads="${CP_THREADS:-2}" \
  -JcpLoops="${CP_LOOPS:-10}" \
  -JnoiseLoops="${NOISE_LOOPS:-12}" \
  -l "${JTL}" \
  -e -o "results/dash-beauty-report"

echo "==> JTL: ${JTL}"
echo "==> Abra o Graylog (last 15m) e atualize os dashboards."
