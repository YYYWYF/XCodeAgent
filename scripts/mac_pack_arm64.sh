#!/usr/bin/env bash
set -euo pipefail

# 在 Apple Silicon 主机上依次打包 arm64 后端和 Electron 安装包。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if ! command -v pnpm >/dev/null 2>&1; then
  echo "pnpm is required to build the frontend package. Install pnpm first." >&2
  exit 1
fi

bash "$SCRIPT_DIR/build-backend-mac.sh" arm64
cd "$REPO_ROOT/Frontend"
pnpm build:mac:arm64:dev

echo "macOS arm64 package created in $REPO_ROOT/Frontend/dist"
