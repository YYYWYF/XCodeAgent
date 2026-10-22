#!/usr/bin/env bash
set -euo pipefail

# 打包完整仓库，保留 .git、隐藏文件和符号链接，仅排除各层依赖目录。
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
REPO_NAME="$(basename "$REPO_ROOT")"
REPO_PARENT="$(dirname "$REPO_ROOT")"

if [ "$#" -gt 1 ]; then
  echo "用法：bash scripts/pack-source-zip.sh [仓库外的输出路径.zip]" >&2
  exit 1
fi
if ! command -v zip >/dev/null 2>&1; then
  echo "缺少 zip 命令，请先安装。" >&2
  exit 1
fi

# 默认输出到仓库同级目录，避免压缩包自身被递归打包。
OUTPUT="${1:-$REPO_PARENT/$REPO_NAME-$(date +%Y%m%d-%H%M%S).zip}"
OUTPUT_DIR="$(cd "$(dirname "$OUTPUT")" && pwd -P)"
OUTPUT="$OUTPUT_DIR/$(basename "$OUTPUT")"
case "$OUTPUT" in
  "$REPO_ROOT"/*)
    echo "请将输出路径放在仓库之外，避免递归打包。" >&2
    exit 1
    ;;
esac
if [[ "$OUTPUT" != *.zip ]]; then
  echo "输出文件必须以 .zip 结尾。" >&2
  exit 1
fi
if [ -e "$OUTPUT" ] || [ -L "$OUTPUT" ]; then
  echo "输出文件已存在，请换一个路径：$OUTPUT" >&2
  exit 1
fi

# 不读取 Git 忽略规则；-y 保存链接本身，排除模式覆盖任意深度的目标目录。
cd "$REPO_PARENT"
ZIPOPT= zip -q -r -y "$OUTPUT" "$REPO_NAME" \
  -x '*/node_modules/*' '*/.venv/*'
echo "打包完成：$OUTPUT"
