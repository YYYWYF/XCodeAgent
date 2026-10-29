"""沿现有 Engine capability 合同声明 Runtime 布局，不另建模板传输或状态。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.template_reconcile.desired import compile_template_capabilities
from app.services.workspace_bootstrap.models import TemplateConfigError

RUNTIME_CAPABILITY = "python_application_runtime"
STANDARD_ROOTS = ("frontend", "backend")
RUNTIME_ROOTS = ("frontend", "agent-runtime")
ALL_SOURCE_ROOTS = frozenset((*STANDARD_ROOTS, *RUNTIME_ROOTS))
RUNTIME_CAPABILITIES = (
    RUNTIME_CAPABILITY, "python_business_api", "python_domain_model", "python_repository",
    "agent_runtime_public_edge", "agent_runtime_principal_ownership",
    "agent_runtime_local_debug", "agent_runtime_anonymous_session", "python_business_mysql",
)


def template_roots(capabilities: dict[str, Any]) -> tuple[str, ...]:
    """只按明确请求的 capability 选择布局，不按工程目录或 Agent 数量推断。"""

    runtime = capabilities.get(RUNTIME_CAPABILITY)
    if runtime is None:
        return STANDARD_ROOTS
    if hasattr(runtime, "model_dump"):
        runtime = runtime.model_dump(mode="json")
    if runtime != {"enabled": True, "config": {}}:
        raise TemplateConfigError("python_application_runtime 必须显式启用且 config 为空对象。")
    return RUNTIME_ROOTS


def load_template_plan(root: Path) -> dict[str, Any]:
    """读取当前技术规划；未进入技术规划的现有 Java 配置动作允许无规划文件。"""

    path = root / WORKSPACE_ARTIFACT_DIR / "plans/technical-plan.json"
    if not path.exists() and not path.is_symlink():
        return {}
    if path.is_symlink() or not path.is_file():
        raise TemplateConfigError("当前 TechnicalPlan 路径无效。")
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as exc:
        raise TemplateConfigError("当前 TechnicalPlan 无法读取。") from exc
    if not isinstance(plan, dict):
        raise TemplateConfigError("当前 TechnicalPlan 必须是对象。")
    return plan


def compile_workspace_template_capabilities(
    root: Path, application_config: dict[str, Any], *, plan: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    """保留应用能力权威，叠加已确认拓扑固定要求的实现能力供 Generate/V4 共用。"""

    capabilities = compile_template_capabilities(application_config)
    current = load_template_plan(root) if plan is None else plan
    topology = current.get("topology")
    if topology is None:
        return capabilities
    if not isinstance(topology, dict):
        raise TemplateConfigError("TechnicalPlan.topology 必须是对象。")
    if topology.get("type") == "backend_direct":
        if topology.get("serviceIds") != ["backend"] or topology.get("publicEdgeServiceId") != "backend":
            raise TemplateConfigError("Java 拓扑服务边界无效。")
        return capabilities
    if topology.get("type") != "agent_runtime_direct":
        raise TemplateConfigError("当前模板不支持该显式拓扑。")
    if current.get("artifact_type") != "technical-plan" or current.get("confirmation_status") != "confirmed":
        raise TemplateConfigError("Runtime 模板必须使用已确认的技术规划。")
    if topology.get("serviceIds") != ["agent-runtime"] or topology.get("publicEdgeServiceId") != "agent-runtime":
        raise TemplateConfigError("Runtime 拓扑服务边界无效。")
    # Python 登录与授权配置面未交付前禁止改写 Java 配置或静默关闭应用开关。
    if application_config["auth"]["enable"] or application_config["authorization"]["enabled"]:
        raise TemplateConfigError("Python 登录／授权模板配置面尚未交付，当前 Runtime 模板仅支持已明确关闭登录和授权的应用。")
    if topology.get("authenticationTermination") != "anonymous-session":
        raise TemplateConfigError("Runtime 匿名会话认证终止点无效。")
    return {**capabilities, **{name: {"enabled": True, "config": {}} for name in RUNTIME_CAPABILITIES}}


def validate_template_layout_state(state: Any, roots: tuple[str, ...]) -> None:
    """要求模板实际能力和受管文件归属与请求布局一致，禁止夹带 Java 后端。"""

    actual = template_roots(state.requested)
    if actual != roots:
        raise TemplateConfigError("TemplateState 与当前请求的工程布局不一致。")
    if roots != RUNTIME_ROOTS:
        if RUNTIME_CAPABILITY in state.effective:
            raise TemplateConfigError("Engine 未经请求启用了 Python Runtime。")
        return
    missing = set(RUNTIME_CAPABILITIES) - set(state.effective)
    if missing:
        raise TemplateConfigError("Runtime 模板缺少能力：" + "、".join(sorted(missing)))
    if state.managedContributions or any(item.target.startswith("backend/") for item in state.installedArtifacts.values()):
        raise TemplateConfigError("Runtime Direct 模板不得包含 Java 构建贡献或 Backend 文件归属。")
