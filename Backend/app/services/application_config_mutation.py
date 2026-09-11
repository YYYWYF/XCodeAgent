"""应用配置 Delta 的集中校验与原子提交服务。"""

from __future__ import annotations

import json
import os
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Sequence

from app.domain.application_config_change import (
    ApplicationConfigChange,
    NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG,
)


class ApplicationConfigMutationError(ValueError):
    """表示应用配置变更无法安全提交。"""


def apply_application_config_changes(
    workspace_root: str | Path,
    *,
    changes: Sequence[ApplicationConfigChange],
    initial_administrator_subjects: Sequence[str] | None = None,
) -> dict[str, Any]:
    """校验待确认 Delta 并原子更新唯一的 application.json。"""

    target = Path(workspace_root).expanduser().resolve() / ".xcodeagent" / "application.json"
    current = _load_current_application(target)
    normalized_changes = list(changes)
    _validate_changes(current, normalized_changes)
    if not normalized_changes and initial_administrator_subjects is None:
        return current
    updated = deepcopy(current)
    for change in normalized_changes:
        section, field = change.path.split(".")
        updated[section][field] = change.to_value
    if initial_administrator_subjects is not None:
        updated["authorization"]["initialAdministratorSubjects"] = _normalize_initial_administrator_subjects(
            initial_administrator_subjects
        )
    _validate_application_configuration(updated)
    _atomic_write_application(target, updated)
    return updated


def _load_current_application(target: Path) -> dict[str, Any]:
    """读取并校验 canonical application.json 的基本对象结构。"""

    if not target.is_file():
        raise ApplicationConfigMutationError("当前工作区缺少 .xcodeagent/application.json。")
    try:
        current = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ApplicationConfigMutationError("当前工作区 application.json 无法读取或格式无效。") from exc
    _validate_application_configuration(current)
    return current


def _validate_changes(current: dict[str, Any], changes: list[ApplicationConfigChange]) -> None:
    """验证白名单、重复路径和 from 快照，阻止过期或伪造提案覆盖当前值。"""

    paths: set[str] = set()
    for change in changes:
        if not isinstance(change, ApplicationConfigChange) or change.path not in NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG:
            raise ApplicationConfigMutationError("配置变更包含不允许的字段。")
        if change.path in paths:
            raise ApplicationConfigMutationError(f"配置变更重复设置 {change.path}。")
        paths.add(change.path)
        section, field = change.path.split(".")
        current_section = current.get(section)
        current_value = current_section.get(field) if isinstance(current_section, dict) else None
        if type(current_value) is not bool:
            raise ApplicationConfigMutationError(f"application.json 的 {change.path} 必须是布尔值。")
        if current_value is not change.from_value:
            raise ApplicationConfigMutationError(f"application.json 的 {change.path} 已变化，请重新生成正式修订。")


def _validate_application_configuration(application: Any) -> None:
    """验证当前 Schema、能力开关和授权依赖，确保提交后的完整配置自洽。"""

    if not isinstance(application, dict) or type(application.get("schemaVersion")) is not int or application.get("schemaVersion") != 5:
        raise ApplicationConfigMutationError("仅支持当前 schemaVersion 5 的 application.json。")
    values: dict[str, bool] = {}
    for path in ("auth.enable", "authorization.enabled"):
        section, field = path.split(".")
        value = application.get(section, {}).get(field) if isinstance(application.get(section), dict) else None
        if type(value) is not bool:
            raise ApplicationConfigMutationError(f"application.json 的 {path} 必须是布尔值。")
        values[path] = value
    if values["authorization.enabled"] and not values["auth.enable"]:
        raise ApplicationConfigMutationError("启用权限管理时必须启用登录认证。")
    if values["authorization.enabled"]:
        datasource = application.get("datasource")
        if not isinstance(datasource, dict) or datasource.get("type") != "database":
            raise ApplicationConfigMutationError("启用权限控制时必须使用数据库数据源。")
        authorization = application.get("authorization")
        subjects = authorization.get("initialAdministratorSubjects") if isinstance(authorization, dict) else None
        if not isinstance(subjects, list) or not any(isinstance(item, str) and item.strip() and item != "current-user" for item in subjects):
            raise ApplicationConfigMutationError("启用权限控制时至少需要一个真实初始管理员 subjectId。")
    authorization = application.get("authorization")
    if not isinstance(authorization, dict) or set(authorization) != {"enabled", "initialAdministratorSubjects"}:
        raise ApplicationConfigMutationError("application.json authorization 必须只包含 enabled 和 initialAdministratorSubjects。")


def _normalize_initial_administrator_subjects(values: Sequence[str]) -> list[str]:
    """去重并校验权限初始化所需的真实管理员 subjectId。"""

    subjects: list[str] = []
    for raw_value in values:
        subject = str(raw_value).strip()
        if not subject or subject in subjects:
            continue
        if subject == "current-user":
            raise ApplicationConfigMutationError("初始管理员必须使用真实 subjectId，不能使用 current-user。")
        subjects.append(subject)
    if not subjects:
        raise ApplicationConfigMutationError("启用权限控制时至少需要一个真实初始管理员 subjectId。")
    return subjects


def _atomic_write_application(target: Path, application: dict[str, Any]) -> None:
    """以同目录临时文件、fsync 与 replace 原子替换完整应用配置。"""

    temporary_path: Path | None = None
    try:
        descriptor, temporary_name = tempfile.mkstemp(prefix=".application.json.", suffix=".tmp", dir=target.parent, text=True)
        temporary_path = Path(temporary_name)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(application, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_path, target)
        temporary_path = None
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
