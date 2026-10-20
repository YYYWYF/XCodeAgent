"""阶段入口从当前磁盘加载业务产物，不以旧 Graph State 充当资产来源。"""

from functools import wraps
from dataclasses import replace
from inspect import iscoroutinefunction
from pathlib import Path
from typing import Any

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.services.run_workers import WorkerStateGraph
from app.services.generation_baseline import prepare_generation_baseline
from app.workspace.plan_documents import load_project_plan_json
from langgraph.types import Command


_STAGES = {
    "requirements", "product_planning", "ui_confirmation",
    "technical_planning_begin", "technical_planning_generate", "technical_planning_commit",
    "technical_planning_confirm", "template_reconcile",
    "api_design_readiness_gate", "project_planning", "inspect_workspace", "prepare_build_tasks",
    "authorization_bootstrap", "build", "unit_test", "unit_test_repair",
    "integration_test", "small_task_repair", "code_review", "acceptance",
    "test_phase_confirmation", "review_phase_confirmation", "acceptance_phase_confirmation",
    "scan_workspace_code", "scan_change_impact_code", "classify_intent", "execute_frontend",
    "execute_backend", "execute_workspace", "validate_direct_fix", "direct_modification_repair",
    "finalize_direct_modification", "finalize_project",
}
_REQUIREMENT_STAGES = {"requirements", "product_planning"}
_RUNTIME_STAGES = {
    "api_design_readiness_gate", "prepare_build_tasks", "authorization_bootstrap",
    "build", "unit_test", "unit_test_repair", "integration_test", "small_task_repair", "code_review",
    "acceptance", "test_phase_confirmation", "review_phase_confirmation", "acceptance_phase_confirmation", "finalize_project",
}
_ARTIFACTS = {
    "requirement_spec": "specs/requirement-spec.json",
    "product_plan": "plans/product-plan.json",
    "ui_designs": "specs/ui-designs.json",
    "technical_plan": "plans/technical-plan.json",
}
# 只声明节点实际消费的产物；扫描和二次修改节点自行读取工作区源码，无正式规划依赖。
_PLANNING_INPUTS = {
    "requirements": {"requirement_spec"},
    "product_planning": {"requirement_spec", "product_plan"},
    "ui_confirmation": {"requirement_spec", "product_plan", "ui_designs"},
}
_TECHNICAL_STAGES = {
    "technical_planning_begin", "technical_planning_generate", "technical_planning_commit",
    "technical_planning_confirm", "template_reconcile", "project_planning",
}
_TASK_PLAN_STAGES = _RUNTIME_STAGES - {"api_design_readiness_gate", "authorization_bootstrap"}
_REVIEW_FILE_STAGES = _TASK_PLAN_STAGES - {"prepare_build_tasks", "build"}
# 仅生成节点的自身旧输出是可选基线，确认及下游执行仍严格读取必需输入。
_GENERATED_BASELINES = {
    "requirements": "requirement_spec", "product_planning": "product_plan",
    "ui_confirmation": "ui_designs", "technical_planning_begin": "technical_plan",
    "technical_planning_generate": "technical_plan",
}


