"""单一外部 API 的分步生成、规则完善与无落盘草稿替换。"""

import asyncio
from typing import Any

from app.agents.endpoint_design_assist import invoke_mapping_assistant
from app.domain.endpoint_design_assist import MappingModelResult, RefinedRule
from app.domain.endpoint_design_external_assist import ExternalGenerateRequest, ExternalReplaceMappingRequest, ExternalRefineRequest
from app.protocols.ag_ui_action_stream import AgUiActionProgress, ProgressReporter
from app.services.api_design import load_external_operation, _endpoint_field_snapshot
from app.services.endpoint_design_detail import EndpointDesignPrepareRequest, prepare_endpoint_design
from app.services.endpoint_design_assist_validation import digest, field_key
from app.services.endpoint_design_assist_steps import mapping_reviews
from app.services.endpoint_design_assist_retry import MappingGenerationFailure, generation_identity, save_checkpoint, take_checkpoint
from app.services.endpoint_design_external_assist_validation import (
    external_key, external_snapshot, complete_external_mapping, validate_external_mapping,
)
from app.services.api_design_values import value_rule_summary


def load_external_assist_context(request: Any) -> dict:
    """读取正式应用契约与公开外部结构，排除连接配置、凭据和样例。"""
    if len(str(request.draft)) > 1024 * 1024:
        raise ValueError("映射草稿超过 AI 辅助大小限制。")
    preparation = prepare_endpoint_design(EndpointDesignPrepareRequest(workspaceRoot=request.workspace_root,
        apiContractId=request.api_contract_id, endpointId=request.endpoint_id))
    if preparation["technicalPlanHash"] != request.technical_plan_hash or (preparation.get("artifactRevision") or None) != request.base_revision:
        raise ValueError("接口契约或正式映射已变化，请同步后重新生成。")
    draft, selection = request.draft, request.selection.model_dump(by_alias=True)
    if draft.get("apiContractId") != request.api_contract_id or draft.get("endpointId") != request.endpoint_id or draft.get("sourceBinding") != selection:
        raise ValueError("草稿或所选外部接口不属于当前目标。")
    if draft.get("databaseOperation") is not None or draft.get("databaseQuery") is not None or draft.get("databaseWrites") or draft.get("staticData") is not None:
        raise ValueError("外部 API 辅助不支持混合数据库或静态来源。")
    metadata = load_external_operation(request.workspace_root, request.selection.source_id,
        request.selection.directory_id, request.selection.operation_id)
    # 白名单投影，不将公开结构之外的连接信息或请求响应样例传给模型。
    operation = {key: metadata["operation"].get(key, "") for key in ("name", "method", "path", "description")}
    fields = [{key: field.get(key, "" if key != "required" else False) for key in
               ("section", "path", "type", "required", "description")} for field in metadata["fields"]]
    identity = {"selection": selection, "operation": operation, "fields": fields}
    return {"endpoint": preparation["payload"]["endpoint"], "endpointFields": preparation["payload"]["endpointFields"],
            "selection": selection, "externalOperation": operation, "externalFields": fields, "draft": draft,
            "baseline": {"draftHash": digest(draft), "metadataHash": digest(identity)}}


def external_summary(mapping: dict, questions: list[str], completed: list[str]) -> str:
    """向后续步骤投影本轮已校验配置，避免传递旧草稿或连接信息。"""
    lines = []
    if "request" in completed:
        lines.extend(f"外部入参 {external_key(item['externalField'])} ← " +
                     (value_rule_summary(item["right"]) if item.get("right") else "未配置") for item in mapping["externalApiBindings"])
    if "return" in completed:
        for item in mapping["fieldMappings"]:
            if item["endpointField"]["side"] != "response":
                continue
            value = "未配置"
            if item["mappingType"] == "source_mapping":
                value = "、".join([external_key(source) for source in item["sourceFields"]] +
                                 [field_key(field) for field in item.get("endpointFields", [])])
                value += "；" + (item.get("businessDescription") or "直接返回")
            elif item.get("right"):
                value = value_rule_summary(item["right"])
            lines.append(f"返回 {field_key(item['endpointField'])} ← {value}")
    lines.extend(f"待明确（不是已知事实）：{question}" for question in questions)
    return "\n".join(lines)


