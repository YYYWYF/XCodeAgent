"""独立字段映射建议、规则完善与无落盘应用服务。"""

import asyncio
from typing import Any

from app.agents.endpoint_design_assist import invoke_mapping_assistant
from app.domain.api_design import DatabaseSourceField
from app.domain.endpoint_design_assist import (
    ReplaceMappingRequest, AssistRequest, MappingModelResult, RefineRuleRequest,
    RefinedRule, GenerateMappingRequest,
)
from app.domain.endpoint_design_assist_steps import OperationStepResult
from app.protocols.ag_ui_action_stream import AgUiActionProgress, ProgressReporter
from app.services.api_design import _endpoint_field_snapshot, load_database_columns
from app.services.data_sources import selected_tables
from app.services.endpoint_design_detail import EndpointDesignPrepareRequest, prepare_endpoint_design
from app.services.endpoint_design_assist_validation import (
    digest, field_key, complete_mapping, validate_generated_mapping, incomplete_mapping_questions,
)
from app.services.endpoint_design_assist_steps import generation_steps, generated_summary, mapping_reviews
from app.services.endpoint_design_assist_retry import (
    MappingGenerationFailure, generation_identity, save_checkpoint, take_checkpoint,
)


def load_assist_context(request: AssistRequest) -> dict[str, Any]:
    """读取当前契约与所选表公开结构，确认版本而不保存草稿。"""
    if len(str(request.draft)) > 1024 * 1024:
        raise ValueError("映射草稿超过 AI 辅助大小限制。")
    preparation = prepare_endpoint_design(EndpointDesignPrepareRequest(
        workspaceRoot=request.workspace_root, apiContractId=request.api_contract_id, endpointId=request.endpoint_id,
    ))
    if preparation["technicalPlanHash"] != request.technical_plan_hash or (preparation.get("artifactRevision") or None) != request.base_revision:
        raise ValueError("接口契约或正式映射已变化，请同步后重新生成建议。")
    draft, selection = request.draft, request.selection.model_dump(by_alias=True)
    if draft.get("apiContractId") != request.api_contract_id or draft.get("endpointId") != request.endpoint_id:
        raise ValueError("草稿不属于当前 Endpoint。")
    if draft.get("sourceBinding") != selection or draft.get("databaseOperation") not in {None, "create", "read", "update", "delete"}:
        raise ValueError("AI 辅助只支持当前绑定的单表数据库操作。")
    if draft.get("externalApiBindings") or draft.get("staticData") is not None:
        raise ValueError("AI 辅助不支持混合或静态来源。")
    if not any(all(item.get(key) == selection[key] for key in ("sourceId", "schema", "table")) for item in selected_tables(request.workspace_root)):
        raise ValueError("当前表不在应用已添加清单中。")
    metadata = load_database_columns(request.workspace_root, request.selection.source_id, request.selection.table)
    if metadata.get("schema") != request.selection.schema_name or not metadata.get("columns"):
        raise ValueError("当前表结构不可用。")
    columns = [{key: item[key] for key in ("name", "type", "description", "nullable", "primaryKey", "autoIncrement", "defaultValue") if key in item}
               for item in metadata["columns"]]
    fields = preparation["payload"]["endpointFields"]
    # 不把客户端描述作为模型依据；字段快照来自正式 Endpoint 与实时表元数据。
    return {"endpoint": preparation["payload"]["endpoint"], "endpointFields": fields,
            "columns": columns, "selection": selection, "draft": draft, "databaseOperation": draft.get("databaseOperation"),
            "baseline": {"draftHash": digest(draft), "metadataHash": digest({"selection": selection, "columns": columns})}}


