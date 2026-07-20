#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR/Backend"
export XCODEAGENT_WORKING_DIR="${XCODEAGENT_WORKING_DIR:-.xcodeagent_dev}"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3.12" ]; then
  PYTHON_BIN="$VIRTUAL_ENV/bin/python3.12"
else
  PYTHON_BIN=".venv/bin/python3.12"
fi

if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="python3.12"
fi

exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
