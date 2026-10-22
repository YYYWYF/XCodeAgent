"""外部 API 生成配置的身份回填、局部校验与整套替换校验。"""

from typing import Any

from pydantic import TypeAdapter

from app.domain.api_design import (
    DraftFieldMapping, ExternalApiBinding, ExternalApiBindingDraft, ExternalSourceField,
    SourceMapping, UnconfiguredFieldMapping, ValueFieldMapping,
)
from app.domain.endpoint_design_assist import MappingCandidate, MappingModelResult
from app.domain.endpoint_design_assist_steps import DescriptionStepResult, ReturnStepResult, StepReturn
from app.domain.endpoint_design_external_assist import ExternalGeneratedMapping, ExternalRequestStepResult
from app.services.api_design import _endpoint_field_snapshot, _validate_external_api_bindings, _validate_field_mappings
from app.services.endpoint_design_assist_steps import _request, _right
from app.services.endpoint_design_assist_validation import field_key, _validate_generation_policy

_MAPPING = TypeAdapter(DraftFieldMapping)


def external_key(field: dict) -> str:
    """完整区段和路径共同标识外部字段，避免请求响应重名混用。"""
    return f"external:{field['section']}:{field['path']}"


def external_snapshot(reference: str, context: dict, *, response: bool) -> dict:
    """只从服务端当前 Operation 结构补齐允许方向的字段快照。"""
    field = next((item for item in context["externalFields"] if external_key(item) == reference
                  and (item["section"] == "response_body") == response), None)
    if field is None:
        raise ValueError(f"外部字段不存在或方向错误：{reference}。")
    return ExternalSourceField(**context["selection"], **{key: field[key] for key in
        ("section", "path", "type", "description")}).model_dump(by_alias=True)


def _return_value(item: StepReturn, context: dict) -> dict:
    """将外部响应和应用请求依赖转换为当前返回映射，不猜测来源身份。"""
    target = next((field for field in context["endpointFields"]
                   if field["side"] == "response" and field_key(field) == item.field), None)
    if target is None:
        raise ValueError(f"应用返回目标不存在：{item.field}。")
    result = {"endpointField": _endpoint_field_snapshot(target)}
    sources, parameters = [], []
    for reference in item.inputs:
        if reference.startswith("external:"):
            sources.append(external_snapshot(reference, context, response=True))
        else:
            parameters.append(_request(reference, context))
    if not sources:
        return {**result, "mappingType": "value_mapping", "right": _right(item, context)}
    if not item.rule.strip():
        if len(sources) != 1 or parameters:
            raise ValueError("多输入返回必须提供处理规则。")
        return {**result, "mappingType": "source_mapping", "sourceFields": sources, "processingType": "direct"}
    return {**result, "mappingType": "source_mapping", "sourceFields": sources, "endpointFields": parameters,
            "processingType": "multi_field_description" if len(sources) > 1 else "single_field_description",
            "businessDescription": item.rule.strip(), "missingBehavior": "error"}


def validate_external_candidate(candidate: MappingCandidate, context: dict) -> Any:
    """逐目标核验方向、权威快照、类型与隐藏策略，允许无关字段尚未配置。"""
    area, key, value = candidate.area, candidate.key, candidate.value
    if area == "description":
        if key != "implementationDescription" or not isinstance(value, str) or len(value) > 4000:
            raise ValueError("映射说明格式无效。")
        return value.strip()
    if area == "request":
        parsed = ExternalApiBinding.model_validate(value)
        if parsed.external_field.model_dump(by_alias=True) != external_snapshot(key, context, response=False):
            raise ValueError("外部请求目标身份或结构已变化。")
        _validate_external_api_bindings([parsed], context["endpointFields"])
    elif area == "return":
        parsed = _MAPPING.validate_python(value)
        if not isinstance(parsed, (SourceMapping, ValueFieldMapping)):
            raise ValueError("返回建议必须提供明确取值方式。")
        target = next((field for field in context["endpointFields"] if field["side"] == "response" and field_key(field) == key), None)
        if target is None or parsed.endpoint_field.model_dump(by_alias=True) != _endpoint_field_snapshot(target):
            raise ValueError("返回目标身份或契约快照无效。")
        subset = [field for field in context["endpointFields"] if field["side"] == "request"] + [target]
        _validate_field_mappings([parsed], subset, allow_unconfigured_request=True)
        if isinstance(parsed, SourceMapping):
            for source in parsed.source_fields:
                payload = source.model_dump(by_alias=True)
                if not isinstance(source, ExternalSourceField) or payload != external_snapshot(external_key(payload), context, response=True):
                    raise ValueError("返回来源不属于当前外部响应结构。")
    else:
        raise ValueError("外部 API 映射不支持数据库操作或查询写入配置。")
    _validate_generation_policy(value)
    return parsed.model_dump(mode="json", by_alias=True, exclude_none=True)


