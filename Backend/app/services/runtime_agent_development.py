"""智能体独立开发复用正式 DAG、执行切片和生命周期，不持有第二份配置。"""

from pathlib import Path

from app.branding import WORKSPACE_ARTIFACT_DIR
from app.domain.development_artifacts import DevelopmentArtifactTarget
from app.services.agent_runtime_template_policy import AGENT_RUNTIME_MODULES, load_agent_runtime_template_policy
from app.services.runtime_agent_contracts import agent_contract_index, agent_endpoint_keys, MODULE_LABELS
from app.services.runtime_agent_module_policy import agent_module_facts, module_source_refs


def agent_build_context(plan: dict, agent_id: str, plan_path) -> dict:
    """合并当前 Agent 的精确 Tool 前置输入，不纳入关联页面或其他 Agent。"""

    from app.services.build_context_resolver import _endpoint_context

    contract = agent_contract_index(plan).get(agent_id)
    if contract is None:
        raise ValueError(f"正式技术规划不存在智能体 {agent_id}。")
    contexts = [_endpoint_context(plan, endpoint, api, plan_path)
                for api, endpoint in agent_endpoint_keys(contract)]
    result = {"target": {"type": "agent", "id": agent_id},
              "agent_contract": contract, "page_implementation_contract": None, "endpoint_contract": None}
    for key in ("direct_endpoint_contracts", "endpoint_designs", "mapping_flows", "business_descriptions"):
        result[key] = [item for context in contexts for item in context[key]]
    for key in ("endpoint_ids", "required_endpoint_ids", "entity_ids", "source_types", "required_unit_ids"):
        result[key] = list(dict.fromkeys(item for context in contexts for item in context[key]))
    result["required_unit_ids"].append(f"agent:{agent_id}")
    result["source_refs"] = {"technical_plan_agent": {"id": agent_id},
        "technical_plan_endpoints": [item for context in contexts for item in context["source_refs"]["technical_plan_endpoints"]],
        "endpoint_designs": result["endpoint_designs"], "mapping_flows": result["mapping_flows"],
        "business_descriptions": result["business_descriptions"]}
    return result


def module_inputs(plan: dict, agent_id: str) -> dict:
    """保存完成时消费的模块事实作为证据，增量比较不使用固定哈希常量。"""

    return {module: agent_module_facts(plan, agent_id, module) for module in AGENT_RUNTIME_MODULES}


def reconcile_agent_progress(plan: dict, agent_id: str, progress):
    """正式模块输入改变时标记需更新，保留首次开发完成事实。"""

    if progress.initial_development_status != "completed":
        return progress
    previous = (progress.model_extra or {}).get("module_inputs")
    if previous != module_inputs(plan, agent_id):
        return progress.model_copy(update={"change_status": "changed", "change_reason": "智能体正式输入已变化，请更新实现。"})
    return progress


def completed_build_agents(workspace, state: dict, execution) -> list[DevelopmentArtifactTarget]:
    """只登记同轮已确认切片内七模块齐全、成功且仍匹配正式输入的 Agent。"""

    from app.services.build_scheduler import resolve_execution_slice
    from app.services.python_runtime_checks import runtime_check_plan
    from app.workspace.task_documents import build_task_plan_sha256

    plan, scope = state.get("build_task_plan"), state.get("build_execution_scope")
    sliced = state.get("build_execution_slice")
    if (state.get("active_run_id") != execution.run_id or state.get("unit_test_gate_passed") is not True
            or not isinstance(plan, dict) or plan.get("confirmation_status") != "confirmed"
            or not isinstance(scope, dict) or plan.get("build_execution_scope") != scope
            or not state.get("build_run_id") or state.get("build_run_plan_sha256") != build_task_plan_sha256(plan)
            or not isinstance(sliced, dict) or sliced.get("scope") != scope):
        return []
    root = Path(workspace).resolve()
    formal = runtime_check_plan(root, {})
    if formal is None:
        return []
    policy = load_agent_runtime_template_policy(root / "agent-runtime")
    registry = plan.get("task_registry") or {}
    current = {task["id"]: task for task in state.get("tasks", [])}
    selected = resolve_execution_slice(build_task_plan=plan, tasks=list(registry.values()), build_execution_scope=scope)
    selected_ids = set(selected["task_ids"]) & set(sliced.get("task_ids") or [])
    result = []
    for agent_id in agent_contract_index(formal):
        ids = [f"agent:{agent_id}::{module}" for module in AGENT_RUNTIME_MODULES]
        if not all(key in selected_ids and current.get(key, {}).get("status") in {"completed", "already_satisfied"}
                   and current[key].get("unit_id") == f"agent:{agent_id}"
                   and registry[key].get("source_refs") == module_source_refs(formal, registry[key], policy)
                   for key in ids):
            continue
        # 同一切片的 Tool 前置任务也必须成功，不能用单独模块结果冒充完整 Agent。
        if any(current.get(key, {}).get("status") not in {"completed", "already_satisfied"} for key in selected_ids):
            continue
        result.append(DevelopmentArtifactTarget(type="agent", agentId=agent_id))
    return result


def agent_development_payload(root: Path, formal: dict, agent_id: str) -> dict:
    """投影当前正式 DAG 七模块状态、文件及原 execution，失败恢复仍交由主工作流。"""

    from app.services.application_lifecycle import load_application_lifecycle
    from app.workspace.task_documents import load_confirmed_build_task_plan

    lifecycle = load_application_lifecycle(root)
    progress = lifecycle.development_artifacts.agents.get(agent_id) if lifecycle else None
    if progress:
        progress = reconcile_agent_progress(formal, agent_id, progress)
    plan = load_confirmed_build_task_plan(root) or {}
    registry = plan.get("task_registry") or {}
    # 尚未生成 DAG 时只展示待开发，不因模板策略尚不存在而阻断整个目录。
    policy = load_agent_runtime_template_policy(root / "agent-runtime") if registry else None
    modules = []
    for module in AGENT_RUNTIME_MODULES:
        task = registry.get(f"agent:{agent_id}::{module}")
        valid = bool(task and task.get("source_refs") == module_source_refs(formal, task, policy))
        modules.append({"module": module, "label": MODULE_LABELS[module],
            "status": task.get("status", "pending") if valid else "pending",
            "reason": task.get("failure_reason") if valid else "尚未开发或正式输入已变化",
            "files": task.get("target_files", []) if task else []})
    owner = next((item for item in lifecycle.active_executions.values()
                  if f"agent:{agent_id}" in item.resource_keys or "agent:runtime" in item.resource_keys), None) if lifecycle else None
    return {"progress": progress.model_dump(mode="json", by_alias=True, exclude={"module_inputs"}) if progress else None,
            "modules": modules, "execution": owner.model_dump(mode="json", by_alias=True) if owner else None}
