from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any


class ApplicationAuthorizationConfigError(ValueError):
    """表示应用权限配置无法安全更新。"""


def authorization_configuration_can_enable(workspace_root: str | Path) -> bool:
    """读取当前应用数据源，判断是否满足启用内置权限的数据库前提。"""

    target = Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    datasource = current.get("datasource") if isinstance(current, dict) else None
    return isinstance(datasource, dict) and datasource.get("type") == "database"


def persist_authorization_configuration(
    workspace_root: str | Path,
    *,
    initial_administrator_subjects: list[str],
) -> dict[str, Any]:
    """在同一目录内原子启用认证与权限配置，并返回已写入的当前对象。"""

    target = Path(workspace_root).expanduser() / ".xcodeagent" / "application.json"
    if not target.is_file():
        raise ApplicationAuthorizationConfigError("当前工作区缺少 .xcodeagent/application.json。")
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ApplicationAuthorizationConfigError("当前工作区 application.json 无法读取或格式无效。") from exc
    if not isinstance(current, dict) or current.get("schemaVersion") != 5:
        raise ApplicationAuthorizationConfigError("仅支持当前 schemaVersion 5 的 application.json。")
    if not authorization_configuration_can_enable(workspace_root):
        raise ApplicationAuthorizationConfigError("启用权限控制时必须使用数据库数据源。")
    auth = current.get("auth")
    authorization = current.get("authorization")
    if not isinstance(auth, dict) or not isinstance(authorization, dict):
        raise ApplicationAuthorizationConfigError("application.json 缺少有效的认证或权限配置。")

    subjects: list[str] = []
    seen: set[str] = set()
    for raw_subject in initial_administrator_subjects:
        subject = str(raw_subject).strip()
        if not subject or subject in seen:
            continue
        if subject == "current-user":
            raise ApplicationAuthorizationConfigError("初始管理员必须使用真实 subjectId，不能使用 current-user。")
        seen.add(subject)
        subjects.append(subject)
    if not subjects:
        raise ApplicationAuthorizationConfigError("启用权限控制时至少需要一个初始管理员 subjectId。")

    updated = deepcopy(current)
    updated_auth = updated["auth"]
    updated_authorization = updated["authorization"]
    updated_auth["enable"] = True
    updated_authorization["enabled"] = True
    updated_authorization["initialAdministratorSubjects"] = subjects

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=".application.json.", suffix=".tmp", dir=target.parent, text=True
        )
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(updated, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
    return updated
