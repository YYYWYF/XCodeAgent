"""按任务交付物直接消费的正式合同判断覆盖是否仍有效，不修改正式基线。"""

from collections.abc import Mapping

from app.services.page_task_freshness import page_task_covers_current_input
from app.services.planning_frozen import plain_json
from app.services.runtime_business_contract import backend_generation_constraints
from app.services.runtime_agent_module_policy import module_source_refs


def _business_inputs(task, project_plan):
    """只比较确定性业务输入，忽略路径、诊断、确认时间和其他任务的 owner 投影。"""
    result = []
    for check in plain_json(task).get("business_acceptance_checks", ()):
        expected = dict(check.get("expected") or {})
        # 同模块其他 Endpoint 的实现归属不是本任务消费的接口合同。
        expected.pop("allowed_existing_endpoints", None)
        expected.pop("support_paths", None)
        sources = check.get("sources", [])
        if str(check.get("kind", "")).startswith("python."):
            from app.services.runtime_business_acceptance import endpoint_entity_ids
            keys = {(item.get("api_contract_id"), item.get("endpoint_id"))
                    for item in expected.get("endpoints", [])}
            contracts = [api for api in project_plan.get("api_contracts", [])
                         if any((api["id"], endpoint["id"]) in keys for endpoint in api.get("endpoints", []))]
            entity_ids = endpoint_entity_ids(contracts)
            expected["entities"] = [entity for entity in expected.get("entities", [])
                                    if entity.get("entity_id") in entity_ids]
            sources = [source for source in sources
                       if source.get("artifact") != "runtime_entity" or source.get("target_id") in entity_ids]
        # 与验收比较同一业务来源摘要，真正变化的合同不能继续声明可复用。
        result.append({"kind": check.get("kind"), "expected": expected, "sources": sources})
    return result


def _page_endpoint_inputs(task, project_plan, build_context):
    """只比较本页声明读取的接口设计正文，不纳入其他接口或确认元数据。"""
    page_id = task["unit_id"].removeprefix("page:")
    contract = next((item for item in project_plan.get("page_implementation_contracts", ())
                     if item.get("pageId") == page_id), {})
    required = set(contract.get("requiredEndpointIds", ()))

    def content(designs):
        """规范化所引用接口的输入正文，排除与业务输入无关的签名时间及全局依赖摘要。"""
        from app.services.api_design_fingerprint import api_design_content
        return {f'{item["apiContractId"]}:{item["endpointId"]}': api_design_content(item)
                for item in plain_json(designs) if item.get("endpointId") in required}

    return content(task.get("source_refs", {}).get("endpoint_designs", ())) == content(
        build_context.get("endpoint_designs", ()))


def task_covers_current_input(task: Mapping, project_plan: Mapping, build_context: Mapping, *, check_page=True) -> bool:
    """重编译同一交付物的直接输入并比较原冻结合同；不因无关上游变更递归失效。"""
    return task_input_change_reason(task, project_plan, build_context, check_page=check_page) is None


def task_input_change_reason(task: Mapping, project_plan: Mapping, build_context: Mapping, *, check_page=True) -> str | None:
    """复用判断和用户原因共用同一次正式输入比较，不让展示自行猜测失效原因。"""
    if task.get("owner") == "agent":
        expected = module_source_refs(project_plan, task, build_context.get("runtime_agent_template_policy") or {})
        if plain_json(task.get("source_refs") or {}) != expected:
            return "智能体模块配置或依赖已变化"
        return None if any(check.get("kind") == "agent.module_contract" for check in task.get("business_acceptance_checks") or []) else "缺少智能体模块验收合同"
    if task.get("owner") == "backend":
        expected = backend_generation_constraints(project_plan)
        stored = task.get("source_refs") or {}
        if any(stored.get(key) != expected.get(key) for key in ("backend_implementation", "backend_root")):
            return "后端实现架构或工程根已变化"
    if check_page and not page_task_covers_current_input(task, project_plan):
        return "页面实现契约已变化"
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
            return "页面引用的接口映射已变化"
    # 无业务交付物的脚手架/权限能力仍使用原平台能力证据，不增加新的准入校验。
    kinds = {item.get("kind") for item in current.get("deliverables", ())}
    if not kinds.intersection({"frontend.api_module", "frontend.static_data_module", "frontend.page",
                              "backend.objects", "backend.repository", "backend.upstream",
                              "backend.application_service", "backend.endpoint_controller"}):
        return None
    current = apply_unit_compilation({}, [current], context)[0]
    current = compile_business_acceptance([current], context)[0]
    previous, latest = _business_inputs(task, project_plan), _business_inputs(current, project_plan)
    if previous == latest:
        return None
    # 来源身份与摘要直接定位变化，不解析错误字符串或依赖模型解释。
    def sources(checks):
        """按正式来源身份建立摘要索引，忽略无语义的列表顺序。"""
        return {(source["artifact"], source["target_id"], source["pointer"]): source["sha256"]
                for check in checks for source in check["sources"]}
    old_sources, new_sources = sources(previous), sources(latest)
    changed = {key[0] for key in old_sources.keys() | new_sources.keys()
               if old_sources.get(key) != new_sources.get(key)}
    labels = {"api_contract": "接口契约", "api_design": "字段映射", "runtime_entity": "实体定义",
              "runtime_topology": "运行架构", "page_implementation_contract": "页面契约"}
    return "、".join(labels.get(kind, kind) for kind in sorted(changed)) + "已变化" if changed else "业务验收输入已变化"
