"""T0.1 正式 Contract 夹具；不模拟 replacement、自动 rename 或 pending 落盘。"""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory

from app.domain.api_design import EndpointApiDesign
from app.services.authorization_overlay import compile_authorization_overlay
from app.services.build_context_resolver import resolve_target_build_context
from app.services.build_task_planner import create_build_task_plan
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.workspace.endpoint_design_documents import (
    technical_plan_sha256,
    write_endpoint_design,
)


def project_plan(*, authorization: bool = False) -> dict:
    """构造不含 Entity binding、由 Endpoint Design 补齐来源的 TechnicalPlan。"""

    plan = {
        "artifact_type": "technical-plan",
        "version": "baseline-v1",
        "confirmation_status": "confirmed",
        "architecture": {"frontend": "React", "backend": "Spring Boot"},
        "pages": [],
        "page_implementation_contracts": [],
        "entities": [],
        "api_contracts": [],
    }
    for name, entity in (("orders", "Order"), ("customers", "Customer")):
        plan["pages"].append({
            "pageId": name, "path": f"/{name}",
            "references": {"permissions": ["admin" if authorization else "anonymous"]},
        })
        plan["page_implementation_contracts"].append({
            "schema_version": "page-implementation-contract.v1",
            "pageId": name,
            "uiDesignRef": {"path": f".xcodeagent/ui-design/pages/{entity}s/index.tsx"},
            "requiredEndpointIds": [f"{name}.list"],
            "actionBindings": [{"actionId": "list"}] if authorization else [],
        })
        plan["entities"].append({
            "id": entity, "name": entity,
            "fields": [{"name": "id", "type": "string", "required": True}],
        })
        plan["api_contracts"].append({
            "id": f"{name}-api", "entity_ids": [entity],
            "schemas": {entity: {
                "type": "object", "required": ["id"],
                "properties": {"id": {"type": "string"}},
            }},
            "endpoints": [{
                "id": f"{name}.list", "method": "GET", "path": f"/{name}",
                "response_schema_ref": f"#/schemas/{entity}", "parameters": [],
                "operation_semantics": {"operation_kind": "list"},
            }],
        })
    if authorization:
        plan["authorization_manifest"] = {
            "schema_version": "authorization-manifest.v2", "enabled": True,
            "bindings": {
                "pages": [{"pageId": name, "resourceKey": name} for name in ("orders", "customers")],
                "actions": [{
                    "pageId": name, "actionId": "list", "resourceKey": f"{name}_list",
                } for name in ("orders", "customers")],
                "endpoints": [{
                    "endpointId": f"{name}.list", "operationResourceKeys": [f"{name}_list"],
                } for name in ("orders", "customers")],
            },
            "resources": [item for name in ("orders", "customers") for item in (
                {"resourceKey": name, "type": "page", "targetResourceRef": f"page:{name}"},
                {"resourceKey": f"{name}_list", "type": "operation", "targetResourceRef": f"action:{name}:list"},
            )],
        }
    return plan


def workspace_snapshot() -> dict:
    """提供无宿主路径的固定工作区快照，避免指纹随测试运行变化。"""

    return {"schema_version": "workspace-snapshot.v1", "workspace_revision": "baseline-revision", "tech_stack": ["React", "Spring Boot"]}


def execution_scope(target_type: str = "page", name: str = "orders") -> dict:
    """构造当前 page 或 endpoint Build 接口使用的 scope。"""

    if target_type == "endpoint":
        return {"type": "endpoint", "targetId": f"{name}.list", "apiContractId": f"{name}-api"}
    return {"type": "page", "targetId": name}


def build_context(plan: dict, scope: dict) -> dict:
    """通过真实 Endpoint Design 文档、解析器和权限 Overlay 编译上下文。"""

    with TemporaryDirectory() as directory:
        workspace = Path(directory)
        plan_path = write_json(
            workspace,
            ".xcodeagent/plans/technical-plan.json",
            formal_artifacts(plan)["technical_plan"],
        )
        write_confirmed_endpoint_designs(workspace, plan)
        context = resolve_target_build_context(
            plan,
            target_type=scope["type"],
            target_id=scope["targetId"],
            api_contract_id=scope.get("apiContractId"),
            project_plan_path=plan_path,
        )
    return compile_authorization_overlay(plan, {
        **context, "project_plan": plan, "scope": scope,
        "template_variant": "auth" if plan.get("authorization_manifest") else "main",
    })