def expand_external_step(result: Any, context: dict) -> MappingModelResult:
    """转换短引用并校验；缺少业务依据的固定值和规则保留未配置。"""
    if isinstance(result, DescriptionStepResult):
        return MappingModelResult(candidates=[MappingCandidate(area="description", key="implementationDescription",
            value=result.description, reason="概括本轮已生成映射")], questions=result.questions)
    request_step = isinstance(result, ExternalRequestStepResult)
    if not request_step and not isinstance(result, ReturnStepResult):
        raise ValueError("外部映射步骤输出无效。")
    candidates, questions, seen = [], list(result.questions), set()
    for item in result.requests if request_step else result.returns:
        if item.field in seen:
            raise ValueError(f"重复映射目标：{item.field}。")
        seen.add(item.field)
        value = {"externalField": external_snapshot(item.field, context, response=False), "right": _right(item, context)} if request_step else _return_value(item, context)
        candidate = MappingCandidate(area="request" if request_step else "return", key=item.field, value=value,
            reason=item.basis.strip() or f"直接引用 {'、'.join(item.inputs)}，请核对业务含义。")
        candidate.value = validate_external_candidate(candidate, context)
        if ("value" in item.model_fields_set or item.rule.strip()) and not item.basis.strip():
            questions.append(f"{item.field} 的取值缺少契约或需求依据，请补充规则。")
        else:
            candidates.append(candidate)
    return MappingModelResult(candidates=candidates, questions=list(dict.fromkeys(questions))[:50])


def complete_external_mapping(candidates: list[MappingCandidate], context: dict) -> dict:
    """从空配置构造所有目标，不沿用旧草稿中的任何取值或规则。"""
    requests = {external_key(field): {"externalField": external_snapshot(external_key(field), context, response=False)}
                for field in context["externalFields"] if field["section"] != "response_body"}
    returns = {field_key(field): {"endpointField": _endpoint_field_snapshot(field), "mappingType": "unconfigured"}
               for field in context["endpointFields"]}
    description, seen = "", set()
    for candidate in candidates:
        identity = (candidate.area, candidate.key)
        if identity in seen:
            raise ValueError("同一目标不能存在多个生成结果。")
        seen.add(identity)
        value = validate_external_candidate(candidate, context)
        if candidate.area == "request":
            requests[candidate.key] = value
        elif candidate.area == "return":
            returns[candidate.key] = value
        else:
            description = value
    return ExternalGeneratedMapping(externalApiBindings=list(requests.values()), fieldMappings=list(returns.values()),
                                    implementationDescription=description).model_dump(by_alias=True, exclude_none=True)


def validate_external_mapping(mapping: ExternalGeneratedMapping, context: dict) -> dict:
    """应用前验证全量目标集合和未配置快照，拒绝删除、添加或伪造目标。"""
    candidates = [MappingCandidate(area="description", key="implementationDescription", value=mapping.implementation_description, reason="整体替换")]
    expected = {external_key(field) for field in context["externalFields"] if field["section"] != "response_body"}
    seen = set()
    for value in mapping.external_api_bindings:
        parsed = ExternalApiBindingDraft.model_validate(value)
        key = external_key(parsed.external_field.model_dump(by_alias=True))
        if key in seen or parsed.external_field.model_dump(by_alias=True) != external_snapshot(key, context, response=False):
            raise ValueError("外部请求目标重复或结构已变化。")
        seen.add(key)
        if parsed.right is not None:
            candidates.append(MappingCandidate(area="request", key=key, value=value, reason="整体替换"))
    if seen != expected:
        raise ValueError("生成结果必须覆盖当前外部请求目标。")
    expected_fields = {field_key(field): _endpoint_field_snapshot(field) for field in context["endpointFields"]}
    seen = set()
    for value in mapping.field_mappings:
        parsed = _MAPPING.validate_python(value)
        key = field_key(parsed.endpoint_field.model_dump(by_alias=True))
        if key in seen or expected_fields.get(key) != parsed.endpoint_field.model_dump(by_alias=True):
            raise ValueError("应用接口字段重复、越界或契约已变化。")
        seen.add(key)
        if not isinstance(parsed, UnconfiguredFieldMapping):
            candidates.append(MappingCandidate(area="return", key=key, value=value, reason="整体替换"))
    if seen != set(expected_fields):
        raise ValueError("生成结果必须覆盖当前全部契约字段。")
    return complete_external_mapping(candidates, context)
