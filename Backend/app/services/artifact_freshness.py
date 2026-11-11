"""读取当前正式产物的依赖事实，为正常开发与重试选择同一重生成入口。"""

from pathlib import Path
from typing import Any

from app.services.artifact_invalidation import canonical_sha256


def upstream_errors(root: Path, key: str, value: dict[str, Any], *, include_ui_designs: bool = True) -> list[str]:
    """校验文件依赖与各正式产物原生语义摘要，不把旧确认状态当作有效证据。"""
    from app.services.stage_artifacts import _ARTIFACTS
    from app.workspace.plan_documents import load_project_plan_json
    from app.services.product_plan import requirement_spec_sha256
    from app.services.page_implementation_contract import _artifact_hash
    from app.services.ui_design_manifest import persisted_ui_manifest

    paths = {name.replace("_", "-"): root / relative for name, relative in _ARTIFACTS.items()}
    paths["ui-design"] = root / _ARTIFACTS["ui_designs"]
    references = value.get("basedOn") or []
    errors = []
    for reference in references:
        if not include_ui_designs and isinstance(reference, dict) and reference.get("artifactKey") == "ui-design":
            continue
        path = paths.get(reference.get("artifactKey")) if isinstance(reference, dict) else None
        if path is None or not path.is_file() or reference.get("sha256") != canonical_sha256(path):
            errors.append("直接上游已变化")
    dependencies = {
        "product_plan": [("requirement_spec_sha256", "requirement_spec", requirement_spec_sha256)],
        "ui_designs": [("product_plan_sha256", "product_plan", _artifact_hash)],
        "technical_plan": [
            ("product_plan_sha256", "product_plan", _artifact_hash),
            ("ui_designs_sha256", "ui_designs", lambda item: _artifact_hash(persisted_ui_manifest(item))),
        ],
    }
    for field, upstream, digest in dependencies.get(key, []):
        if upstream == "ui_designs" and not include_ui_designs:
            continue
        if field in value:
            current = load_project_plan_json(root / _ARTIFACTS[upstream])
            if value[field] != digest(current):
                errors.append(f"{upstream} 已变化")
    status = value.get("confirmation_status")
    if status is not None and status != "confirmed":
        errors.append("当前版本尚未确认")
    return errors


def task_plan_errors(state: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """按实际执行切片校验直接输入，范围外接口不使用本轮上下文重新编译。"""
    from app.services.build_context_resolver import resolve_confirmation_context
    from app.services.build_scheduler import resolve_execution_slice
    from app.services.backend_module_scope import task_endpoint_keys
    from app.services.api_design import load_confirmed_endpoint_designs
    from app.services.application_config import read_application_config
    from app.services.authorization_overlay import compile_authorization_overlay
    from app.services.task_input_freshness import task_covers_current_input

    scope = state.get("build_execution_scope") or {}
    if scope.get("type") not in {"page", "endpoint"}:
        return []
    resolved = resolve_confirmation_context(state["workspace"], scope)
    context = resolved["build_context"]
    current = resolved["project_plan"]
    bound_digest = state.get("build_run_plan_sha256")
    if bound_digest:
        from app.workspace.task_documents import build_task_plan_sha256
        if bound_digest != build_task_plan_sha256(plan):
            return ["当前执行绑定的任务计划已被替换"]
    tasks = list(plan.get("task_registry", {}).values())
    selected = set(resolve_execution_slice(
        build_task_plan=plan, tasks=tasks, build_execution_scope=scope,
    )["task_ids"])
    selected_tasks = [task for task in tasks if task["id"] in selected]
    # 依赖闭包可能带入其他接口，按精确合同/接口身份补读当前正式设计，禁止回填冻结旧设计。
    designs = {(item["apiContractId"], item["endpointId"]): item
               for item in context.get("endpoint_designs", [])}
    keys = {key for task in selected_tasks for key in task_endpoint_keys(task)}
    for contract_id, endpoint_id in sorted(keys - designs.keys()):
        for design in load_confirmed_endpoint_designs(
            state["workspace"], current, [endpoint_id], api_contract_id=contract_id,
        ):
            designs[(contract_id, endpoint_id)] = design
    context = {**context, "endpoint_designs": list(designs.values()),
               "endpoint_ids": sorted({endpoint for _, endpoint in designs})}
    config = read_application_config(state["workspace"])
    errors = []
    for task in selected_tasks:
        task_context = context
        endpoints = task_endpoint_keys(task)
        if endpoints:
            # 权限投影也按任务自己的合同裁剪，跨合同依赖不能继承当前开发目标的合同过滤器。
            contract_id = next(iter(endpoints))[0]
            task_context = {**context, "target": {"type": "endpoint", "api_contract_id": contract_id},
                            "endpoint_ids": sorted(endpoint for _, endpoint in endpoints)}
        task_context = compile_authorization_overlay(current, task_context, application_config=config)
        if not task_covers_current_input(task, current, task_context):
            errors.append(str(task["id"]))
    return errors