def load_stage_artifacts(state: dict[str, Any], node: str) -> dict[str, Any]:
    """按当前阶段读取正式文件和需求联合草稿，缺失时清除旧资产快照。"""
    workspace = str(state.get("workspace") or state.get("workspace_path") or "").strip()
    if not workspace or node not in _STAGES:
        return {}
    from app.services.stage_prerequisites import require_stage_inputs
    require_stage_inputs(state, node)
    root = Path(workspace) / WORKSPACE_ARTIFACT_DIR
    required = (
        set(_ARTIFACTS) if node in _TECHNICAL_STAGES | _RUNTIME_STAGES
        else _PLANNING_INPUTS.get(node, set())
    )
    fields: dict[str, Any] = {}
    for key, relative in _ARTIFACTS.items():
        path = root / relative
        if node in _REQUIREMENT_STAGES and key in required:
            draft = root / "drafts" / relative
            if draft.is_file():
                path = draft
        # 未消费的下游产物只清空 State 投影，绝不检查或解析磁盘，损坏文件也不能挡住上游。
        exists = key in required and path.is_file()
        if exists and _GENERATED_BASELINES.get(node) == key:
            exists = prepare_generation_baseline(path)
        value = load_project_plan_json(path, hydrate_detail_designs=True) if exists else {}
        if not isinstance(value, dict):
            raise ValueError(f"阶段产物不是有效对象：{path.name}")
        fields[key] = value
        fields[f"{key}_json_path"] = str(path) if key in required else ""
        fields[f"{key}_path"] = str(path.with_suffix(".md")) if key in required else ""
    # ProjectPlan 是当前正式 TechnicalPlan 的运行时投影，不是另一份历史资产。
    from app.services.page_implementation_contract import materialize_technical_plan_runtime
    plan = fields["technical_plan"]
    fields["project_plan"] = (
        materialize_technical_plan_runtime(plan, fields["requirement_spec"], fields["product_plan"], fields["ui_designs"])
        if plan and node in _RUNTIME_STAGES and fields["product_plan"] else plan
    )
    fields["project_plan_json_path"] = str(root / _ARTIFACTS["technical_plan"]) if "technical_plan" in required else ""
    if "technical_plan" not in required:
        fields["project_plan_path"] = ""
    if node in _TASK_PLAN_STAGES:
        from app.workspace.task_documents import load_confirmed_build_task_plan, build_task_plan_json_path
        if node == "prepare_build_tasks":
            prepare_generation_baseline(build_task_plan_json_path({"workspace": workspace}))
        fields["build_task_plan"] = load_confirmed_build_task_plan(workspace) or {}
    if node in _REVIEW_FILE_STAGES:
        from app.services.development_review_files import development_review_files
        # 下游测试/审查以当前已完成模块和 Git 文件为准，不依赖旧 Run 的内存变更集合。
        fields["development_review_files"] = development_review_files(workspace)
    if node in {"unit_test", "unit_test_repair", "test_phase_confirmation"}:
        from app.services.stage_execution_artifacts import load_stage_execution_artifacts
        fields.update(load_stage_execution_artifacts({**state, **fields}, fields["build_task_plan"], node))
    if state.get("retry_source_run_id") and node in {"classify_intent", "execute_frontend", "execute_backend", "execute_workspace", "code_review", "unit_test", "integration_test"}:
        from app.graph.nodes.workspace_inspection import inspect_workspace
        fields.update({key: value for key, value in inspect_workspace({**state, **fields}).items() if key.startswith("workspace_")})
    return fields


def _stage_result(fields: dict[str, Any], result: Any) -> Any:
    """合并磁盘资产，同时保留确认节点 Command 的路由和续接语义。"""
    if isinstance(result, Command):
        return replace(result, update={**fields, **(result.update or {})})
    return {**fields, **result}


class ArtifactStateGraph(WorkerStateGraph):
    """把磁盘读取放在业务节点入口，正常执行和重新执行使用同一套输入。"""

    def add_node(self, node, action=None, **kwargs):
        """仅包装业务阶段函数，保留节点配置注入签名和真实工作退出登记。"""
        target = action if action is not None else node
        name = node if isinstance(node, str) else getattr(node, "__name__", "")
        if name in _STAGES and hasattr(target, "ainvoke"):
            async def hydrated_subgraph(state, config):
                """子图也在阶段入口刷新磁盘资产，并保留父图运行配置。"""
                fields = load_stage_artifacts(state, name)
                result = await target.ainvoke({**state, **fields}, config)
                return _stage_result(fields, result)
            return super().add_node(name, hydrated_subgraph, **kwargs)
        if name in _STAGES and callable(target):
            if iscoroutinefunction(target):
                @wraps(target)
                async def hydrated(state, *args, **call_kwargs):
                    """异步阶段先读取当前资产，再保留节点返回的最新修改。"""
                    fields = load_stage_artifacts(state, name)
                    result = await target({**state, **fields}, *args, **call_kwargs)
                    return _stage_result(fields, result)
            else:
                @wraps(target)
                def hydrated(state, *args, **call_kwargs):
                    """同步阶段先读取当前资产，再保留节点返回的最新修改。"""
                    fields = load_stage_artifacts(state, name)
                    result = target({**state, **fields}, *args, **call_kwargs)
                    return _stage_result(fields, result)
            return super().add_node(name, hydrated, **kwargs)
        return super().add_node(node, action, **kwargs)
