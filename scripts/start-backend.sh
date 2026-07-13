#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/Backend"
export XCODEAGENT_WORKING_DIR="${XCODEAGENT_WORKING_DIR:-.xcodeagent_dev}"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python" ]; then
  PYTHON_BIN="$VIRTUAL_ENV/bin/python"
else
  PYTHON_BIN=".venv/bin/python"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3"
fi

if [ "${1:-}" = "workflow" ]; then
  shift
  exec "$PYTHON_BIN" -m app.cli "$@"
fi

exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
