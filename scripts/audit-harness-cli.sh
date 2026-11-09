#!/usr/bin/env bash
set -euo pipefail

if [[ -x ".venv/bin/python" ]]; then
  exec .venv/bin/python -m app.cli.main "$@"
fi

exec python -m app.cli.main "$@"
