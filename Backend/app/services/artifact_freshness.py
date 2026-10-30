"""读取当前正式产物的依赖事实，为正常开发与重试选择同一重生成入口。"""

from pathlib import Path
from typing import Any

from app.services.artifact_invalidation import canonical_sha256


def upstream_errors(root: Path, key: str, value: dict[str, Any]) -> list[str]:
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
        if field in value:
            current = load_project_plan_json(root / _ARTIFACTS[upstream])
            if value[field] != digest(current):
                errors.append(f"{upstream} 已变化")
    status = value.get("confirmation_status")
    if status is not None and status not in ({"confirmed", "skipped"} if key == "ui_designs" else {"confirmed"}):
        errors.append("当前版本尚未确认")
    return errors


def task_plan_errors(state: dict[str, Any], plan: dict[str, Any]) -> list[str]:
    """在恢复旧任务前重新编译直接输入，识别过期任务合同而非只检查 DAG 自身摘要。"""
    from app.services.build_context_resolver import resolve_confirmation_context
    from app.services.task_input_freshness import task_covers_current_input

    scope = state.get("build_execution_scope") or {}
    if scope.get("type") not in {"page", "endpoint", "agent"}:
        return []
    resolved = resolve_confirmation_context(state["workspace"], scope)
    context = resolved["build_context"]
    current = resolved["project_plan"]
    bound_digest = state.get("build_run_plan_sha256")
    if bound_digest:
        from app.workspace.task_documents import build_task_plan_sha256
        if bound_digest != build_task_plan_sha256(plan):
            return ["当前执行绑定的任务计划已被替换"]
    required = set(context.get("required_unit_ids", []))
    return [str(task["id"]) for task in plan.get("task_registry", {}).values()
            if task.get("unit_id") in required and not task_covers_current_input(task, current, context)]
