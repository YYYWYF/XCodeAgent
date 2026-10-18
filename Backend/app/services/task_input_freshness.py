"""按任务交付物直接消费的正式合同判断覆盖是否仍有效，不修改正式基线。"""

from collections.abc import Mapping
from hashlib import sha256
import json

from app.services.page_task_freshness import page_task_covers_current_input
from app.services.planning_frozen import plain_json


def _business_inputs(task):
    """只比较确定性业务输入，忽略路径、诊断、确认时间和其他任务的 owner 投影。"""
    result = []
    for check in plain_json(task).get("business_acceptance_checks", ()):
        expected = dict(check.get("expected") or {})
        # 同模块其他 Endpoint 的实现归属不是本任务消费的接口合同。
        expected.pop("allowed_existing_endpoints", None)
        result.append({"kind": check.get("kind"), "expected": expected})
    return sha256(json.dumps(result, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":")).encode()).hexdigest()


def _page_endpoint_inputs(task, project_plan, build_context):
    """只比较本页声明读取的接口设计正文，不纳入其他接口或确认元数据。"""
    page_id = task["unit_id"].removeprefix("page:")
    contract = next((item for item in project_plan.get("page_implementation_contracts", ())
                     if item.get("pageId") == page_id), {})
    required = set(contract.get("requiredEndpointIds", ()))

    def content(designs):
        """规范化所引用接口的输入正文，排除与业务输入无关的签名时间及全局依赖摘要。"""
        return {f'{item["apiContractId"]}:{item["endpointId"]}': {
            key: value for key, value in item.items()
            if key not in {"artifactRevision", "confirmedAt", "basedOn", "status", "confirmationStatus"}
        } for item in plain_json(designs) if item.get("endpointId") in required}

    return content(task.get("source_refs", {}).get("endpoint_designs", ())) == content(
        build_context.get("endpoint_designs", ()))


def task_covers_current_input(task: Mapping, project_plan: Mapping, build_context: Mapping, *, check_page=True) -> bool:
    """重编译同一交付物的直接输入并比较原冻结合同；不因无关上游变更递归失效。"""
    if check_page and not page_task_covers_current_input(task, project_plan):
        return False
    # 复用原平台合同编译器选择精确 Endpoint、Schema、字段映射和页面依赖，不能用新 Unit 背书。
    from app.services.business_acceptance import compile_business_acceptance
    from app.services.build_unit_compiler import apply_unit_compilation

    current = plain_json(task)
    context = {**plain_json(build_context), "project_plan": plain_json(project_plan)}
    # 同一共享 API Unit 可保留其他 Scope 的接口任务；按任务自身的接口清单取输入。
    endpoint_ids = {endpoint["endpoint_id"]
                    for check in current.get("business_acceptance_checks", ())
                    for endpoint in (check.get("expected") or {}).get("endpoints", ())
                    if endpoint.get("endpoint_id")}
    if endpoint_ids:
        context["endpoint_ids"] = sorted(endpoint_ids)
    unit_id = task["unit_id"]
    if unit_id.startswith("page:"):
        # 页面编译沿用原上下文；单独比较本页直接读取的接口，不能把应用范围改成页面范围制造差异。
        if not _page_endpoint_inputs(task, project_plan, build_context):
            return False
    # 无业务交付物的脚手架/权限能力仍使用原平台能力证据，不增加新的准入校验。
    kinds = {item.get("kind") for item in current.get("deliverables", ())}
    if not kinds.intersection({"frontend.api_module", "frontend.static_data_module", "frontend.page",
                              "backend.objects", "backend.repository", "backend.upstream",
                              "backend.application_service", "backend.endpoint_controller"}):
        return True
    current = apply_unit_compilation({}, [current], context)[0]
    current = compile_business_acceptance([current], context)[0]
    return _business_inputs(task) == _business_inputs(current)
