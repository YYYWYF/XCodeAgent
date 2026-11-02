"""权限初始化的数据源决策与只读 DDL 合同，不解析运行时凭据。"""

import hashlib
import json
import stat
from pathlib import Path
from typing import Any

from app.persistence.data_sources import DataSourceStorageError, read_sources
from app.services.database_credentials import DatabaseCredentialError, load_application_json
from app.services.database_runtime_source import select_runtime_database

AUTHORIZATION_SCHEMA_RELATIVE_PATH = Path("backend/sql/authorization/schema.sql")
DDL_MAX_BYTES = 256 * 1024


def resolve_bootstrap_mode(root: Path) -> tuple[str, str]:
    """以权威配置及有效目录决定副作用权限，仅哈希白名单目标库身份。"""

    try:
        application = load_application_json(root)
        datasource = application.get("datasource")
        kind = datasource.get("type") if isinstance(datasource, dict) else None
        if kind in {"static", "external_api"}:
            return kind, ""
        if kind != "database":
            return "unknown", ""
        selected = select_runtime_database(root)
        if selected is None:
            return "unknown", ""
        if selected.mode == "dbid":
            return "dbid", ""
        identity: dict[str, Any] = {"mode": selected.mode, "sourceId": selected.source_id}
        if selected.mode == "builtin":
            binding = application.get("managedDatabase") or {}
            if (not isinstance(binding, dict) or binding.get("status") != "REGISTERED"
                    or not binding.get("datasourceKey") or not binding.get("datasourceId")):
                return "builtin", ""
            identity.update({key: binding[key] for key in ("datasourceKey", "datasourceId")})
        else:
            source = next(item for item in read_sources(root, source_id=selected.source_id)
                          if item["id"] == selected.source_id)
            if source.get("mode") != selected.mode:
                return "unknown", ""
            if not all(source.get(key) for key in ("domain", "port", "schema")):
                return "direct", ""
            identity.update({key: source[key] for key in ("domain", "port", "schema")})
        encoded = json.dumps(identity, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return selected.mode, "sha256:" + hashlib.sha256(encoded).hexdigest()
    except (DatabaseCredentialError, DataSourceStorageError, OSError, ValueError):
        return "unknown", ""


def read_authorization_ddl(root: Path) -> dict[str, Any]:
    """有界读取唯一工程 DDL，拒绝越界链接、非常规文件及无效文本。"""

    base = {"ddl_available": False, "ddl_path": AUTHORIZATION_SCHEMA_RELATIVE_PATH.as_posix()}
    try:
        path = (root / AUTHORIZATION_SCHEMA_RELATIVE_PATH).resolve(strict=True)
        if not path.is_relative_to(root) or not stat.S_ISREG(path.stat().st_mode):
            raise ValueError()
        with path.open("rb") as stream:
            raw = stream.read(DDL_MAX_BYTES + 1)
        if len(raw) > DDL_MAX_BYTES:
            return {**base, "ddl_error": "权限 DDL 超过 256 KiB，无法安全展示。"}
        ddl = raw.decode("utf-8")
        if not ddl.strip() or "\x00" in ddl:
            raise ValueError()
        return {**base, "ddl_available": True, "ddl": ddl}
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return {**base, "ddl_error": "权限 DDL 缺失、不可读或不是工作区内有效 UTF-8 普通文件：backend/sql/authorization/schema.sql。"}


def manual_result(root: Path, fingerprint: str, mode: str, status: str,
                  category: str, message: str, *, execution_attempted: bool = False) -> dict[str, Any]:
    """保留真实初始化子状态，附安全 DDL 和人工处理提示，不包含脚本日志。"""

    ddl = read_authorization_ddl(root)
    return {"status": status, "manifest_fingerprint": fingerprint, "datasource_mode": mode,
            "execution_attempted": execution_attempted, "requires_manual_action": True,
            "failure_category": category,
            "message": message + (str(ddl.get("ddl_error", "")) if ddl.get("ddl_error") != message else ""),
            **ddl}
