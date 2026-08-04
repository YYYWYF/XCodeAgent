from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.services.database_crypto import (
    DatabaseCryptoError,
    decrypt_password,
    is_encrypted_password,
)


_DATABASE_SOURCE_TYPE = "database"


class DatabaseCredentialError(RuntimeError):
    """表示应用级数据库连接配置缺失、非法或无法解密。"""


@dataclass(frozen=True)
class MySQLConnectionConfig:
    """保存一次 MySQL 结构查询所需的强类型应用级配置。"""

    host: str
    port: int
    user: str
    password: str
    database: str


def load_application_json(workspace_root: str | Path) -> dict[str, Any]:
    """只读取目标工作区的 application.json，并校验顶层对象。"""

    root = Path(workspace_root).expanduser().resolve()
    application_file = root / ".xcodeagent" / "application.json"
    if not application_file.is_file():
        raise DatabaseCredentialError("当前应用工作区缺少 .xcodeagent/application.json。")
    try:
        payload = json.loads(application_file.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise DatabaseCredentialError("当前应用的 application.json 无法读取或格式无效。") from exc
    if not isinstance(payload, dict):
        raise DatabaseCredentialError("当前应用的 application.json 必须是 JSON 对象。")
    return payload


def read_plant_mode(application: dict[str, Any]) -> dict[str, Any]:
    """从应用配置中提取唯一支持直接连接的 datasource.db.plantMode。"""

    datasource = application.get("datasource")
    if not isinstance(datasource, dict):
        raise DatabaseCredentialError("当前应用未配置 datasource 数据源。")
    source_type = str(datasource.get("type") or "").strip().lower()
    if source_type != _DATABASE_SOURCE_TYPE:
        raise DatabaseCredentialError("当前应用的数据源类型必须是 database。")

    database = datasource.get("db")
    if not isinstance(database, dict):
        raise DatabaseCredentialError("当前应用未配置 datasource.db 数据库信息。")

    use_builtin = database.get("useBuiltin")
    if use_builtin is True:
        raise DatabaseCredentialError(
            "当前应用使用平台内置数据库，暂不支持通过应用配置直接建立 MySQL 连接。"
        )
    if use_builtin is not None and not isinstance(use_builtin, bool):
        raise DatabaseCredentialError("当前应用的 datasource.db.useBuiltin 必须是布尔值。")

    plant_mode = database.get("plantMode")
    dbid_mode = database.get("dbidMode")
    if plant_mode is not None and dbid_mode is not None:
        raise DatabaseCredentialError(
            "当前应用同时配置了 plantMode 和 dbidMode，无法确定数据库连接模式。"
        )
    if dbid_mode is not None:
        raise DatabaseCredentialError(
            "当前应用使用 DBID 数据库连接模式，暂不支持直接建立 MySQL 连接。"
        )
    if plant_mode is None:
        raise DatabaseCredentialError("当前应用未配置 datasource.db.plantMode。")
    if not isinstance(plant_mode, dict):
        raise DatabaseCredentialError("当前应用的 datasource.db.plantMode 必须是对象。")
    return plant_mode


def decrypt_application_password(
    value: Any,
    *,
    key_file: Path | None = None,
) -> str:
    """解密新版密码，并兼容只读旧应用中的非空明文密码。"""

    if not isinstance(value, str) or not value:
        raise DatabaseCredentialError("当前应用的数据库密码不能为空。")
    if not is_encrypted_password(value):
        return value
    try:
        return decrypt_password(value, key_file=key_file)
    except DatabaseCryptoError as exc:
        raise DatabaseCredentialError(str(exc)) from exc


def validate_mysql_config(
    plant_mode: dict[str, Any],
    *,
    key_file: Path | None = None,
) -> MySQLConnectionConfig:
    """校验 plantMode 字段并映射为 MySQLConnectionConfig。"""

    host = _required_text(plant_mode.get("domain"), "domain")
    user = _required_text(plant_mode.get("userName"), "userName")
    database = _required_text(plant_mode.get("schema"), "schema")
    password = decrypt_application_password(plant_mode.get("pwd"), key_file=key_file)
    raw_port = plant_mode.get("port")
    if isinstance(raw_port, bool):
        raise DatabaseCredentialError("当前应用的数据库端口必须是合法整数。")
    try:
        port = int(raw_port)
    except (TypeError, ValueError) as exc:
        raise DatabaseCredentialError("当前应用的数据库端口必须是合法整数。") from exc
    if not 1 <= port <= 65535:
        raise DatabaseCredentialError("当前应用的数据库端口必须在 1 到 65535 之间。")
    return MySQLConnectionConfig(
        host=host,
        port=port,
        user=user,
        password=password,
        database=database,
    )


def resolve_application_mysql_config(
    workspace_root: str | Path | None,
    *,
    key_file: Path | None = None,
) -> MySQLConnectionConfig:
    """从指定应用工作区解析本次 MySQL 查询的完整连接配置。"""

    if workspace_root is None or not str(workspace_root).strip():
        raise DatabaseCredentialError("缺少当前应用工作区，无法读取数据库连接配置。")
    return validate_mysql_config(
        read_plant_mode(load_application_json(workspace_root)),
        key_file=key_file,
    )


def _required_text(value: Any, field_name: str) -> str:
    """读取非空文本字段，同时避免把原值写入异常。"""

    if not isinstance(value, str) or not value.strip():
        raise DatabaseCredentialError(f"当前应用的数据库字段 {field_name} 不能为空。")
    return value.strip()
