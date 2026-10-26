"""静态返回映射、规则完善及独立 Mock 数据的无落盘辅助服务。"""

import asyncio

from app.agents.endpoint_design_assist import invoke_mapping_assistant
from app.domain.api_design_static import static_fields, validate_static_design
from app.domain.endpoint_design_assist import MappingModelResult, RefinedRule
from app.domain.endpoint_design_static_assist import (
    ApplyMockRequest, GenerateMockRequest, MockStepResult, StaticAssistRequest,
    StaticGenerateRequest, StaticRefineRequest, StaticReplaceRequest,
)
from app.protocols.ag_ui_action_stream import AgUiActionProgress, ProgressReporter
from app.services.api_design import _endpoint_field_snapshot
from app.services.endpoint_design_detail import EndpointDesignPrepareRequest, prepare_endpoint_design
from app.services.endpoint_design_assist_retry import MappingGenerationFailure, generation_identity, save_checkpoint, take_checkpoint
from app.services.endpoint_design_assist_steps import mapping_reviews
from app.services.endpoint_design_assist_validation import digest, field_key
from app.services.endpoint_design_static_assist_validation import (
    complete_static_mapping, static_snapshot, validate_mock_data, validate_static_mapping,
)


def load_static_assist_context(request: StaticAssistRequest, *, require_data: bool = True) -> dict:
    """读取正式契约并核验当前静态身份；Mock 允许当前数据尚未配置。"""
    if len(str(request.draft)) > 1024 * 1024:
        raise ValueError("映射草稿超过 AI 辅助大小限制。")
    preparation = prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=request.workspace_root,
        apiContractId=request.api_contract_id, endpointId=request.endpoint_id))
    if preparation["technicalPlanHash"] != request.technical_plan_hash or (preparation.get("artifactRevision") or None) != request.base_revision:
        raise ValueError("接口契约或正式映射已变化，请同步后重新生成。")
    draft, selection = request.draft, request.selection.model_dump(by_alias=True)
    if draft.get("apiContractId") != request.api_contract_id or draft.get("endpointId") != request.endpoint_id or draft.get("sourceBinding") != selection:
        raise ValueError("草稿或静态来源不属于当前目标。")
    if any(draft.get(key) for key in ("databaseOperation", "databaseQuery", "databaseWrites", "externalApiBindings", "implementationDescription", "sourceSnapshots")):
        raise ValueError("静态辅助不支持混合来源、数据库操作或接口映射说明。")
    data = draft.get("staticData")
    fields = static_fields(data) if require_data else []
    return {"endpoint": preparation["payload"]["endpoint"], "endpointFields": preparation["payload"]["endpointFields"],
            "selection": selection, "staticData": data, "staticFields": fields, "draft": draft,
            "baseline": {"draftHash": digest(draft), "metadataHash": digest({"selection": selection, "staticData": data})}}


async def generate_static_mapping(request: StaticGenerateRequest, report: ProgressReporter, thread_id: str | None = None) -> dict:
    """仅生成返回映射，样例内容独立生成，不把已有静态值或旧规则传给模型。"""
    await report(AgUiActionProgress("context", "正在读取接口契约和静态数据结构", 10))
    context = await asyncio.to_thread(load_static_assist_context, request)
    identity = generation_identity(request, context["baseline"])
    if request.retry_token:
        take_checkpoint(request.retry_token, identity)
    model_context = {key: context[key] for key in ("endpoint", "endpointFields", "selection", "staticFields")}
    model_context.update(instruction=request.instruction, clarification=request.clarification)
    await report(AgUiActionProgress("return", "正在生成静态返回映射", 40))
    try:
        raw = await invoke_mapping_assistant(model_context, step="return", thread_id=thread_id)
        result = MappingModelResult.model_validate(raw)
        mapping = complete_static_mapping(result.candidates, context)
    except Exception as exc:
        token = save_checkpoint(identity, 0, [], [])
        raise MappingGenerationFailure(f"静态返回映射生成失败：{str(exc)[:2000]}", token=token, stage="return", completed=[]) from exc
    await report(AgUiActionProgress("validating", "正在核对静态字段和返回契约", 90))
    questions = [*result.questions, *(f"返回字段 {item['endpointField']['path']} 尚未配置，请补充其取值规则。"
        for item in mapping["fieldMappings"] if item["mappingType"] == "unconfigured" and item["endpointField"]["side"] == "response")]
    return {"baseline": context["baseline"], "mapping": mapping, "operation": None, "operationReason": "使用当前已应用的静态数据。",
            "questions": list(dict.fromkeys(questions)), "reviews": mapping_reviews(result.candidates)}


