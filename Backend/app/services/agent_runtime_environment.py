"""当前 Runtime 模板的进程配置合同，凭据仅在启动进程前解析。"""

import hashlib
import json
import secrets
from pathlib import Path

from app.config import Settings
from app.services.database_credentials import DatabaseCredentialError, load_application_json
from app.services.database_runtime_source import select_runtime_database, resolve_runtime_mysql_config
from app.services.managed_database_runtime import StartupSnapshot, resolve_managed_runtime
from app.services.python_runtime_checks import runtime_verification_environment


def runtime_database_environment(root: Path, snapshot: StartupSnapshot | None) -> dict[str, str]:
    """共用新版数据源选择和 SaaS 一次取密，只注入连接，不执行 DDL。"""

    source = select_runtime_database(root)
    if source is None:
        return {"AGENT_RUNTIME_DATABASE_ENABLED": "false"}
    if source.mode == "builtin":
        if snapshot is None:
            raise DatabaseCredentialError("内置数据库缺少本次表结构检查，已停止 Runtime 启动。")
        connection = resolve_managed_runtime(root, snapshot)
        host, port, user, password, database, ssl_mode = (
            connection.host, connection.port, connection.username,
            connection.password.get_secret_value(), connection.database, connection.sslMode,
        )
    else:
        connection = resolve_runtime_mysql_config(root, source)
        host, port, user, password, database, ssl_mode = (
            connection.host, connection.port, connection.user, connection.password, connection.database, "DISABLED",
        )
    return {
        "AGENT_RUNTIME_DATABASE_ENABLED": "true", "AGENT_RUNTIME_DATABASE_DRIVER": "pymysql",
        "AGENT_RUNTIME_DATABASE_HOST": host, "AGENT_RUNTIME_DATABASE_PORT": str(port),
        "AGENT_RUNTIME_DATABASE_USERNAME": user, "AGENT_RUNTIME_DATABASE_PASSWORD": password,
        "AGENT_RUNTIME_DATABASE_NAME": database, "AGENT_RUNTIME_DATABASE_SSL_MODE": ssl_mode,
        "AGENT_RUNTIME_DATABASE_ALLOW_DDL": "false",
    }


def build_runtime_environment(
    root: Path, *, port: int, launch_id: str, snapshot: StartupSnapshot | None,
    settings: Settings | None = None,
) -> dict[str, str]:
    """使用 Studio 当前主模型设置和真实匿名会话，拒绝宿主残留或工作区 dotenv 覆盖。"""

    application = load_application_json(root)
    if (application.get("auth") or {}).get("enable") is not False:
        raise ValueError("当前 Runtime 模板只支持明确关闭登录的应用。")
    if (application.get("authorization") or {}).get("enabled") is not False:
        raise ValueError("当前 Runtime 模板尚不支持授权，请先确认应用配置。")
    settings = settings or Settings.from_env()
    if not all((settings.model_base_url, settings.model_api_key, settings.model_api_name)):
        raise ValueError("Studio 当前模型配置不完整，无法注入 Runtime。")
    if settings.model_provider not in {"openai", "anthropic"}:
        raise ValueError("Runtime 模型 provider 必须为 openai 或 anthropic。")
    environment = runtime_verification_environment()
    environment.update({
        "AGENT_RUNTIME_HOST": "127.0.0.1", "AGENT_RUNTIME_PORT": str(port),
        "AGENT_RUNTIME_LAUNCH_ID": launch_id, "AGENT_RUNTIME_PROFILE": "preview",
        "AGENT_RUNTIME_AUTH_ENABLED": "false", "AGENT_RUNTIME_AUTH_MODE": "anonymous-session",
        "AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET": secrets.token_urlsafe(48),
        "AGENT_RUNTIME_ANONYMOUS_COOKIE_NAME": "das_" + hashlib.sha256(str(root).encode()).hexdigest()[:16],
        "AGENT_RUNTIME_NAMESPACE": "preview:" + hashlib.sha256(str(root).encode()).hexdigest()[:24],
        "AGENT_RUNTIME_ALLOWED_ORIGINS": "[]",
        "MODEL_PROVIDER": settings.model_provider, "MODEL_BASE_URL": settings.model_base_url,
        "MODEL_API_KEY": settings.model_api_key, "MODEL_NAME": settings.model_api_name,
        "MODEL_TIMEOUT_SECONDS": str(settings.model_timeout_seconds),
        "MODEL_MAX_RETRIES": str(settings.model_max_retries), "MODEL_TRUST_ENV": str(settings.model_trust_env).lower(),
        "MODEL_CUSTOM_HEADERS": json.dumps(settings.model_custom_headers),
        "ANTHROPIC_API_VERSION": settings.anthropic_api_version,
        "AGENT_TEMPERATURE": str(settings.default_temperature),
        "AGENT_MAX_TOKENS": str(settings.default_max_tokens),
    })
    environment.update(runtime_database_environment(root, snapshot))
    return environment
