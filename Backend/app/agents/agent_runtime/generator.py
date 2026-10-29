"""执行新版确认 DAG 中的 Python Endpoint 业务任务。"""

from copy import deepcopy
import fnmatch
import json
from typing import Any

from app.agents.agent_runtime.agent import create_agent_runtime_agent
from app.agents.agent_runtime.scope import runtime_source_path
from app.agents.data_source.prompt_context import task_endpoint_designs, task_implementation_contract
from app.agents.field_value_prompt_rules import field_value_implementation_rules
from app.agents.tool_activity_stream import ToolActivityCallback, invoke_agent_with_tool_activity
from app.config import Settings
from app.domain.api_design import EndpointFieldMappingDesign
from app.services.build_result_coordinator import create_agent_task_results
from app.services.runtime_business_contract import serves_python_business
from app.services.runtime_business_paths import runtime_business_path_error


def runtime_task_paths(task: dict[str, Any]) -> tuple[str, ...]:
    """交叉核对确认任务的三个写范围，拒绝 Java、SQL、环境和未声明文件。"""

    targets = task.get("target_files")
    changes = task.get("change_scope")
    allowed = task.get("allowed_paths")
    bootstrap = task.get("unit_id") == "backend:bootstrap"
    if (not isinstance(targets, list) or not targets
            or not isinstance(changes, list) or not isinstance(allowed, list)
            or any(not isinstance(path, str) for path in allowed)
            or any(not isinstance(item, dict) for item in changes)):
        raise ValueError("Runtime 任务必须提供完整的精确文件写范围。")
    if any(not runtime_source_path(path, bootstrap=bootstrap) for path in targets):
        raise ValueError("Runtime 任务包含非 Python 业务路径或未授权的基础配置。")
    if (len(targets) != len(set(targets)) or len(changes) != len(targets)
            or set(targets) != {item.get("path") for item in changes}
            or any(item.get("operation") not in {"add", "modify"} for item in changes)
            or any(not any(fnmatch.fnmatchcase(path, pattern) for pattern in allowed) for path in targets)):
        raise ValueError("Runtime 任务的 target_files、change_scope 和 allowed_paths 不一致。")
    # 与 Local Validation 使用同一写范围，不能让规划通过后才发现平台职责冲突。
    errors = [error for path in targets if (error := runtime_business_path_error(path, bootstrap=bootstrap))]
    if errors:
        raise ValueError("；".join(errors))
    return tuple(targets)


def python_business_task_packet(plan: dict[str, Any], task: dict[str, Any]) -> dict[str, Any]:
    """复用新版 Endpoint 设计裁剪事实，替换 Java 专有实现元数据。"""

    if not serves_python_business(plan):
        raise ValueError("Python 业务执行器只接受已明确选择 Runtime 的计划。")
    if task.get("owner") != "backend" or not str(task.get("unit_id") or "").startswith("backend:"):
        raise ValueError("Python 业务任务必须属于当前 Backend Endpoint DAG。")
    paths = runtime_task_paths(task)
    bootstrap = task.get("unit_id") == "backend:bootstrap"
    if bootstrap:
        snapshots = [snapshot for design in task_endpoint_designs(task)
                     for snapshot in design.get("sourceSnapshots") or [] if isinstance(snapshot, dict)]
        source_types = sorted({item.get("sourceType") for item in snapshots if item.get("sourceType")})
        if not source_types or set(source_types) - {"database", "external_api"}:
            raise ValueError("Python Bootstrap 缺少明确的 Endpoint 数据源事实。")
        contract = {"kind": "bootstrap", "source_types": source_types}
    else:
        # 该投影器只裁剪已确认业务合同；不把其 Java Skill、语言或预置代码送入 Python。
        contract = deepcopy(task_implementation_contract(plan, task))
        for key in ("language", "prebuilt_files"):
            contract.pop(key, None)
        authorization = contract.pop("authorization_constraints", None)
        if authorization and authorization.get("operationResourceKeys"):
            raise ValueError("Python 授权实现未交付，不能忽略 Endpoint 权限。")
        design = contract["api_design"]
        EndpointFieldMappingDesign.model_validate(design)
        target = (task.get("source_refs") or {}).get("target") or {}
        if (design.get("status") != "confirmed"
                or design.get("apiContractId") != target.get("api_contract_id")
                or design.get("endpointId") != target.get("id")):
            raise ValueError("Python 任务的 Endpoint Design 未确认或与当前任务身份不一致。")
        if (contract.get("api_contract", {}).get("id") != target.get("api_contract_id")
                or len(contract.get("api_contract", {}).get("endpoints") or []) != 1):
            raise ValueError("Python Endpoint 任务必须精确绑定一个正式接口。")
    return {
        "id": task["id"], "unit_id": task["unit_id"],
        "stage": "bootstrap" if bootstrap else str(task["id"]).rsplit("::", 1)[-1],
        "description": task.get("description"), "target_files": list(paths),
        "change_scope": deepcopy(task["change_scope"]),
        "implementation_contract": {**contract, "language": "python3.12", "framework": "fastapi",
                                    "verification_policy": "outer_integration_test_only"},
    }