def replace_static_mapping(request: StaticReplaceRequest) -> dict:
    """重新核对内容、契约和草稿基线，一次返回完整内存配置。"""
    context = load_static_assist_context(request)
    if context["baseline"] != request.baseline.model_dump(by_alias=True):
        raise ValueError("草稿或静态内容已变化，请重新生成映射。")
    mapping = validate_static_mapping(request.mapping, context)
    return {"draft": {**request.draft, **mapping}, "areas": ["return"],
            "targets": [{"area": "return", "key": field_key(item["endpointField"])} for item in mapping["fieldMappings"] if item["endpointField"]["side"] == "response"]}


async def refine_static_rule(request: StaticRefineRequest, report: ProgressReporter, thread_id: str | None = None) -> dict:
    """仅完善当前返回目标及已选真实依赖的文字，不修改配置。"""
    await report(AgUiActionProgress("context", "正在核对静态返回规则输入", 10))
    context = await asyncio.to_thread(load_static_assist_context, request)
    target = next((field for field in context["endpointFields"] if field["side"] == "response" and field_key(field) == request.target.key), None)
    if target is None:
        raise ValueError("规则目标已失效。")
    seen = set()
    for dependency in request.dependencies:
        value = dependency.field
        if dependency.kind == "source":
            if value.get("sourceType") != "static" or static_snapshot(f"static:{value.get('path', '')}", context) != value:
                raise ValueError("规则来源不属于当前静态结构。")
        elif dependency.kind == "endpoint":
            actual = next((field for field in context["endpointFields"] if field["side"] == "request" and field_key(field) == field_key(value)), None)
            if actual is None or _endpoint_field_snapshot(actual) != value:
                raise ValueError("规则输入不属于当前请求参数。")
        elif value not in ({"key": "current_user_id"}, {"key": "current_time"}):
            raise ValueError("规则内置依赖无效。")
        identity = digest(dependency.model_dump(by_alias=True))
        if identity in seen:
            raise ValueError("规则依赖不能重复。")
        seen.add(identity)
    await report(AgUiActionProgress("generating", "正在完善静态返回处理规则", 40))
    raw = await invoke_mapping_assistant({"selection": context["selection"], "target": {**request.target.model_dump(), "type": target["type"]},
        "dependencies": [item.model_dump(by_alias=True) for item in request.dependencies], "description": request.description}, step="rule", thread_id=thread_id)
    return RefinedRule.model_validate(raw).model_dump(by_alias=True)


async def generate_mock_data(request: GenerateMockRequest, report: ProgressReporter, thread_id: str | None = None) -> dict:
    """按返回契约生成虚构样例，独立预览，不附带或替换字段映射。"""
    await report(AgUiActionProgress("context", "正在读取 Mock 数据的返回契约", 10))
    context = await asyncio.to_thread(load_static_assist_context, request, require_data=False)
    if not any(field["side"] == "response" for field in context["endpointFields"]):
        raise ValueError("当前接口没有返回字段，无法根据契约生成 Mock 数据。")
    model_context = {key: context[key] for key in ("endpoint", "endpointFields", "selection")}
    model_context.update(instruction=request.instruction, clarification=request.clarification, rowCount=request.row_count)
    await report(AgUiActionProgress("mock", "正在生成 Mock 样例数据", 40))
    result = MockStepResult.model_validate(await invoke_mapping_assistant(model_context, step="mock", thread_id=thread_id))
    await report(AgUiActionProgress("validating", "正在校验 Mock 结构和数据大小", 90))
    fields = validate_mock_data(result.data, context, request.row_count) if result.data is not None else []
    warnings = []
    if result.data is not None:
        try:
            validate_static_design({**request.draft, "staticData": result.data})
        except ValueError as exc:
            warnings.append(f"应用后请重新核对已有映射：{exc}")
    return {"baseline": context["baseline"], "data": result.data, "fields": fields,
            "questions": result.questions, "warnings": warnings}


def apply_mock_data(request: ApplyMockRequest) -> dict:
    """只替换静态数据，保留已有映射供用户审阅；正式写入仍由原确认动作完成。"""
    context = load_static_assist_context(request, require_data=False)
    if context["baseline"] != request.baseline.model_dump(by_alias=True):
        raise ValueError("草稿或接口契约已变化，请重新生成 Mock 数据。")
    validate_mock_data(request.data, context)
    return {"draft": {**request.draft, "staticData": request.data}, "areas": [], "targets": []}
