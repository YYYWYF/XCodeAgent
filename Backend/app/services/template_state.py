"""读取并校验 Template Engine 唯一拥有的 Workspace TemplateState。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.workspace_bootstrap.models import TemplateStateError

TEMPLATE_STATE_RELATIVE_PATH = Path(".xcodeagent/template-state.json")
_STATE_FIELDS = frozenset({"templateRevision", "managedFiles", "requested", "effective"})


def template_state_path(workspace: str | Path) -> Path:
    """返回当前工作区唯一 TemplateState 的规范路径。"""

    return Path(workspace).expanduser().resolve() / TEMPLATE_STATE_RELATIVE_PATH


def load_template_state(workspace: str | Path) -> dict[str, Any]:
    """读取工作区 State，并拒绝缺失、损坏或未冻结的结构。"""

    path = template_state_path(workspace)
    if not path.is_file() or path.is_symlink():
        raise TemplateStateError("工作区缺少有效的 .xcodeagent/template-state.json。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise TemplateStateError("TemplateState 无法读取或 JSON 损坏。") from exc
    return validate_template_state(value)


def validate_template_state(value: Any) -> dict[str, Any]:
    """按当前 Engine OpenAPI 四字段 Schema 校验 TemplateState。"""

    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        raise TemplateStateError("TemplateState 字段必须严格等于冻结的 Engine Schema。")
    revision = value.get("templateRevision")
    if not isinstance(revision, str) or not revision.strip():
        raise TemplateStateError("TemplateState.templateRevision 必须为非空字符串。")
    managed_files = value.get("managedFiles")
    if not isinstance(managed_files, dict) or not all(
        isinstance(path, str) and isinstance(content, str)
        for path, content in managed_files.items()
    ):
        raise TemplateStateError("TemplateState.managedFiles 必须是字符串键和值的对象。")
    for field in ("requested", "effective"):
        if not isinstance(value.get(field), dict):
            raise TemplateStateError(f"TemplateState.{field} 必须是对象。")
    return value


def template_revision(state: dict[str, Any]) -> str:
    """返回已经校验的 TemplateState revision。"""

    return str(validate_template_state(state)["templateRevision"])


def effective_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """读取 Engine 输出的有效能力集合，并校验 capability enabled 语义。"""

    effective = validate_template_state(state)["effective"]
    result: dict[str, dict[str, Any]] = {}
    for capability_id, value in effective.items():
        if (
            not isinstance(capability_id, str)
            or not capability_id
            or not isinstance(value, dict)
            or value.get("enabled") is not True
        ):
            raise TemplateStateError("TemplateState.effective 包含无效 Capability。")
        result[capability_id] = value
    return result


def has_capability(state: dict[str, Any], capability_id: str) -> bool:
    """判断一个指定能力是否已被 Engine 解析为有效能力。"""

    return capability_id in effective_capabilities(state)
