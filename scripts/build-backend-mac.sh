#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ROOT="${BACKEND_ROOT:-"$ROOT_DIR/Backend"}"
FRONTEND_ROOT="${FRONTEND_ROOT:-"$ROOT_DIR/Frontend"}"
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
elif [ -x "$BACKEND_ROOT/.venv/bin/python" ]; then
  PYTHON_BIN="$BACKEND_ROOT/.venv/bin/python"
else
  PYTHON_BIN="python3"
fi

ENV_FILE="$BACKEND_ROOT/.env"
SPEC_FILE="$BACKEND_ROOT/packaging/xcodeagent-backend.spec"
DIST_DIR="$BACKEND_ROOT/dist/xcodeagent-backend"
TARGET_DIR="$FRONTEND_ROOT/resources/backend/darwin"
TARGET_EXECUTABLE="$TARGET_DIR/xcodeagent-backend"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing Backend/.env. Create it before building the packaged backend." >&2
  exit 1
fi

cd "$BACKEND_ROOT"
"$PYTHON_BIN" -m pip install -r requirements-build.txt
"$PYTHON_BIN" -m PyInstaller --noconfirm --clean "$SPEC_FILE"

if [ ! -d "$DIST_DIR" ]; then
  echo "PyInstaller output not found: $DIST_DIR" >&2
  exit 1
fi

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"
cp -R "$DIST_DIR"/. "$TARGET_DIR"/
cp "$ENV_FILE" "$TARGET_DIR/.env"

if [ ! -f "$TARGET_EXECUTABLE" ]; then
  echo "Staged backend executable not found: $TARGET_EXECUTABLE" >&2
  exit 1
fi

chmod +x "$TARGET_EXECUTABLE"
echo "Backend staged for macOS Electron at $TARGET_DIR"
