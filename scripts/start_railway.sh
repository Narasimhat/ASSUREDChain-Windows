#!/usr/bin/env bash
# Start ASSUREDChain on Railway: FastAPI assistant + Streamlit UI.
set -euo pipefail

cd "$(dirname "$0")/.."

PORT="${PORT:-8503}"
ASSISTANT_PORT="${ASSISTANT_PORT:-8000}"
export ASSISTANT_API_URL="${ASSISTANT_API_URL:-http://127.0.0.1:${ASSISTANT_PORT}}"

mkdir -p data

echo "Starting assistant API on :${ASSISTANT_PORT}..."
python -m uvicorn assistant.backend.main:app \
  --host 127.0.0.1 \
  --port "${ASSISTANT_PORT}" &
ASSISTANT_PID=$!

cleanup() {
  kill "${ASSISTANT_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Wait until the assistant answers (or give up after ~20s).
for _ in $(seq 1 40); do
  if curl -fsS "http://127.0.0.1:${ASSISTANT_PORT}/docs" >/dev/null 2>&1; then
    break
  fi
  sleep 0.5
done

echo "Starting Streamlit on :${PORT}..."
exec streamlit run app/Home.py \
  --server.address 0.0.0.0 \
  --server.port "${PORT}" \
  --server.headless true \
  --browser.gatherUsageStats false
