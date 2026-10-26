"""静态来源的短引用转换、精确目标校验和 Mock 契约核对。"""

from typing import Any

from pydantic import TypeAdapter

from app.domain.api_design import DraftFieldMapping, SourceMapping, UnconfiguredFieldMapping, ValueFieldMapping
from app.domain.api_design_static import StaticSourceField, static_fields
from app.domain.endpoint_design_assist import MappingCandidate, MappingModelResult
from app.domain.endpoint_design_assist_steps import ReturnStepResult, StepReturn
from app.domain.endpoint_design_static_assist import StaticGeneratedMapping
from app.services.api_design import _endpoint_field_snapshot, _validate_field_mappings
from app.services.endpoint_design_assist_steps import _request, _right
from app.services.endpoint_design_assist_validation import digest, field_key, _validate_generation_policy

_MAPPING = TypeAdapter(DraftFieldMapping)


def static_snapshot(reference: str, context: dict) -> dict:
    """只补齐当前已应用静态内容中的真实叶子字段快照。"""
    field = next((item for item in context["staticFields"] if f"static:{item['path']}" == reference), None)
    if field is None:
        raise ValueError(f"静态字段不存在：{reference}。")
    return dict(field)


def _return_value(item: StepReturn, context: dict) -> dict:
    """区分静态字段、接口参数、固定值与业务处理，不接受其他来源。"""
    target = next((field for field in context["endpointFields"] if field["side"] == "response" and field_key(field) == item.field), None)
    if target is None:
        raise ValueError(f"返回目标不属于当前契约：{item.field}。")
    result = {"endpointField": _endpoint_field_snapshot(target)}
    sources, parameters = [], []
    for reference in item.inputs:
        if reference.startswith("static:"):
            sources.append(static_snapshot(reference, context))
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


def validate_static_candidate(candidate: MappingCandidate, context: dict) -> dict:
    """核验当前目标、依赖方向、类型及列表层级，允许其他返回项未配置。"""
    if candidate.area != "return":
        raise ValueError("静态映射只支持应用返回字段。")
    parsed = _MAPPING.validate_python(candidate.value)
    target = next((field for field in context["endpointFields"] if field["side"] == "response" and field_key(field) == candidate.key), None)
    if not isinstance(parsed, (SourceMapping, ValueFieldMapping)) or target is None:
        raise ValueError("静态返回建议必须指定真实目标与取值。")
    if parsed.endpoint_field.model_dump(by_alias=True) != _endpoint_field_snapshot(target):
        raise ValueError("返回目标快照与当前契约不一致。")
    _validate_field_mappings([parsed], [field for field in context["endpointFields"] if field["side"] == "request"] + [target], allow_unconfigured_request=True)
    if isinstance(parsed, SourceMapping):
        for source in parsed.source_fields:
            if not isinstance(source, StaticSourceField) or source.model_dump(by_alias=True) != static_snapshot(f"static:{source.path}", context):
                raise ValueError("返回来源不属于当前静态内容。")
            if parsed.processing_type == "direct" and source.path.count("[]") != target["path"].count("[]"):
                raise ValueError("静态字段与返回字段的列表层级不一致。")
            if parsed.processing_type == "direct" and source.type == "number" and target["type"] == "integer":
                raise ValueError("静态小数不能直接返回为整数。")
    _validate_generation_policy(candidate.value)
    # 固定 null 的显式键必须保留，不能通过 exclude_none 丢失取值语义。
    value = parsed.model_dump(mode="json", by_alias=True, exclude_none=True)
    if isinstance(parsed, ValueFieldMapping) and parsed.right.kind == "fixed":
        value["right"]["value"] = parsed.right.value
    return value


def expand_static_step(result: ReturnStepResult, context: dict) -> MappingModelResult:
    """转换短引用，先校验真实目标，再把缺少依据的规则留空提问。"""
    candidates, questions, seen = [], list(result.questions), set()
    for item in result.returns:
        if item.field in seen:
            raise ValueError("同一返回目标不能重复生成。")
        seen.add(item.field)
        candidate = MappingCandidate(area="return", key=item.field, value=_return_value(item, context),
            reason=item.basis.strip() or f"直接引用 {'、'.join(item.inputs)}，请核对业务含义。")
        candidate.value = validate_static_candidate(candidate, context)
        if ("value" in item.model_fields_set or item.rule.strip()) and not item.basis.strip():
            questions.append(f"{item.field} 缺少契约或需求依据，请补充取值规则。")
        else:
            candidates.append(candidate)
    return MappingModelResult(candidates=candidates, questions=list(dict.fromkeys(questions))[:50])


def complete_static_mapping(candidates: list[MappingCandidate], context: dict) -> dict:
    """保留当前静态内容，从空配置覆盖全部契约字段而不继承旧规则。"""
    mappings = {field_key(field): {"endpointField": _endpoint_field_snapshot(field), "mappingType": "unconfigured"}
                for field in context["endpointFields"]}
    seen = set()
    for candidate in candidates:
        if candidate.key in seen:
            raise ValueError("同一返回目标不能重复生成。")
        seen.add(candidate.key)
        mappings[candidate.key] = validate_static_candidate(candidate, context)
    return {"staticData": context["staticData"], "fieldMappings": list(mappings.values())}


def validate_static_mapping(mapping: StaticGeneratedMapping, context: dict) -> dict:
    """应用时禁止借映射改写数据，并严格核验目标全集和未配置快照。"""
    if digest(mapping.static_data) != digest(context["staticData"]):
        raise ValueError("返回映射不能修改静态内容，请通过数据内容入口修改。")
    expected = {field_key(field): _endpoint_field_snapshot(field) for field in context["endpointFields"]}
    candidates, seen = [], set()
    for value in mapping.field_mappings:
        parsed = _MAPPING.validate_python(value)
        key = field_key(parsed.endpoint_field.model_dump(by_alias=True))
        if key in seen or expected.get(key) != parsed.endpoint_field.model_dump(by_alias=True):
            raise ValueError("返回目标重复、越界或契约快照已变化。")
        seen.add(key)
        if not isinstance(parsed, UnconfiguredFieldMapping):
            candidates.append(MappingCandidate(area="return", key=key, value=value, reason="整体替换"))
    if seen != set(expected):
        raise ValueError("生成结果必须覆盖当前全部契约字段。")
    return complete_static_mapping(candidates, context)


def validate_mock_data(data: Any, context: dict, row_count: int | None = None) -> list[dict]:
    """Mock 按返回契约的完整路径生成，核对类型、同构、安全大小及每组列表数量。"""
    fields = static_fields(data)
    expected = {field["path"]: field for field in context["endpointFields"] if field["side"] == "response"}
    actual = {field["path"]: field for field in fields}
    if not expected or set(expected) != set(actual):
        raise ValueError("Mock 数据字段必须完整匹配当前返回契约，不能新增或遗漏路径。")
    for path, field in expected.items():
        kind = actual[path]["type"]
        if kind != field["type"] and not (field["type"] == "number" and kind == "integer"):
            raise ValueError(f"Mock 字段类型与返回契约不符：{path}。")

    def check_rows(value: Any) -> None:
        """每个列表都按用户选择的条数生成，不在校验后静默裁剪内容。"""
        if isinstance(value, list):
            if len(value) > 20:
                raise ValueError("Mock 每组列表最多包含 20 条数据。")
            if row_count is not None and len(value) != row_count:
                raise ValueError(f"Mock 每组列表必须包含 {row_count} 条数据。")
            for child in value:
                check_rows(child)
        elif isinstance(value, dict):
            for child in value.values():
                check_rows(child)

    check_rows(data)
    return fields
