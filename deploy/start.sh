#!/usr/bin/bash
set -euo pipefail
release_dir=$(cd -- "$(dirname -- "$0")" && pwd -P)
export PYTHONPATH="$release_dir/vendor:$release_dir"
export PYTHONDONTWRITEBYTECODE=1
export PYTHONUNBUFFERED=1
exec /usr/bin/python3 -m uvicorn app.main:app --host 127.0.0.1 \
    --port "${SERVER_PORT:-8080}" --workers 1 --loop asyncio --http h11 \
    --proxy-headers --forwarded-allow-ips=127.0.0.1 --no-access-log
