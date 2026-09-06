"""T6.4 正式输入、真实 reuse/skeleton 与严格模型正文夹具。"""

from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_task_reuse_contracts import ExternalCapability
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.dag_planning_inputs import SequentialPlanningInputs
from tests.dag_planning_baseline_fixtures import build_context, execution_scope, project_plan, task, workspace_snapshot


def planning_inputs(*, plan=None, baseline=None, required=None, scope=None, context=None):
    """构造真实骨架/reuse；纯页面应用用明确的 application BuildContext。"""

    if plan is None:
        plan = {
            "artifact_type": "technical-plan", "confirmation_status": "confirmed",
            "architecture": {"frontend": "React"}, "entities": [], "entity_detail_plans": [], "api_contracts": [],
            "pages": [{"pageId": key, "path": f"/{key}"} for key in ("a", "b", "c", "history")],
            "page_implementation_contracts": [{
                "schema_version": "page-implementation-contract.v1", "pageId": key,
                "uiDesignRef": {"path": f".xcodeagent/ui-design/pages/{key.title()}/index.tsx"},
                "requiredEndpointIds": [],
            } for key in ("a", "b", "c", "history")],
        }
    scope = scope or {"type": "application", "targetId": "application"}
    context = context or {"scope": scope, "template_variant": "main"}
    context = {**context, "required_unit_ids": required if required is not None else ["frontend:shell", "page:a", "page:b", "page:c"]}
    snapshot = workspace_snapshot()
    skeleton = ensure_build_unit_skeleton(plan, snapshot, baseline)
    facts = resolve_reuse_facts(confirmed_plan=baseline, unit_skeleton=skeleton,
                               build_context=context, workspace_snapshot=snapshot, formal_plan=plan)
    facts = facts.model_copy(update={"external_capabilities": (ExternalCapability(
        unit_id="frontend:shell", capability_id="frontend.shell.ready", source="template_generation_readiness",
        workspace_revision=snapshot["workspace_revision"], source_refs={"manifest_path": ".xcodeagent/template-generation-manifest.json"},
    ),)})
    return SequentialPlanningInputs(
        project_plan=plan, base_confirmed_plan=baseline, skeleton_plan=skeleton, build_context=context,
        build_execution_scope=scope, workspace_snapshot=snapshot, reuse_facts=facts,
    )


def shared_inputs(baseline):
    """提供 customers 增量 Scope，正式历史含 orders 的共享 adapter/API 及其他 Unit。"""

    plan = project_plan()
    scope = execution_scope(name="customers")
    return planning_inputs(plan=plan, baseline=baseline, required=["frontend:api-client"],
                           scope=scope, context=build_context(plan, scope))


def model_tasks(job):
    """每条正式缺项生成一个严格任务，职责身份和 owner 均来自本 Unit 冻结输入。"""

    tasks = []
    for index, requirement in enumerate(job.context.generation_requirements):
        refs = requirement.source_refs
        kind = refs["kind"]
        target = refs.get("page_id") if kind == "frontend.page" else (
            refs.get("endpoint_id") if kind in {"frontend.api_module", "frontend.static_data_module", "backend.endpoint_controller"}
            else refs.get("target_id") if kind == "frontend.shared_capability"
            else refs.get("data_source_type") if kind == "backend.bootstrap" else refs.get("entity_id")
        )
        path = f"frontend/src/pages/{target.title()}/index.tsx" if kind == "frontend.page" else (
            "backend/pom.xml" if kind == "backend.bootstrap"
            else f"backend/src/main/java/example/{index}Implementation.java" if kind.startswith("backend.")
            else f"frontend/src/apis/{index}Implementation.ts"
        )
        task_id = f"{job.identity.unit_id}-r{job.identity.generation_round}-a{job.identity.attempt_in_round}-{index}"
        value = task(task_id, job.identity.unit_id, kind, path, target)
        value.update({"title": "实现本 Unit 正式职责", "source_refs": dict(refs),
                      "task_type": "backend.code" if kind.startswith("backend.") else "frontend.code",
                      "status": "pending", "can_run_in_parallel": False, "parallel_reason": "串行测试",
                      "impact_scope": {"summary": "当前职责", "affected_modules": [], "public_contracts": [], "risks": []}})
        value["change_scope"][0]["description"] = "按正式职责修改"
        value["deliverables"][0]["provides"] = [requirement.requirement_id]
        tasks.append(value)
    return tasks
