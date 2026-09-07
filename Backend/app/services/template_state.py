"""读取并校验 Template Engine 唯一拥有的 Workspace TemplateState。"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from app.services.workspace_bootstrap.models import TemplateStateError

TEMPLATE_STATE_RELATIVE_PATH = Path(".xcodeagent/template-state.json")
_STATE_FIELDS = frozenset({"templateRevision", "managedFiles", "requested", "effective"})
_CAPABILITY_FIELDS = frozenset({"enabled"})
_TEMPLATE_CONTEXT_FIELDS = frozenset(
    {"state_path", "template_revision", "effective_capabilities"}
)


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
    """按 Engine OpenAPI 顶层和 Core 当前持久化语义校验 TemplateState。"""

    if not isinstance(value, dict) or set(value) != _STATE_FIELDS:
        raise TemplateStateError("TemplateState 字段必须严格等于冻结的 Engine Schema。")
    revision = value.get("templateRevision")
    if not isinstance(revision, str) or not revision.strip():
        raise TemplateStateError("TemplateState.templateRevision 必须为非空字符串。")
    managed_files = value.get("managedFiles")
    if not isinstance(managed_files, dict) or not all(
        isinstance(path, str)
        and bool(path)
        and isinstance(content, str)
        for path, content in managed_files.items()
    ):
        raise TemplateStateError(
            "TemplateState.managedFiles 必须是非空字符串键和字符串值的对象。"
        )
    _validate_capabilities(value.get("requested"), "TemplateState.requested")
    _validate_capabilities(value.get("effective"), "TemplateState.effective")
    return value


def template_revision(state: dict[str, Any]) -> str:
    """返回已经校验的 TemplateState revision。"""

    return str(validate_template_state(state)["templateRevision"])


def effective_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回按 ID 排序的有效能力副本，避免消费者改写 Engine State。"""

    effective = validate_template_state(state)["effective"]
    return {
        capability_id: deepcopy(effective[capability_id])
        for capability_id in sorted(effective)
    }


def requested_capabilities(state: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """返回按 ID 排序的请求能力副本，不替 Engine 执行依赖补全。"""

    requested = validate_template_state(state)["requested"]
    return {
        capability_id: deepcopy(requested[capability_id])
        for capability_id in sorted(requested)
    }


def has_capability(state: dict[str, Any], capability_id: str) -> bool:
    """判断一个指定能力是否已被 Engine 解析为有效能力。"""

    return capability_id in effective_capabilities(state)


def template_context(state: dict[str, Any]) -> dict[str, Any]:
    """生成 Build 可持久化的只读 TemplateState 绑定快照。"""

    validated = validate_template_state(state)
    return {
        "state_path": TEMPLATE_STATE_RELATIVE_PATH.as_posix(),
        "template_revision": str(validated["templateRevision"]),
        "effective_capabilities": effective_capabilities(validated),
    }


def validate_template_context(value: Any) -> dict[str, Any]:
    """校验 Build 保存的 TemplateState 绑定快照并返回规范化副本。"""

    if not isinstance(value, dict) or set(value) != _TEMPLATE_CONTEXT_FIELDS:
        raise TemplateStateError("template_context 字段不符合当前 Build 绑定契约。")
    if value.get("state_path") != TEMPLATE_STATE_RELATIVE_PATH.as_posix():
        raise TemplateStateError("template_context.state_path 不是唯一 TemplateState 路径。")
    revision = value.get("template_revision")
    if not isinstance(revision, str) or not revision.strip():
        raise TemplateStateError("template_context.template_revision 必须为非空字符串。")
    capabilities = _validate_capabilities(
        value.get("effective_capabilities"),
        "template_context.effective_capabilities",
    )
    return {
        "state_path": TEMPLATE_STATE_RELATIVE_PATH.as_posix(),
        "template_revision": revision,
        "effective_capabilities": {
            capability_id: deepcopy(capabilities[capability_id])
            for capability_id in sorted(capabilities)
        },
    }


def assert_template_context_matches(
    state: dict[str, Any],
    expected_context: Any,
) -> dict[str, Any]:
    """要求 Build 绑定的 revision 和 effective capabilities 与当前 State 一致。"""

    current = template_context(state)
    expected = validate_template_context(expected_context)
    if current["template_revision"] != expected["template_revision"]:
        raise TemplateStateError("TemplateState revision 与 Build 绑定快照不一致。")
    if current["effective_capabilities"] != expected["effective_capabilities"]:
        raise TemplateStateError("TemplateState effective capabilities 与 Build 绑定快照不一致。")
    return current


def _validate_capabilities(value: Any, label: str) -> dict[str, dict[str, Any]]:
    """校验 Core 当前持久化的 capability 映射，不推断或补写任何能力。"""

    if not isinstance(value, dict):
        raise TemplateStateError(f"{label} 必须是对象。")
    for capability_id, capability in value.items():
        if not isinstance(capability_id, str) or not capability_id.strip():
            raise TemplateStateError(f"{label} 包含空 Capability ID。")
        if not isinstance(capability, dict) or set(capability) != _CAPABILITY_FIELDS:
            raise TemplateStateError(
                f"{label}.{capability_id} 必须严格等于 Engine 持久化的 enabled 结构。"
            )
        if capability.get("enabled") is not True:
            raise TemplateStateError(f"{label}.{capability_id}.enabled 必须为 true。")
    return value
