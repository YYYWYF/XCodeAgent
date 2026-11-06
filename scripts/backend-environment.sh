#!/usr/bin/env bash

# 解析统一环境参数；打包不允许由终端残留 APP_ENV 隐式选择环境。
parse_backend_environment_arguments() {
  local require_env="$1" allow_slim="$2"
  shift 2
  SELECTED_ENV=""
  GRAMMAR_PROFILE="full"
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --env)
        if [ -n "$SELECTED_ENV" ] || [ "$#" -lt 2 ] || [ -z "$2" ]; then
          echo "Provide --env dev|st|uat|prd exactly once." >&2
          return 1
        fi
        SELECTED_ENV="$2"
        shift 2
        ;;
      --slim)
        if [ "$allow_slim" != "true" ]; then
          echo "--slim is only supported by the one-command package entry." >&2
          return 1
        fi
        GRAMMAR_PROFILE="builtin"
        shift
        ;;
      *) echo "Unknown argument: $1. Use --env dev|st|uat|prd." >&2; return 1 ;;
    esac
  done
  if [ -z "$SELECTED_ENV" ]; then
    if [ "$require_env" = "true" ]; then
      echo "Packaging requires --env dev|st|uat|prd." >&2
      return 1
    fi
    SELECTED_ENV="${APP_ENV-dev}"
  fi
  case "$SELECTED_ENV" in
    dev|st|uat|prd) ;;
    *) echo "APP_ENV must be dev, st, uat or prd." >&2; return 1 ;;
  esac
}

# 本地允许显式文件路径；打包始终从 Backend 选择目标环境的标准文件。
configure_backend_environment() {
  local backend_root="$1" allow_explicit_file="$2"
  ENV_FILENAME=".env"
  if [ "$SELECTED_ENV" != "dev" ]; then ENV_FILENAME=".env.$SELECTED_ENV"; fi
  ENV_FILE="$backend_root/$ENV_FILENAME"
  if [ "$allow_explicit_file" = "true" ] && [ -n "${DEVAGENTSTUDIO_BACKEND_ENV_FILE:-}" ]; then
    ENV_FILE="$DEVAGENTSTUDIO_BACKEND_ENV_FILE"
  fi
  if [ ! -f "$ENV_FILE" ]; then
    echo "Missing backend configuration for $SELECTED_ENV: $ENV_FILE" >&2
    return 1
  fi
  ENV_FILE="$(cd "$(dirname "$ENV_FILE")" && pwd)/$(basename "$ENV_FILE")"
  export APP_ENV="$SELECTED_ENV"
  export DEVAGENTSTUDIO_BACKEND_ENV_FILE="$ENV_FILE"
}
