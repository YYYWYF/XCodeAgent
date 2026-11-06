#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# 共用参数校验并在启动 reload 监控器前检查配置。
source "$ROOT_DIR/scripts/backend-environment.sh"
parse_backend_environment_arguments false false "$@"
configure_backend_environment "$ROOT_DIR/Backend" true
cd "$ROOT_DIR/Backend"

if [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3.12" ]; then
  PYTHON_BIN="$VIRTUAL_ENV/bin/python3.12"
elif [ -n "${VIRTUAL_ENV:-}" ] && [ -x "$VIRTUAL_ENV/bin/python3" ]; then
  PYTHON_BIN="$VIRTUAL_ENV/bin/python3"
elif [ -x ".venv/bin/python3.12" ]; then
  PYTHON_BIN=".venv/bin/python3.12"
elif [ -x ".venv/bin/python3" ]; then
  PYTHON_BIN=".venv/bin/python3"
elif command -v python3.12 >/dev/null 2>&1; then
  PYTHON_BIN="python3.12"
else
  PYTHON_BIN="python3"
fi

"$PYTHON_BIN" -m app.environment
exec "$PYTHON_BIN" -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