def generate_python_business_with_deep_agent(
    *, project_plan: dict[str, Any], build_task_plan: dict[str, Any],
    workspace_snapshot: dict[str, Any], tasks: list[dict[str, Any]],
    workspace: str | None = None, selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """逐任务创建精确权限执行器，沿原工具活动、结果和真实 Diff 合同收口。"""

    del build_task_plan, workspace_snapshot
    if not tasks:
        return []
    if not workspace:
        raise ValueError("Python 业务代码生成必须绑定真实工作区。")
    # 所有任务先验证再开始写入，避免后续任务合同非法时前面已经发生变更。
    packets = [python_business_task_packet(project_plan, task) for task in tasks]
    results: list[dict[str, Any]] = []
    for task, packet in zip(tasks, packets):
        agent = create_agent_runtime_agent(workspace, tuple(packet["target_files"]), selected_skill_names)
        prompt = (
            "Execute only this approved Python business task. Use the exact confirmed request, "
            "response envelope, field mappings and source snapshots. Reuse the real Runtime template. "
            "REST and Agent Tools delegate to the same ApplicationService. Business tables use MySQL "
            "and exact confirmed table_name; use parameterized SQL, existing transaction boundaries "
            "and startup-injected credentials. Never create or alter tables, invent SQLite storage, "
            "initialize a new model or fetch secrets. Preserve trusted Principal ownership. "
            "Controller only adapts request/response; the service owns mapping and composition. "
            "Missing inputs or unsupported behavior must return failed with contract_mismatch. "
            "Do not run commands or verification.\n"
            + field_value_implementation_rules("Python")
            + "\nReturn exactly {\"task_results\":[{\"task_id\":...,\"status\":...,\"summary\":...}]}. "
            "Use completed after actual writes, already_satisfied only with non-empty "
            "satisfaction_evidence identifying inspected files and findings, or failed with "
            "failure_category and failure_reason. Never return unknown task IDs or changed_files; "
            "the platform captures the actual Diff.\nTask packet:\n"
            + json.dumps(packet, ensure_ascii=False, indent=2)
        )
        note = invoke_agent_with_tool_activity(
            agent, {"messages": [{"role": "user", "content": prompt}]},
            workspace=workspace, on_tool_activity=on_tool_activity,
        )
        current = create_agent_task_results(
            [task], note, require_structured=True, strict_schema=True,
            executed_by={"agent": "agent-runtime-generation-agent", "mode": "live",
                         "model": Settings.from_env().model_name, "source": "python_runtime_business",
                         "requiredSkillsLoaded": list(selected_skill_names or [])},
        )
        results.extend(current)
        if any(item.get("status") == "failed" for item in current):
            results.extend({
                "task_id": pending["id"], "owner": pending["owner"], "status": "failed",
                "failure_category": "dependency_failed",
                "failure_reason": "前序 Python 任务失败，本批后续任务未执行。",
                "summary": "前序任务失败，未执行。",
            } for pending in tasks[len(results):])
            break
    return results
