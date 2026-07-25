#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKEND_ROOT="${BACKEND_ROOT:-"$ROOT_DIR/Backend"}"
FRONTEND_ROOT="${FRONTEND_ROOT:-"$ROOT_DIR/Frontend"}"
REQUESTED_ARCH="${1:-}"
MACHINE_ARCH="$(uname -m)"
case "$MACHINE_ARCH" in
  x86_64) BUILD_ARCH="x64" ;;
  arm64) BUILD_ARCH="arm64" ;;
  *)
    echo "Unsupported macOS architecture: $MACHINE_ARCH" >&2
    exit 1
    ;;
esac
if [ -n "$REQUESTED_ARCH" ] && [ "$REQUESTED_ARCH" != "$BUILD_ARCH" ]; then
  echo "Requested macOS architecture $REQUESTED_ARCH does not match build host $BUILD_ARCH." >&2
  exit 1
fi
if [ -n "${PYTHON:-}" ]; then
  PYTHON_BIN="$PYTHON"
elif [ -x "$BACKEND_ROOT/.venv/bin/python3.12" ]; then
  PYTHON_BIN="$BACKEND_ROOT/.venv/bin/python3.12"
else
  PYTHON_BIN="python3.12"
fi

PYTHON_VERSION="$("$PYTHON_BIN" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
if [ "$PYTHON_VERSION" != "3.12" ]; then
  echo "Python 3.12 is required to build the macOS backend. Current Python version: $PYTHON_VERSION" >&2
  exit 1
fi

ENV_FILE="$BACKEND_ROOT/.env"
SPEC_FILE="$BACKEND_ROOT/packaging/xcodeagent-backend.spec"
DIST_DIR="$BACKEND_ROOT/dist/xcodeagent-backend"
TARGET_DIR="$FRONTEND_ROOT/resources/backend/darwin-$BUILD_ARCH"
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

"$PYTHON_BIN" "$BACKEND_ROOT/packaging/verify_bundled_skills.py" "$DIST_DIR"

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
