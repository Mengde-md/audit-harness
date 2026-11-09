#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${HOST:-127.0.0.1}"
PORT="${PORT:-8000}"
PYTHON="${ROOT_DIR}/.venv/bin/python"

stop_pid() {
  local pid="$1"

  if ! kill -0 "${pid}" 2>/dev/null; then
    return
  fi

  echo "Stopping audit-harness (PID ${pid})..."
  kill "${pid}"

  for _ in {1..20}; do
    if ! kill -0 "${pid}" 2>/dev/null; then
      return
    fi
    sleep 0.25
  done

  echo "Process did not stop gracefully; forcing shutdown."
  kill -9 "${pid}"
}

port_pid="$(lsof -tiTCP:"${PORT}" -sTCP:LISTEN 2>/dev/null || true)"
if [[ -n "${port_pid}" ]]; then
  echo "Port ${PORT} is occupied by PID ${port_pid}; stopping it."
  stop_pid "${port_pid}"
fi

if [[ ! -x "${PYTHON}" ]]; then
  echo "Error: virtual environment not found at ${PYTHON}" >&2
  exit 1
fi

cd "${ROOT_DIR}"
echo "Starting Audit Harness in the foreground."
echo "URL: http://${HOST}:${PORT}/docs"
echo "Press Ctrl+C to stop."

exec "${PYTHON}" -m uvicorn app.main:app \
  --host "${HOST}" \
  --port "${PORT}"
