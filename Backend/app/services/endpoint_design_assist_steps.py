"""把模型的短字段引用转换为平台当前 v7 配置，不信任模型补写身份。"""

from typing import Any

from app.domain.endpoint_design_assist import MappingCandidate, MappingModelResult
from app.domain.endpoint_design_assist_steps import (
    AssistStep, DescriptionStepResult, QueryStepResult, ReturnStepResult, StepCondition,
    StepQueryGroup, StepReturn, StepValue, WriteStepResult,
)
from app.services.api_design import _endpoint_field_snapshot
from app.services.api_design_values import value_rule_summary
from app.services.endpoint_design_assist_validation import field_key, merge_candidates


def generation_steps(operation: str) -> list[AssistStep]:
    """按固定 CRUD 顺序逐步生成，所有步骤完成后统一返回结果。"""
    if operation not in {"create", "read", "update", "delete"}:
        raise ValueError("不支持的数据库操作。")
    steps: list[AssistStep] = []
    if operation != "create":
        steps.append("query")
    if operation in {"create", "update"}:
        steps.append("write")
    steps.extend(["return", "description"])
    return steps


def _request(reference: str, context: dict[str, Any]) -> dict[str, Any]:
    """只解析当前请求参数的精确引用，禁止模型猜测字段快照。"""
    field = next((item for item in context["endpointFields"]
                  if item["side"] == "request" and field_key(item) == reference), None)
    if field is None:
        raise ValueError(f"输入引用不属于当前请求参数：{reference}。")
    return _endpoint_field_snapshot(field)


def _column(name: str, context: dict[str, Any]) -> dict[str, Any]:
    """由服务端表结构补齐来源身份、列类型及说明。"""
    column = next((item for item in context["columns"] if item["name"] == name), None)
    if column is None:
        raise ValueError(f"当前数据表不存在字段：{name}。")
    return {**context["selection"], "column": column["name"], "type": column["type"],
            "description": column.get("description", "")}


def _right(value: StepValue, context: dict[str, Any]) -> dict[str, Any]:
    """把请求引用或自然语言规则转换为原取值契约，保留显式固定空值。"""
    if "value" in value.model_fields_set:
        return {"kind": "fixed", "value": value.value}
    fields = [_request(item, context) for item in value.inputs]
    if value.rule.strip():
        return {"kind": "business", "origin": "business", "endpointFields": fields,
                "businessDescription": value.rule.strip(), "missingBehavior": "error"}
    if len(fields) != 1:
        raise ValueError("直接取值必须指定一个输入，或提供明确处理规则。")
    return {"kind": "endpoint", "endpointField": fields[0]}


def _condition(value: StepCondition, context: dict[str, Any]) -> dict[str, Any]:
    """补齐查询目标，并维持空值判断不携带右值的领域约束。"""
    result = {**_column(value.column, context), "kind": "condition", "operator": value.op}
    if value.op not in {"is_null", "is_not_null"}:
        result["right"] = _right(value, context)
    return result


def _return(value: StepReturn, context: dict[str, Any]) -> dict[str, Any]:
    """按实际依赖数量选择直接、单字段或多字段处理，并补齐返回快照。"""
    target = next((item for item in context["endpointFields"]
                   if item["side"] == "response" and field_key(item) == value.field), None)
    if target is None:
        raise ValueError(f"返回目标不属于当前接口：{value.field}。")
    result = {"endpointField": _endpoint_field_snapshot(target)}
    sources, parameters = [], []
    for reference in value.inputs:
        if reference.startswith("column:"):
            sources.append({**_column(reference[len("column:"):], context), "usage": "read"})
        else:
            parameters.append(_request(reference, context))
    if not sources:
        return {**result, "mappingType": "value_mapping", "right": _right(value, context)}
    result.update(mappingType="source_mapping", sourceFields=sources)
    if not value.rule.strip():
        if len(sources) != 1 or parameters:
            raise ValueError("多输入返回映射必须提供处理规则。")
        return {**result, "processingType": "direct"}
    return {**result, "processingType": "multi_field_description" if len(sources) > 1 else "single_field_description",
            "endpointFields": parameters, "businessDescription": value.rule.strip(), "missingBehavior": "error"}


def expand_step_result(result: Any, context: dict[str, Any]) -> list[MappingCandidate]:
    """将单步骤简洁输出转换成受限候选，后续仍走现有确定性校验。"""
    values: list[tuple[str, str, Any]] = []
    if isinstance(result, QueryStepResult) and result.query is not None:
        items = []
        for item in result.query.items:
            items.append({"kind": "group", "join": item.join,
                          "items": [_condition(child, context) for child in item.items]}
                         if isinstance(item, StepQueryGroup) else _condition(item, context))
        values.append(("query", "databaseQuery", {"join": result.query.join, "items": items}))
    elif isinstance(result, WriteStepResult):
        values.extend(("write", item.column, {**_column(item.column, context), "right": _right(item, context)})
                      for item in result.writes)
    elif isinstance(result, ReturnStepResult):
        values.extend(("return", item.field, _return(item, context)) for item in result.returns)
    elif isinstance(result, DescriptionStepResult):
        values.append(("description", "implementationDescription", result.description))
    reasons = {}
    if isinstance(result, (WriteStepResult, ReturnStepResult)):
        rows = result.writes if isinstance(result, WriteStepResult) else result.returns
        reasons = {item.column if isinstance(result, WriteStepResult) else item.field:
                   item.basis.strip() or f"直接引用 {'、'.join(item.inputs)}，请核对业务含义。" for item in rows}
    elif isinstance(result, QueryStepResult) and result.query:
        rows = [child for item in result.query.items for child in (item.items if isinstance(item, StepQueryGroup) else [item])]
        reasons["databaseQuery"] = "；".join(dict.fromkeys(item.basis.strip() or f"{item.column} {item.op}，引用 {'、'.join(item.inputs) or '空值判断'}" for item in rows))[:2000]
    return [MappingCandidate(area=area, key=key, value=value, reason=reasons.get(key, "根据本轮已验证映射概括"))
            for area, key, value in values]


