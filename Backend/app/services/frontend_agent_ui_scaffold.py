"""按已确认 ProductPlan 条件式注入生成应用 Agent UI 固定资产。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.frontend_agent_ui_assets import (
    AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS,
    AGENT_UI_FRONTEND_TARGET_PATHS,
    AGENT_UI_FRONTEND_TEMPLATE_VERSION,
    FrontendAgentUiAssetError,
    assert_safe_agent_ui_target,
    calculate_sha256,
    load_agent_ui_frontend_source_assets,
    write_missing_agent_ui_file_atomically,
)
from app.services.product_plan import PRODUCT_PLAN_SCHEMA_VERSION
_AGENT_UI_FRONTEND_COMPONENT_DIRECTORY = Path(
    "frontend/src/components/AgentConversation"
)
_PRODUCT_PLAN_RELATIVE_PATH = Path(".xcodeagent/plans/product-plan.json")
_SUPPORTED_SURFACES = {"standalone_page", "floating_panel"}


class FrontendAgentUiScaffoldError(ValueError):
    """表示固定 Agent UI 资产无法安全注入或验证。"""


def _read_json_object(path: Path, label: str) -> dict[str, Any]:
    """读取固定路径 JSON 对象并把损坏输入转换为领域错误。"""

    if not path.is_file() or path.is_symlink():
        raise FrontendAgentUiScaffoldError(f"{label} 不存在或不是普通文件。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise FrontendAgentUiScaffoldError(f"{label} 损坏或无法读取。") from exc
    if not isinstance(value, dict):
        raise FrontendAgentUiScaffoldError(f"{label} 必须是 JSON 对象。")
    return value


def _confirmed_product_plan(workspace: Path) -> dict[str, Any]:
    """读取当前版本且已确认的正式 ProductPlan。"""

    plan = _read_json_object(workspace / _PRODUCT_PLAN_RELATIVE_PATH, "正式 ProductPlan")
    if plan.get("schema_version") != PRODUCT_PLAN_SCHEMA_VERSION:
        raise FrontendAgentUiScaffoldError(
            f"正式 ProductPlan 不是 {PRODUCT_PLAN_SCHEMA_VERSION}。"
        )
    if plan.get("confirmation_status") != "confirmed":
        raise FrontendAgentUiScaffoldError("正式 ProductPlan 尚未确认。")
    return plan


def _agent_ui_required(product_plan: dict[str, Any]) -> bool:
    """只按 Agent 绑定中的显式 Surface 类型和 enabled 派生注入条件。"""

    raw_agents = product_plan.get("agents")
    if raw_agents is None:
        raw_agents = []
    if not isinstance(raw_agents, list) or any(
        not isinstance(agent, dict) for agent in raw_agents
    ):
        raise FrontendAgentUiScaffoldError("正式 ProductPlan.agents 必须是对象数组。")
    required = False
    for agent_index, agent in enumerate(raw_agents):
        raw_bindings = agent.get("pageActionBindings")
        if raw_bindings is None:
            raw_bindings = []
        if not isinstance(raw_bindings, list) or any(
            not isinstance(binding, dict) for binding in raw_bindings
        ):
            raise FrontendAgentUiScaffoldError(
                "正式 ProductPlan.agents"
                f"[{agent_index}].pageActionBindings 必须是对象数组。"
            )
        for binding_index, binding in enumerate(raw_bindings):
            surface = binding.get("surface")
            location = (
                f"正式 ProductPlan.agents[{agent_index}]"
                f".pageActionBindings[{binding_index}].surface"
            )
            if not isinstance(surface, dict):
                raise FrontendAgentUiScaffoldError(f"{location} 必须是对象。")
            surface_type = surface.get("type")
            if surface_type not in _SUPPORTED_SURFACES:
                raise FrontendAgentUiScaffoldError(f"{location}.Surface 类型无效。")
            enabled = surface.get("enabled")
            if not isinstance(enabled, bool):
                raise FrontendAgentUiScaffoldError(f"{location}.enabled 必须是 boolean。")
            required = required or enabled
    return required


def _unexpected_skipped_targets(workspace: Path) -> list[str]:
    """列出无 Agent Surface 时不应存在的固定资产路径。"""

    unexpected = [
        path
        for path in AGENT_UI_FRONTEND_TARGET_PATHS
        if (workspace / path).exists() or (workspace / path).is_symlink()
    ]
    component_directory = workspace / _AGENT_UI_FRONTEND_COMPONENT_DIRECTORY
    if (component_directory.exists() or component_directory.is_symlink()) and not unexpected:
        unexpected.append(_AGENT_UI_FRONTEND_COMPONENT_DIRECTORY.as_posix())
    return unexpected


def inspect_frontend_agent_ui_requirement(workspace: str | Path) -> dict[str, Any]:
    """只读返回当前 ProductPlan 对应的注入条件与固定源码元数据。"""

    workspace_path = Path(workspace).expanduser().resolve()
    required = _agent_ui_required(_confirmed_product_plan(workspace_path))
    try:
        _, source_sha256 = load_agent_ui_frontend_source_assets()
    except FrontendAgentUiAssetError as exc:
        raise FrontendAgentUiScaffoldError(str(exc)) from exc
    return {
        "required": required,
        "templateVersion": AGENT_UI_FRONTEND_TEMPLATE_VERSION,
        "sourceSha256": source_sha256,
        "targetFiles": list(AGENT_UI_FRONTEND_TARGET_PATHS),
    }


def inject_frontend_agent_ui_scaffold(workspace: str | Path) -> dict[str, Any]:
    """按当前 ProductPlan 幂等注入固定资产，并返回可持久化 Manifest 步骤。"""

    workspace_path = Path(workspace).expanduser().resolve()
    frontend = workspace_path / "frontend"
    if not frontend.is_dir() or frontend.is_symlink():
        raise FrontendAgentUiScaffoldError("前端模板目录不存在或不是普通目录。")
    requirement = inspect_frontend_agent_ui_requirement(workspace_path)
    required = bool(requirement["required"])
    try:
        assets, _ = load_agent_ui_frontend_source_assets()
    except FrontendAgentUiAssetError as exc:
        raise FrontendAgentUiScaffoldError(str(exc)) from exc
    common = {
        **requirement,
        "error": None,
    }
    if not required:
        unexpected = _unexpected_skipped_targets(workspace_path)
        if unexpected:
            raise FrontendAgentUiScaffoldError(
                "无启用 Agent Surface 时发现不应存在的 Agent UI 资产："
                + "、".join(unexpected)
            )
        return {**common, "status": "skipped", "targets": []}

    dispositions: dict[str, str] = {}
    for relative_source, relative_target in zip(
        AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS,
        AGENT_UI_FRONTEND_TARGET_PATHS,
    ):
        target = workspace_path / relative_target
        try:
            assert_safe_agent_ui_target(workspace_path, target)
        except FrontendAgentUiAssetError as exc:
            raise FrontendAgentUiScaffoldError(str(exc)) from exc
        if not target.exists():
            dispositions[relative_target] = "created"
            continue
        if not target.is_file():
            raise FrontendAgentUiScaffoldError(
                f"Agent UI 注入目标不是普通文件：{relative_target}"
            )
        try:
            current = target.read_bytes()
        except OSError as exc:
            raise FrontendAgentUiScaffoldError(
                f"Agent UI 注入目标无法读取：{relative_target}"
            ) from exc
        if current != assets[relative_source]:
            raise FrontendAgentUiScaffoldError(
                f"已有文件与固定资产不一致，已保留现场：{relative_target}"
            )
        dispositions[relative_target] = "reused"

    targets: list[dict[str, str]] = []
    for relative_source, relative_target in zip(
        AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS,
        AGENT_UI_FRONTEND_TARGET_PATHS,
    ):
        content = assets[relative_source]
        target = workspace_path / relative_target
        if dispositions[relative_target] == "created":
            try:
                write_missing_agent_ui_file_atomically(target, content)
            except FrontendAgentUiAssetError as exc:
                raise FrontendAgentUiScaffoldError(str(exc)) from exc
        targets.append(
            {
                "path": relative_target,
                "sha256": calculate_sha256(content),
                "status": dispositions[relative_target],
            }
        )
    return {
        **common,
        "status": "succeeded",
        "targets": targets,
        "createdCount": sum(item["status"] == "created" for item in targets),
        "reusedCount": sum(item["status"] == "reused" for item in targets),
    }


def validate_frontend_agent_ui_scaffold(
    workspace: str | Path,
    manifest_step: dict[str, Any],
) -> list[str]:
    """复核 ProductPlan 条件、Manifest 摘要和目标文件内容。"""

    workspace_path = Path(workspace).expanduser().resolve()
    errors: list[str] = []
    try:
        required = _agent_ui_required(_confirmed_product_plan(workspace_path))
        assets, source_sha256 = load_agent_ui_frontend_source_assets()
    except (FrontendAgentUiScaffoldError, FrontendAgentUiAssetError) as exc:
        return [str(exc)]
    if manifest_step.get("required") is not required:
        errors.append("agentUiFrontend.required 与已确认 ProductPlan 不一致")
    expected_status = "succeeded" if required else "skipped"
    if manifest_step.get("status") != expected_status:
        errors.append(f"agentUiFrontend.status 必须为 {expected_status}")
    if manifest_step.get("templateVersion") != AGENT_UI_FRONTEND_TEMPLATE_VERSION:
        errors.append("agentUiFrontend.templateVersion 不匹配")
    if manifest_step.get("sourceSha256") != source_sha256:
        errors.append("agentUiFrontend.sourceSha256 与当前固定资产不匹配")
    if manifest_step.get("targetFiles") != list(AGENT_UI_FRONTEND_TARGET_PATHS):
        errors.append("agentUiFrontend.targetFiles 与固定目标清单不匹配")
    if not required:
        unexpected = _unexpected_skipped_targets(workspace_path)
        if unexpected:
            errors.append(
                "无启用 Agent Surface 时存在 Agent UI 资产："
                + "、".join(unexpected)
            )
        if manifest_step.get("targets") != []:
            errors.append("skipped 的 agentUiFrontend.targets 必须为空数组")
        return errors

    raw_targets = manifest_step.get("targets")
    targets = raw_targets if isinstance(raw_targets, list) else []
    target_by_path = {
        str(item.get("path") or ""): item
        for item in targets
        if isinstance(item, dict)
    }
    if len(target_by_path) != len(AGENT_UI_FRONTEND_TARGET_PATHS):
        errors.append("agentUiFrontend.targets 数量或路径不完整")
    for relative_source, relative_target in zip(
        AGENT_UI_FRONTEND_SOURCE_RELATIVE_PATHS,
        AGENT_UI_FRONTEND_TARGET_PATHS,
    ):
        target = workspace_path / relative_target
        try:
            assert_safe_agent_ui_target(workspace_path, target)
        except FrontendAgentUiAssetError as exc:
            errors.append(str(exc))
            continue
        expected_sha256 = calculate_sha256(assets[relative_source])
        item = target_by_path.get(relative_target)
        if not isinstance(item, dict) or item.get("sha256") != expected_sha256:
            errors.append(f"agentUiFrontend 目标摘要不匹配：{relative_target}")
        if not target.is_file():
            errors.append(f"Agent UI 固定资产缺失：{relative_target}")
            continue
        try:
            actual_sha256 = calculate_sha256(target.read_bytes())
        except OSError:
            errors.append(f"Agent UI 固定资产无法读取：{relative_target}")
            continue
        if actual_sha256 != expected_sha256:
            errors.append(f"Agent UI 固定资产内容漂移：{relative_target}")
    return errors
