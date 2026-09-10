"""校验 Workspace Bootstrap 在事务提交前的完整就绪条件。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from app.services.template_state import effective_capabilities, load_template_state, requested_capabilities
from app.services.workspace_bootstrap.git_manager import BootstrapGitManager
from app.services.workspace_bootstrap.models import WorkspaceBootstrapReadinessError

_STAGING_RELATIVE_PATH = Path(".xcodeagent/bootstrap-staging")


class GitBaselineVerifier(Protocol):
    """定义 Readiness 对 Git baseline 的最小验证接口。"""

    def verify_baseline(self, workspace: str | Path) -> str:
        """验证工作区 Git baseline 并返回 HEAD。"""


def validate_workspace_bootstrap_readiness(
    workspace: str | Path,
    *,
    requested_config: dict[str, Any],
    git_manager: GitBaselineVerifier | None = None,
) -> None:
    """验证正式产物、真实入口、State 映射、Git 和 staging 均符合当前契约。"""

    root = Path(workspace).expanduser().resolve()
    if not root.is_dir() or root.is_symlink():
        raise WorkspaceBootstrapReadinessError("Workspace 必须是非符号链接目录。")
    _validate_formal_artifacts(root)
    _validate_template_roots(root)
    state = load_template_state(root)
    _validate_requested_capabilities(state, requested_config)
    _validate_entrypoints(root)
    _validate_staging_absent(root)
    (git_manager or BootstrapGitManager()).verify_baseline(root)


def _validate_formal_artifacts(workspace: Path) -> None:
    """要求 Bootstrap 所依赖的正式产物均已确认，UiDesign 可被明确跳过。"""

    statuses = {
        "RequirementSpec": _artifact_confirmation_status(workspace / ".xcodeagent/specs/requirement-spec.json"),
        "ProductPlan": _artifact_confirmation_status(workspace / ".xcodeagent/plans/product-plan.json"),
        "UiDesign": _artifact_confirmation_status(workspace / ".xcodeagent/specs/ui-designs.json"),
        "TechnicalPlan": _artifact_confirmation_status(
            workspace / ".xcodeagent/plans/technical-plan.json",
            expected_artifact_type="technical-plan",
        ),
    }
    if not (
        statuses["RequirementSpec"] == "confirmed"
        and statuses["ProductPlan"] == "confirmed"
        and statuses["UiDesign"] in {"confirmed", "skipped"}
        and statuses["TechnicalPlan"] == "confirmed"
    ):
        raise WorkspaceBootstrapReadinessError("正式产物未确认，不能完成 Workspace Bootstrap。")


def _validate_template_roots(workspace: Path) -> None:
    """确认两个模板根与 Git 元数据是受管的真实目录。"""

    for name in ("frontend", "backend", ".git"):
        path = workspace / name
        if not path.is_dir() or path.is_symlink():
            raise WorkspaceBootstrapReadinessError(f"Bootstrap 缺少有效 {name}。")


def _validate_requested_capabilities(state: dict[str, Any], requested_config: dict[str, Any]) -> None:
    """要求 Engine 原样回写的 requested 与应用编译请求完全一致。"""

    expected = _enabled_requested_capabilities(requested_config)
    requested = requested_capabilities(state)
    if requested != expected:
        raise WorkspaceBootstrapReadinessError("TemplateState.requested 与 Bootstrap 请求不一致。")
    effective = effective_capabilities(state)
    missing = sorted(set(expected) - set(effective))
    if missing:
        raise WorkspaceBootstrapReadinessError(
            "TemplateState.effective 缺少已请求能力：" + "、".join(missing)
        )


def _enabled_requested_capabilities(requested_config: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """从已编译请求提取 Engine 应原样持久化的 enabled capability 映射。"""

    capabilities = requested_config.get("capabilities") if isinstance(requested_config, dict) else None
    if not isinstance(capabilities, dict):
        raise WorkspaceBootstrapReadinessError("Bootstrap 请求缺少 capabilities 对象。")
    enabled: dict[str, dict[str, Any]] = {}
    for capability_id, definition in capabilities.items():
        if not isinstance(capability_id, str) or not capability_id.strip() or not isinstance(definition, dict):
            raise WorkspaceBootstrapReadinessError("Bootstrap 请求包含无效 capability 定义。")
        if not isinstance(definition.get("enabled"), bool):
            raise WorkspaceBootstrapReadinessError("Bootstrap 请求 capability.enabled 必须为布尔值。")
        config = definition.get("config") or {}
        if not isinstance(config, dict):
            raise WorkspaceBootstrapReadinessError("Bootstrap 请求 capability.config 必须是对象。")
        if definition["enabled"]:
            enabled[capability_id] = {"enabled": True, "config": config}
    return {capability_id: enabled[capability_id] for capability_id in sorted(enabled)}


def _validate_entrypoints(workspace: Path) -> None:
    """验证前后端最小真实入口，避免空目录或仅占位文件成为 READY。"""

    package_json = workspace / "frontend/package.json"
    pom = workspace / "backend/pom.xml"
    java_root = workspace / "backend/src/main/java"
    if not package_json.is_file() or package_json.is_symlink():
        raise WorkspaceBootstrapReadinessError("Bootstrap 缺少 frontend/package.json。")
    if not pom.is_file() or pom.is_symlink():
        raise WorkspaceBootstrapReadinessError("Bootstrap 缺少 backend/pom.xml。")
    if not java_root.is_dir() or java_root.is_symlink() or not any(
        path.is_file() and not path.is_symlink() for path in java_root.rglob("*Application.java")
    ):
        raise WorkspaceBootstrapReadinessError("Bootstrap 缺少 backend Spring Boot Application 入口。")


def _validate_staging_absent(workspace: Path) -> None:
    """确认事务 staging 在提交前已完全消失，避免 Attach 看到半成品。"""

    staging = workspace / _STAGING_RELATIVE_PATH
    if staging.exists() or staging.is_symlink():
        raise WorkspaceBootstrapReadinessError("Bootstrap staging 未清理完成。")


def _artifact_confirmation_status(path: Path, *, expected_artifact_type: str | None = None) -> str | None:
    """严格读取当前正式 JSON 的确认状态和可选产物类型。"""

    if not path.is_file() or path.is_symlink():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return "invalid"
    if expected_artifact_type and (
        not isinstance(value, dict) or value.get("artifact_type") != expected_artifact_type
    ):
        return "invalid"
    confirmation_status = value.get("confirmation_status") if isinstance(value, dict) else None
    return confirmation_status if isinstance(confirmation_status, str) else "invalid"
