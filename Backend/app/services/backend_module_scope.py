"""按接口职责选择模块任务，避免模块 Unit 扩大当前开发范围。"""

from collections.abc import Mapping, Sequence

from app.services.unit_generation_requirement_targets import backend_foundation_requirement_id
from app.services.unit_task_manifest import BACKEND_MODULE_PREFIX


def design_endpoint_keys(designs: Sequence) -> set[tuple[str, str]]:
    """从正式设计提取复合接口身份，排除缺少合同或接口 ID 的记录。"""
    return {(item["apiContractId"], item["endpointId"]) for item in designs
            if isinstance(item, Mapping) and item.get("apiContractId") and item.get("endpointId")}


def task_endpoint_keys(task: Mapping) -> set[tuple[str, str]]:
    """提取后端模块任务的精确交付范围，修复任务沿用父任务的范围。"""
    unit_id = str(task.get("unit_id") or "")
    if not unit_id.startswith(BACKEND_MODULE_PREFIX):
        return set()
    contract = unit_id.removeprefix(BACKEND_MODULE_PREFIX)
    return {(contract, item["target_id"]) for item in task.get("deliverables", ())
            if isinstance(item, Mapping) and item.get("target_id")}


def scope_endpoint_keys(tasks: Sequence[Mapping], scope: Mapping) -> set[tuple[str, str]]:
    """按接口目标或页面已冻结的设计定位当前范围。"""
    if scope["type"] == "endpoint":
        return {(scope.get("apiContractId", ""), scope["targetId"])}
    if scope["type"] != "page":
        return set()
    return {
        key for task in tasks if task.get("unit_id") == f"page:{scope['targetId']}"
        for key in design_endpoint_keys((task.get("source_refs") or {}).get("endpoint_designs", ()))
    }


def backend_task_scope_projection(task: Mapping) -> dict:
    """公开任务阶段、覆盖接口的名称和 HTTP 身份，不投射完整正式设计。"""
    refs = task.get("source_refs") or {}
    stage = refs.get("backend_stage")
    if stage not in {"foundation", "business", "controller"}:
        return {}
    covered = []
    for design in refs.get("endpoint_designs", ()):
        endpoint = design.get("endpointContract") or {}
        covered.append({
            "id": design["endpointId"], "method": endpoint.get("method") or "",
            "path": endpoint.get("path") or "",
            "summary": endpoint.get("summary") or "",
        })
    return {"backendStage": stage, "coveredEndpoints": covered}


def scoped_task_ids(
    tasks: Sequence[Mapping], scope: Mapping, units: set[str], *, extra_task_ids: Sequence[str] = (),
) -> set[str]:
    """从当前职责选任务，补齐待执行任务的依赖，不重走成功任务的历史依赖。"""
    by_id = {str(task["id"]): task for task in tasks if task.get("id")}
    endpoints = scope_endpoint_keys(tasks, scope)
    selected = {
        task_id for task_id, task in by_id.items()
        if (unit_id := str(task.get("unit_id") or "application:root")) in units
        and (scope["type"] == "application" or unit_id != "backend:bootstrap")
        and (scope["type"] == "application" or not unit_id.startswith(BACKEND_MODULE_PREFIX)
             or bool(task_endpoint_keys(task) & endpoints))
    }
    selected.update(task_id for task_id in extra_task_ids if task_id in by_id)
    pending = list(selected)
    while pending:
        task_id = pending.pop()
        task = by_id[task_id]
        if task.get("status") in {"completed", "already_satisfied"}:
            continue
        for dependency in task.get("dependencies", ()):
            if dependency in by_id and dependency not in selected:
                selected.add(dependency)
                pending.append(dependency)
    return selected


def module_task_dependencies(
    tasks: list[dict], foundation_providers: Mapping, scoped_endpoints: set[tuple[str, str]],
) -> dict[str, list[str]]:
    """绑定基础骨架与实际接口消费者、Controller 与各自业务任务。"""
    foundations: dict[tuple[str, str], list[dict]] = {}
    business: dict[tuple[str, str], list[dict]] = {}
    tasks_by_id = {task["id"]: task for task in tasks if task.get("id")}
    for task in tasks:
        if task.get("kind") == "repair":
            continue
        stage = (task.get("source_refs") or {}).get("backend_stage")
        index = foundations if stage == "foundation" else business if stage == "business" else None
        if index is not None:
            for key in task_endpoint_keys(task):
                index.setdefault(key, []).append(task)

    dependencies = {}
    for task in tasks:
        endpoints = task_endpoint_keys(task)
        if not endpoints or task.get("kind") == "repair" or not task.get("id"):
            continue
        stage = (task.get("source_refs") or {}).get("backend_stage")
        providers: list[dict] = []
        if stage == "business":
            for contract_id, endpoint_id in endpoints:
                providers.extend(foundations.get((contract_id, endpoint_id), ()))
                capability = backend_foundation_requirement_id(contract_id, endpoint_id)
                providers.extend(tasks_by_id[provider] for provider in foundation_providers.get(capability, ())
                                 if provider in tasks_by_id)
        elif stage == "controller":
            contract = task["unit_id"].removeprefix(BACKEND_MODULE_PREFIX)
            current_keys = {key for key in scoped_endpoints if key[0] == contract}
            keys = current_keys if endpoints & current_keys else endpoints
            providers = [provider for key in keys for provider in business.get(key, ())]
        dependencies[task["id"]] = sorted({provider["id"] for provider in providers})
        if stage == "business" and not task.get("source_refs", {}).get("foundation_paths"):
            # 基础文件是只读验收输入，包含复用来源的源码，不计入 Agent 写入范围。
            task["source_refs"]["foundation_paths"] = sorted({
                path for provider in providers for item in provider.get("deliverables", ())
                if item.get("kind") == "backend.foundation" and (
                    not task_endpoint_keys(provider) & endpoints
                    or any(item.get("target_id") == endpoint for _, endpoint in endpoints)
                )
                for path in item.get("paths", ())
            })
    return dependencies
