"""Agent Settings 修订使用的文档定位、安全投影与定向失效辅助。"""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
from typing import Any

from app.services.application_lifecycle import (
    ApplicationLifecycleConflictError,
    load_application_lifecycle,
)
from app.services.artifact_invalidation import canonical_sha256, mark_artifact_document_stale
from app.workspace.plan_documents import load_project_plan_json


AGENT_SETTINGS_ARTIFACT_KEY = "technical-plan"


def preview_payload(
    *,
    metadata: Any,
    markdown: str,
    lifecycle_revision: int,
    hidden: dict[str, Any],
    settings: dict[str, Any],
) -> dict[str, Any]:
    """构造不暴露完整 TechnicalPlan 的 Agent Settings 修改预览。"""

    return {
        "action": "prepare_agent_settings_revision",
        "active": True,
        "changeId": metadata.change_id,
        "agentId": str(hidden.get("agentId") or metadata.target_id),
        "basedOnContractHash": hidden.get("basedOnContractHash"),
        "candidateContractHash": hidden.get("candidateContractHash"),
        "changedSections": deepcopy(hidden.get("changedSections") or []),
        "fieldDiffs": deepcopy(hidden.get("fieldDiffs") or []),
        "draftSha256": hashlib.sha256(markdown.encode("utf-8")).hexdigest(),
        "basedOnLifecycleRevision": lifecycle_revision,
        "agentSettings": deepcopy(settings),
        "impact": {
            "agentBuild": True,
            "toolAdapterBuild": False,
            "integrationTest": True,
            "launchEvidence": True,
            "unrelatedAgents": False,
        },
    }


def settings_field_diffs(
    old_contract: dict[str, Any],
    new_contract: dict[str, Any],
) -> list[dict[str, str]]:
    """只投影允许用户编辑字段的前后值，不暴露完整内部 Contract。"""

    old_settings = agent_settings(old_contract)
    new_settings = agent_settings(new_contract)
    fields = [
        ("prompt.persona.role", "角色", nested(old_settings, "prompt", "persona", "role"), nested(new_settings, "prompt", "persona", "role")),
        ("prompt.persona.tone", "表达风格", nested(old_settings, "prompt", "persona", "tone"), nested(new_settings, "prompt", "persona", "tone")),
        ("prompt.systemPrompt", "System Prompt", nested(old_settings, "prompt", "systemPrompt"), nested(new_settings, "prompt", "systemPrompt")),
        ("prompt.constraints", "业务约束", nested(old_settings, "prompt", "constraints"), nested(new_settings, "prompt", "constraints")),
        ("model.generation.temperature", "Temperature", nested(old_settings, "model", "generation", "temperature"), nested(new_settings, "model", "generation", "temperature")),
    ]
    return [
        {
            "field": field,
            "label": label,
            "before": display_value(before),
            "after": display_value(after),
        }
        for field, label, before, after in fields
        if before != after
    ]


def artifact_paths(workspace_root: str) -> tuple[Path, Path, Path]:
    """解析当前工作区正式 ProductPlan 与 TechnicalPlan 路径。"""

    root = Path(workspace_root).expanduser().resolve()
    if not root.is_dir():
        raise ValueError("Agent Settings 工作区不存在。")
    plans = root / ".xcodeagent" / "plans"
    return root, plans / "product-plan.json", plans / "technical-plan.json"


def load_confirmed_plan(path: Path, *, label: str) -> dict[str, Any]:
    """读取一份当前已确认正式规划产物。"""

    try:
        value = load_project_plan_json(path)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"{label} 无法读取。") from exc
    if not isinstance(value, dict) or value.get("confirmation_status") != "confirmed":
        raise ValueError(f"{label} 尚未确认或已失效。")
    return value


def agent_contract(plan: dict[str, Any], agent_id: str) -> dict[str, Any]:
    """按 agentId 唯一定位完整 Agent Contract。"""

    contracts = [
        item
        for item in plan.get("agent_contracts", [])
        if isinstance(item, dict) and str(item.get("agentId") or "").strip() == agent_id
    ]
    if len(contracts) != 1:
        raise ValueError("TechnicalPlan 无法唯一定位当前智能体契约。")
    return contracts[0]


def agent_settings(contract: dict[str, Any]) -> dict[str, Any]:
    """复制完整 Contract 中的七段 Agent Settings。"""

    value = contract.get("agentSettings")
    return deepcopy(value) if isinstance(value, dict) else {}


def document_sha256(path: Path) -> str:
    """生成带算法前缀的正式文件哈希。"""

    return f"sha256:{canonical_sha256(path)}"


def required_lifecycle(root: Path):
    """读取必需的应用生命周期状态。"""

    lifecycle = load_application_lifecycle(root)
    if lifecycle is None:
        raise ApplicationLifecycleConflictError("application lifecycle 尚未初始化。")
    return lifecycle


def lifecycle_revision(root: Path) -> int:
    """读取刚完成状态转换后的 lifecycle revision。"""

    return required_lifecycle(root).revision


def invalidate_old_agent_build_plan(
    root: Path,
    *,
    agent_id: str,
    old_contract_hash: str,
) -> list[str]:
    """仅在当前 BuildTaskPlan 确实绑定旧 Agent Contract 时标记其 stale。"""

    path = root / ".xcodeagent" / "plans" / "build-task-plan.json"
    if not path.is_file():
        return []
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return []
    if not isinstance(value, dict):
        return []
    context = value.get("build_context") if isinstance(value.get("build_context"), dict) else {}
    target = context.get("target") if isinstance(context.get("target"), dict) else {}
    if (
        target.get("type") != "agent"
        or str(target.get("id") or "") != agent_id
        or str(context.get("contract_hash") or "") != old_contract_hash
    ):
        return []
    mark_artifact_document_stale(path)
    return ["build-task-plan"]


def nested(value: Any, *keys: str) -> Any:
    """从嵌套字典读取字段，缺失时返回 None。"""

    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def display_value(value: Any) -> str:
    """把允许公开的编辑字段转换为修改预览文本。"""

    if isinstance(value, list):
        return "\n".join(str(item) for item in value)
    if value is None:
        return "未设置"
    return str(value)