async def generate_external_mapping(request: ExternalGenerateRequest, report: ProgressReporter, thread_id: str | None = None) -> dict:
    """入参、返回、说明自动串行生成，完成后一次性交付完整预览。"""
    await report(AgUiActionProgress("context", "正在读取应用契约和外部 API 结构", 10))
    context = await asyncio.to_thread(load_external_assist_context, request)
    model_context = {key: context[key] for key in ("endpoint", "endpointFields", "selection", "externalOperation", "externalFields")}
    model_context.update(instruction=request.instruction, clarification=request.clarification)
    identity = generation_identity(request, context["baseline"])
    candidates, questions, start = [], [], 0
    if request.retry_token:
        checkpoint = take_checkpoint(request.retry_token, identity)
        candidates, questions, start = checkpoint.candidates, checkpoint.questions, checkpoint.step_index
    steps = ["request", "return", "description"]
    labels = {"request": "外部入参映射", "return": "应用返回字段", "description": "映射说明"}
    mapping = complete_external_mapping(candidates, context)
    for index, step in enumerate(steps):
        if index < start:
            continue
        await report(AgUiActionProgress(step, f"正在生成{labels[step]}（{index + 1}/3）", 25 + index * 25))
        step_context = {**model_context, "generatedSummary": external_summary(mapping, questions, steps[:index])}
        try:
            raw = await invoke_mapping_assistant(step_context, step=step, thread_id=thread_id)
            result = MappingModelResult.model_validate(raw)
            if any(candidate.area != step for candidate in result.candidates):
                raise ValueError("AI 返回了当前步骤之外的配置。")
            next_candidates = [*candidates, *result.candidates]
            next_mapping = complete_external_mapping(next_candidates, context)
        except Exception as exc:
            token = save_checkpoint(identity, index, candidates, questions)
            raise MappingGenerationFailure(f"{labels[step]}生成失败：{str(exc)[:2000]}", token=token,
                                           stage=step, completed=steps[:index]) from exc
        candidates, mapping = next_candidates, next_mapping
        questions.extend(result.questions)
    await report(AgUiActionProgress("validating", "正在核对完整外部 API 映射", 95))
    questions.extend(f"外部入参 {external_key(item['externalField'])} 尚未配置，请补充取值或按需移除可选参数。"
                     for item in mapping["externalApiBindings"] if not item.get("right"))
    questions.extend(f"返回字段 {item['endpointField']['path']} 尚未配置，请补充其取值规则。"
                     for item in mapping["fieldMappings"] if item["mappingType"] == "unconfigured" and item["endpointField"]["side"] == "response")
    return {"baseline": context["baseline"], "mapping": mapping, "operation": None, "operationReason": "使用当前绑定的外部 API。",
            "questions": list(dict.fromkeys(questions)), "reviews": mapping_reviews(candidates)}


def replace_external_mapping(request: ExternalReplaceMappingRequest) -> dict:
    """再次读取权威结构与基线，成功后仅返回可撤销的完整内存草稿。"""
    context = load_external_assist_context(request)
    if context["baseline"] != request.baseline.model_dump(by_alias=True):
        raise ValueError("草稿或外部 API 结构已变化，请重新生成。")
    mapping = validate_external_mapping(request.mapping, context)
    merged = {**request.draft, **mapping}
    return {"draft": merged, "areas": ["request", "return", "description"],
            "targets": [{"area": "request", "key": external_key(item["externalField"])} for item in mapping["externalApiBindings"]] +
                       [{"area": "return", "key": field_key(item["endpointField"])} for item in mapping["fieldMappings"] if item["endpointField"]["side"] == "response"]}


async def refine_external_rule(request: ExternalRefineRequest, report: ProgressReporter, thread_id: str | None = None) -> dict:
    """核对原规则目标和已选依赖，只返回可采用到编辑框的文字候选。"""
    await report(AgUiActionProgress("context", "正在核对外部 API 规则输入", 10))
    context = await asyncio.to_thread(load_external_assist_context, request)
    target = request.target
    if target.area == "request":
        field = external_snapshot(target.key, context, response=False)
    else:
        field = next((item for item in context["endpointFields"] if item["side"] == "response" and field_key(item) == target.key), None)
    if field is None:
        raise ValueError("规则目标已失效。")
    dependencies, seen = [], set()
    for dependency in request.dependencies:
        value = dependency.field
        if dependency.kind == "endpoint":
            actual = next((item for item in context["endpointFields"] if item["side"] == "request" and field_key(item) == field_key(value)), None)
            if actual is None or _endpoint_field_snapshot(actual) != value:
                raise ValueError("规则输入不属于当前应用请求参数。")
        elif dependency.kind == "source":
            if target.area != "return" or value.get("sourceType") != "external_api" or "section" not in value or "path" not in value:
                raise ValueError("外部请求规则不能使用外部响应或其他来源。")
            if external_snapshot(external_key(value), context, response=True) != value:
                raise ValueError("规则来源身份或响应结构已变化。")
        elif value not in ({"key": "current_user_id"}, {"key": "current_time"}):
            raise ValueError("规则内置依赖无效。")
        identity = digest(dependency.model_dump(by_alias=True))
        if identity in seen:
            raise ValueError("规则依赖不能重复。")
        seen.add(identity)
        dependencies.append(dependency.model_dump(by_alias=True))
    await report(AgUiActionProgress("generating", "正在完善处理规则", 40))
    raw = await invoke_mapping_assistant({"selection": context["selection"], "target": {**target.model_dump(), "type": field["type"]},
        "dependencies": dependencies, "description": request.description}, step="rule", thread_id=thread_id)
    return RefinedRule.model_validate(raw).model_dump(by_alias=True)