async def generate_mapping(request: GenerateMappingRequest, report: ProgressReporter, thread_id: str | None = None) -> dict[str, Any]:
    """按区域自动串行生成，逐步校验，完成后一次返回整套映射。"""
    await report(AgUiActionProgress("context", "正在读取当前契约和数据表结构", 10))
    context = await asyncio.to_thread(load_assist_context, request)
    # 草稿仅用于平台的身份和过期校验，绝不进入映射生成提示词。
    model_context = {key: context[key] for key in ("endpoint", "endpointFields", "columns", "selection")}
    model_context["instruction"] = request.instruction
    model_context["clarification"] = request.clarification
    labels = {"query": "查询条件", "write": "写入字段", "return": "返回字段", "description": "映射说明"}
    candidates, questions = [], []
    identity = generation_identity(request, context["baseline"])
    start = 0
    decision = None
    if request.retry_token:
        checkpoint = take_checkpoint(request.retry_token, identity)
        candidates, questions, start = checkpoint.candidates, checkpoint.questions, checkpoint.step_index
        decision = checkpoint.operation
    if decision is None:
        await report(AgUiActionProgress("operation", "正在识别数据库操作", 15))
        try:
            # 操作识别不接收字段清单；完整元数据继续用于基线校验和后续映射。
            operation_context = {key: model_context[key] for key in ("endpoint", "selection", "instruction", "clarification")}
            raw = await invoke_mapping_assistant(operation_context, step="operation", thread_id=thread_id)
            decision = OperationStepResult.model_validate(raw)
        except Exception as exc:
            token = save_checkpoint(identity, 0, [], [])
            raise MappingGenerationFailure(f"数据库操作识别失败：{str(exc)[:2000]}", token=token,
                                           stage="operation", completed=[]) from exc
    if decision.operation is None:
        # 操作有歧义时正常结束本次生成，只交付问题，不能生成或应用半套配置。
        return {"baseline": context["baseline"], "mapping": None, "operation": None,
                "operationReason": decision.reason, "questions": decision.questions, "reviews": []}
    operation = decision.operation
    context = {**context, "databaseOperation": operation}
    model_context["databaseOperation"] = operation
    steps = generation_steps(operation)
    mapping = complete_mapping([], context)
    if candidates:
        mapping = complete_mapping(candidates, context)
    for index, step in enumerate(steps):
        if index + 1 < start:
            continue
        await report(AgUiActionProgress(step, f"正在生成{labels[step]}（{index + 2}/{len(steps) + 1}）", 30 + index * 60 // len(steps)))
        step_context = dict(model_context)
        if index > 0:
            # 后续阶段只读取本轮已通过校验的前序摘要，不能把旧草稿或未决问题当作事实。
            step_context["generatedSummary"] = generated_summary(mapping, questions, completed=steps[:index])
        try:
            raw = await invoke_mapping_assistant(step_context, step=step, thread_id=thread_id)
            model = MappingModelResult.model_validate(raw)
            if any(candidate.area != step for candidate in model.candidates):
                raise ValueError("AI 返回了当前生成步骤之外的配置。")
            # 整个步骤组合校验通过后才提交中间结果，失败重试绝不复用失败候选。
            next_candidates = [*candidates, *model.candidates]
            next_mapping = complete_mapping(next_candidates, context)
        except Exception as exc:
            token = save_checkpoint(identity, index + 1, candidates, questions, decision)
            raise MappingGenerationFailure(f"{labels[step]}生成失败：{str(exc)[:2000]}", token=token,
                                           stage=step, completed=["operation", *steps[:index]]) from exc
        candidates, mapping = next_candidates, next_mapping
        questions.extend(model.questions)
    await report(AgUiActionProgress("validating", "正在核对完整映射", 95))
    questions = list(dict.fromkeys([*questions, *incomplete_mapping_questions(mapping, operation)]))
    return {"baseline": context["baseline"], "mapping": mapping, "operation": operation,
            "operationReason": decision.reason, "questions": questions, "reviews": mapping_reviews(candidates)}


def replace_mapping(request: ReplaceMappingRequest) -> dict[str, Any]:
    """整体替换所有映射配置，基线冲突时不修改草稿、不落盘。"""
    context = load_assist_context(request)
    if request.baseline.model_dump(by_alias=True) != context["baseline"]:
        raise ValueError("草稿或表结构已变化，请重新生成建议。")
    mapping = validate_generated_mapping(request.mapping, context)
    merged = {**request.draft, **mapping}
    if merged["databaseQuery"] is None:
        merged.pop("databaseQuery")
    operation = mapping["databaseOperation"]
    areas = ["description", "return"] + (["query"] if operation != "create" else []) + (["write"] if operation in {"create", "update"} else [])
    return {"draft": merged, "areas": areas,
            "targets": [{"area": "return", "key": field_key(item["endpointField"])} for item in mapping["fieldMappings"] if item["endpointField"]["side"] == "response"]}


def rule_context(request: RefineRuleRequest, context: dict[str, Any]) -> dict[str, Any]:
    """验证规则目标和每项输入身份，保留用户已选依赖不增删。"""
    target = request.target
    operation = request.draft.get("databaseOperation")
    if operation is None:
        raise ValueError("请先明确数据库操作，再完善业务规则。")
    if target.area == "query" and operation == "create" or target.area == "write" and operation in {"read", "delete"}:
        raise ValueError("当前操作不支持该规则场景。")
    if target.area == "return":
        field = next((item for item in context["endpointFields"] if item["side"] == "response" and field_key(item) == target.key), None)
    else:
        field = next((item for item in context["columns"] if item["name"] == target.key), None)
    if field is None:
        raise ValueError("业务规则目标已失效。")
    dependencies, seen = [], set()
    for item in request.dependencies:
        value = item.field
        if item.kind == "endpoint":
            actual = next((node for node in context["endpointFields"] if node["side"] == "request" and field_key(node) == field_key(value)), None)
            if actual is None or _endpoint_field_snapshot(actual) != value:
                raise ValueError("规则输入不属于当前接口参数。")
        elif item.kind == "source":
            if target.area != "return":
                raise ValueError("查询或写入规则不能依赖尚未读取的表字段。")
            actual = next((node for node in context["columns"] if node["name"] == value.get("column")), None)
            if actual is None:
                raise ValueError("规则输入列不存在。")
            expected = DatabaseSourceField(**context["selection"], column=actual["name"], type=actual["type"], description=actual["description"])
            if expected.model_dump(by_alias=True) != DatabaseSourceField.model_validate(value).model_dump(by_alias=True):
                raise ValueError("规则输入列身份或结构已变化。")
        elif value not in ({"key": "current_user_id"}, {"key": "current_time"}):
            raise ValueError("规则内置依赖无效。")
        identity = digest({"kind": item.kind, "field": value})
        if identity in seen:
            raise ValueError("业务规则输入重复。")
        seen.add(identity)
        dependencies.append(item.model_dump(by_alias=True))
    return {"target": {**target.model_dump(), "type": field["type"]}, "dependencies": dependencies, "description": request.description}


async def refine_rule(request: RefineRuleRequest, report: ProgressReporter, thread_id: str | None = None) -> dict[str, Any]:
    """只完善说明文字；依赖和隐藏策略始终由原规则编辑器持有。"""
    await report(AgUiActionProgress("context", "正在核对业务规则输入", 10))
    context = await asyncio.to_thread(load_assist_context, request)
    values = rule_context(request, context)
    await report(AgUiActionProgress("generating", "正在完善处理规则", 40))
    raw = await invoke_mapping_assistant(values, step="rule", thread_id=thread_id)
    return RefinedRule.model_validate(raw).model_dump(by_alias=True)