def write_confirmed_endpoint_designs(
    workspace: Path,
    plan: dict,
    *,
    source_type: str = "database",
) -> None:
    """为当前 TechnicalPlan 的全部 Endpoint 写入自包含 confirmed Design。"""

    technical_digest = technical_plan_sha256(workspace)
    confirmed_at = datetime(2026, 9, 10, tzinfo=UTC)
    for contract in plan.get("api_contracts", []):
        contract_id = str(contract.get("id") or "")
        for endpoint in contract.get("endpoints", []):
            endpoint_id = str(endpoint.get("id") or "")
            target_name = endpoint_id.split(".", 1)[0] or "endpoint"
            if source_type == "external_api":
                source_field = {
                    "sourceType": "external_api",
                    "sourceId": f"{target_name}-upstream",
                    "directoryId": target_name,
                    "operationId": endpoint_id,
                    "section": "response_body",
                    "path": "data.id",
                    "type": "string",
                }
            else:
                source_field = {
                    "sourceType": "database",
                    "sourceId": f"{target_name}-database",
                    "schema": "app",
                    "table": target_name,
                    "column": "id",
                    "type": "string",
                    "usage": "read",
                }
            design = EndpointApiDesign.model_validate({
                "schemaVersion": "endpoint-field-mapping.v3",
                "artifactType": "endpoint-field-mapping",
                "status": "confirmed",
                "confirmationStatus": "confirmed",
                "artifactRevision": "0123456789abcdef0123456789abcdef",
                "apiContractId": contract_id,
                "endpointId": endpoint_id,
                "endpointContract": endpoint,
                "fieldMappings": [{
                    "endpointField": {
                        "side": "response",
                        "location": "response_body",
                        "path": "id",
                        "type": "string",
                        "required": True,
                        "description": "",
                    },
                    "mappingType": "source_mapping",
                    "processingType": "direct",
                    "sourceFields": [source_field],
                }],
                "sourceSnapshots": [{
                    "sourceType": source_field["sourceType"],
                    "sourceId": source_field["sourceId"],
                    "name": "测试来源",
                    "details": {},
                }],
                "implementationDescription": "实现当前 Endpoint。",
                "basedOn": [{
                    "artifactKey": "technical-plan",
                    "sha256": technical_digest,
                }],
                "confirmedAt": confirmed_at,
            })
            write_endpoint_design(workspace, design)


def task(task_id: str, unit: str, kind: str, path: str, target: str, *, dependencies: tuple = ()) -> dict:
    """生成字段完整、ID 唯一的候选，避免依赖 legacy 自动修正。"""

    return {
        "id": task_id, "unit_id": unit,
        "owner": "backend" if unit.startswith("backend:") else "frontend",
        "description": f"实现 {target}", "dependencies": list(dependencies),
        "target_files": [path], "allowed_paths": [path],
        "change_scope": [{"operation": "modify", "path": path}],
        "deliverables": [{
            "id": f"{task_id}:deliverable", "kind": kind, "target_id": target,
            "paths": [path], "provides": [f"{target}.ready"],
        }],
    }


def candidate_tasks(context: dict) -> list[dict]:
    """按明确的业务角色创建候选；shell、模板和 auth-guard 不伪造模型任务。"""

    units = set(context["required_unit_ids"])
    name = context["endpoint_ids"][0].split(".")[0]
    endpoint_unit = f"backend:endpoint:{name}-api:{name}.list"
    tasks = []
    if "backend:bootstrap" in units:
        tasks.append(task("backend:bootstrap::config", "backend:bootstrap", "backend.bootstrap", "backend/pom.xml", "backend:bootstrap"))
    if endpoint_unit in units:
        tasks.append(task(f"{name}:controller", endpoint_unit, "backend.endpoint_controller", f"backend/src/main/java/example/{name.title()}Controller.java", f"{name}.list"))
    if "frontend:api-client" in units:
        tasks.extend([
            task("api:adapter", "frontend:api-client", "frontend.shared_capability", "frontend/src/apis/response.ts", "frontend:api-client"),
            task(f"{name}:api", "frontend:api-client", "frontend.api_module", f"frontend/src/apis/{name}.ts", f"{name}.list", dependencies=("api:adapter",)),
        ])
    if "frontend:data:static" in units:
        tasks.append(task(f"{name}:static", "frontend:data:static", "frontend.static_data_module", f"frontend/src/data/{name}.ts", f"{name}.list"))
    if f"page:{name}" in units:
        tasks.append(task(f"{name}:page", f"page:{name}", "frontend.page", f"frontend/src/pages/{name.title()}/index.tsx", name))
    return tasks


def compiled_plan(plan: dict, scope: dict, *, baseline: dict | None = None) -> dict:
    """走真实骨架、Context、acceptance 和 Task Graph 编译链路。"""

    snapshot = workspace_snapshot()
    context = build_context(plan, scope)
    skeleton = ensure_build_unit_skeleton(plan, snapshot, baseline)
    return create_build_task_plan(
        plan, agent_plan={"tasks": candidate_tasks(context)}, workspace_snapshot=snapshot,
        base_build_task_plan=skeleton, build_context=context, build_execution_scope=scope,
    )


def confirmed_baseline(plan: dict, scope: dict) -> dict:
    """建立显式已确认、已有部分成功执行证据的完整 DAG 输入。"""

    baseline = compiled_plan(plan, scope)
    if baseline["status"] != "ready":
        raise AssertionError(baseline["task_graph"]["validation"]["errors"])
    baseline["confirmation_status"] = "confirmed"
    baseline["confirmed_at"] = "2026-09-04T00:00:00+00:00"
    for task_id in ("api:adapter", "backend:bootstrap::config"):
        if task_id in baseline["task_registry"]:
            baseline["task_registry"][task_id]["status"] = "completed"
    return baseline


def write_json(root: Path, relative: str, payload: dict) -> Path:
    """仅向隔离的临时工作区写入测试用正式产物。"""

    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def formal_artifacts(plan: dict) -> dict:
    """提供门禁最小正式产物集合，不把运行时派生字段写入 TechnicalPlan。"""

    technical = deepcopy(plan)
    technical.pop("page_implementation_contracts")
    return {
        "requirement_spec": {"confirmation_status": "confirmed"},
        "product_plan": {"confirmation_status": "confirmed"},
        "ui_designs": {"confirmation_status": "skipped"},
        "technical_plan": technical,
    }