def step_model_result(result: Any, context: dict[str, Any]) -> MappingModelResult:
    """固定值和业务规则没有依据时留空提问；查询整组处理，保持布尔语义。"""
    # 先验证模型实际提出的目标和依赖，不能借“缺少依据”静默吞掉跨来源或重复目标。
    # 模型转换只依赖权威字段与本轮操作；这里不需要、也不接收旧草稿。
    merge_candidates({"databaseOperation": context["databaseOperation"], "databaseWrites": [], "fieldMappings": []},
                     expand_step_result(result, context), context)
    questions = list(result.questions)

    def unsupported(item: StepValue) -> bool:
        """识别需要明确依据的取值或自增列写入，不把数据库默认值当业务输入。"""
        generated_column = isinstance(result, WriteStepResult) and any(
            column["name"] == item.column and column.get("autoIncrement") is True for column in context["columns"])
        return bool((item.rule.strip() or "value" in item.model_fields_set or generated_column) and not item.basis.strip())

    if isinstance(result, QueryStepResult) and result.query:
        rows = [child for item in result.query.items for child in (item.items if isinstance(item, StepQueryGroup) else [item])]
        if any(unsupported(item) for item in rows):
            questions.append("查询条件包含没有契约或用户要求依据的固定值或业务处理，请明确完整条件；本组尚未配置。")
            result = result.model_copy(update={"query": None})
    elif isinstance(result, (WriteStepResult, ReturnStepResult)):
        name = "writes" if isinstance(result, WriteStepResult) else "returns"
        rows = getattr(result, name)
        for item in rows:
            if unsupported(item):
                target = item.column if name == "writes" else item.field
                questions.append(f"{target} 的取值缺少契约或用户要求中的明确依据，尚未配置；请补充规则。")
        result = result.model_copy(update={name: [item for item in rows if not unsupported(item)]})
    questions = list(dict.fromkeys(questions))
    if len(questions) > 50:
        questions = [*questions[:49], f"另有 {len(questions) - 49} 项需要明确依据，请核对完整预览中的未配置字段。"]
    return MappingModelResult(candidates=expand_step_result(result, context), questions=questions)


def mapping_reviews(candidates: list[MappingCandidate]) -> list[dict[str, Any]]:
    """审阅依据只作为辅助结果返回，不混入正式映射或草稿存储。"""
    reviews = []
    for candidate in candidates:
        if candidate.area == "description":
            continue
        value = candidate.value or {}
        # 查询以整组审阅；字段处理和固定值需要用户重点核对其业务依据。
        needs_review = candidate.area == "query" or value.get("processingType", "direct") != "direct" or value.get("right", {}).get("kind") in {"fixed", "business"}
        reviews.append({"area": candidate.area, "key": candidate.key, "reason": candidate.reason, "needsReview": needs_review})
    return reviews


def _query_summary(query: dict[str, Any]) -> str:
    """保留查询括号和 AND/OR 关系，将已验证查询转成易读说明。"""
    parts = []
    for item in query["items"]:
        if item.get("kind") == "group":
            parts.append(f"({_query_summary(item)})")
        else:
            right = value_rule_summary(item["right"]) if item.get("right") else ""
            parts.append(f"{item['column']} {item['operator']} {right}".strip())
    return f" {query['join'].upper()} ".join(parts)


def generated_summary(mapping: dict[str, Any], questions: list[str], *, completed: list[str] | None = None) -> str:
    """向后续步骤提供本轮已完成区域的可读摘要，避免再传完整 JSON。"""
    areas = completed if completed is not None else ["query", "write", "return"]
    lines = ["查询条件：" + (_query_summary(mapping["databaseQuery"]) if mapping.get("databaseQuery") else "未设置")] if "query" in areas else []
    if "write" in areas:
        lines.extend(f"写入 {item['column']} ← {value_rule_summary(item['right'])}" for item in mapping["databaseWrites"])
    if "write" in areas and not mapping["databaseWrites"]:
        lines.append("写入字段：未设置")
    for item in mapping["fieldMappings"]:
        if "return" not in areas or item["endpointField"]["side"] != "response":
            continue
        target = field_key(item["endpointField"])
        if item["mappingType"] == "source_mapping":
            sources = [f"column:{source['column']}" for source in item["sourceFields"]]
            sources.extend(field_key(field) for field in item.get("endpointFields", []))
            rule = item.get("businessDescription") or "直接返回"
            lines.append(f"返回 {target} ← {', '.join(sources)}；{rule}")
        else:
            lines.append(f"返回 {target} ← " + (value_rule_summary(item["right"]) if item.get("right") else "未配置"))
    lines.extend(f"待明确：{question}" for question in questions)
    return "\n".join(lines)
