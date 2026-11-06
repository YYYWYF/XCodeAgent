#!/usr/bin/env bash
set -euo pipefail

# 在 Apple Silicon 主机上依次打包 arm64 后端和 Electron 安装包。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is required to build the frontend package. Install pnpm first." >&2
  exit 1
fi

source "$SCRIPT_DIR/backend-environment.sh"
parse_backend_environment_arguments true true "$@"
configure_backend_environment "$REPO_ROOT/Backend" false

DEVAGENTSTUDIO_BACKEND_GRAMMARS="$GRAMMAR_PROFILE" bash "$SCRIPT_DIR/build-backend-mac.sh" arm64 --env "$SELECTED_ENV"
cd "$REPO_ROOT/Frontend"
pnpm "build:mac:arm64:$SELECTED_ENV"

echo "macOS arm64 package created in $REPO_ROOT/Frontend/dist"
