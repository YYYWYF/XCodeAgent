"""T6.4 正式输入、真实 reuse/skeleton 与严格模型正文夹具。"""

from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_task_reuse_contracts import ExternalCapability
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.dag_planning_inputs import SequentialPlanningInputs
from app.services.frozen_contract_manifest import compile_expected_unit_formal_source_refs
from app.services.frozen_contract_store import FrozenContractStore, PlanningFormalInputs
from app.services.unit_generation_requirement_targets import scoped_formal_targets
from app.services.unit_generation_requirements import resolve_generation_requirements
from app.services.unit_generation_requirements_contracts import GenerationRequirementsError
from tests.dag_planning_baseline_fixtures import build_context, execution_scope, project_plan, task, workspace_snapshot


def _formal_contract_inputs(plan: dict) -> PlanningFormalInputs:
    """把测试 TechnicalPlan 拆成 T10 Store 所需的显式正式输入。"""

    technical_source = {"artifact": "technical-plan.json", "revision": plan.get("version", "confirmed")}
    page_sources = {
        item["pageId"]: {"artifact": "runtime-page-contracts", "page_id": item["pageId"]}
        for item in plan.get("page_implementation_contracts", [])
    }
    api_sources = {
        item["id"]: {"artifact": "technical-plan.json", "api_contract_id": item["id"]}
        for item in plan.get("api_contracts", [])
    }
    entity_sources = {
        item["entity_id"]: {"artifact": f"entities/{item['entity_id']}.json", "entity_id": item["entity_id"]}
        for item in plan.get("entity_detail_plans", [])
    }
    authorization_slices = []
    authorization_sources = {}
    manifest = plan.get("authorization_manifest")
    if isinstance(manifest, dict) and manifest.get("enabled") is True:
        bindings = manifest.get("bindings", {})
        for page in plan.get("page_implementation_contracts", []):
            page_id = page["pageId"]
            endpoint_ids = set(page.get("requiredEndpointIds", []))
            source = {"artifact": "authorization-manifest", "page_id": page_id}
            authorization_sources[page_id] = source
            authorization_slices.append({
                "content": {
                    "pageId": page_id,
                    "pages": [item for item in bindings.get("pages", []) if item.get("pageId") == page_id],
                    "actions": [item for item in bindings.get("actions", []) if item.get("pageId") == page_id],
                    "endpoints": [item for item in bindings.get("endpoints", []) if item.get("endpointId") in endpoint_ids],
                },
                "source": source,
            })
    inputs = PlanningFormalInputs(
        product_plan={
            "content": {
                "artifact_type": "product-plan",
                "confirmation_status": "confirmed",
                "pages": plan.get("pages", []),
            },
            "source": {"artifact": "product-plan.json", "revision": "confirmed"},
        },
        technical_plan={"content": plan, "source": technical_source},
        page_contracts=[
            {"content": item, "source": page_sources[item["pageId"]]}
            for item in plan.get("page_implementation_contracts", [])
        ],
        api_contracts=[
            {"content": item, "source": api_sources[item["id"]]}
            for item in plan.get("api_contracts", [])
        ],
        entity_bindings=[
            {"content": item, "source": entity_sources[item["entity_id"]]}
            for item in plan.get("entity_detail_plans", [])
        ],
        authorization_slices=authorization_slices,
    )
    return inputs


def _formal_source_refs(
    plan: dict,
    skeleton: dict,
    requirements,
    formal_inputs: PlanningFormalInputs,
    scope: dict,
) -> list[dict]:
    """用平台期望清单编译器生成测试生产者输入，负例再从完整清单中定点破坏。"""

    store = FrozenContractStore.create(
        planning_run_id="fixture-formal-source-manifest",
        formal_inputs=formal_inputs,
    )
    _, endpoints = scoped_formal_targets(plan, scope)
    result = []
    for unit_id, duties in requirements.generation_requirements_by_unit.items():
        result.extend(
            item.model_dump(mode="json")
            for item in compile_expected_unit_formal_source_refs(
                unit_id=unit_id,
                unit_kind=skeleton["build_units"][unit_id]["kind"],
                generation_requirements=duties,
                scoped_endpoint_keys=tuple(endpoints),
                frozen_contract_store=store,
            )
        )
    return result


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
    try:
        requirements = resolve_generation_requirements(
            required_unit_ids=context["required_unit_ids"],
            build_execution_scope=scope,
            unit_skeleton=skeleton,
            reuse_facts=facts,
            formal_target=plan,
        )
    except GenerationRequirementsError:
        # 负向夹具仍应在正式 orchestrator 前置门禁失败，而不是被测试辅助构造提前截断。
        requirements = None
    formal_contract_inputs = _formal_contract_inputs(plan)
    return SequentialPlanningInputs(
        project_plan=plan, base_confirmed_plan=baseline, skeleton_plan=skeleton, build_context=context,
        build_execution_scope=scope, workspace_snapshot=snapshot, reuse_facts=facts,
        formal_contract_inputs=formal_contract_inputs,
        formal_source_refs=(
            _formal_source_refs(
                plan,
                skeleton,
                requirements,
                formal_contract_inputs,
                scope,
            )
            if requirements is not None else []
        ),
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
