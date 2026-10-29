from __future__ import annotations

import json
from typing import Any, Callable

from app.agents.main.technical_plan_feedback import technical_plan_feedback_prompt
from app.utils.model_output import extract_json_object


def _technical_plan_chinese_prose_targets(
    plan: dict[str, Any],
) -> dict[str, tuple[dict[str, Any], str, str]]:
    """收集 TechnicalPlan 中面向用户展示的自然语言字段及其写入位置。"""

    targets: dict[str, tuple[dict[str, Any], str, str]] = {}

    def add_text(parent: dict[str, Any], key: str, path: str) -> None:
        """登记可供模型修正的非空自然语言字段。"""

        value = parent.get(key)
        if isinstance(value, str) and value.strip():
            targets[path] = (parent, key, value)

    def visit_schema(value: Any, path: str) -> None:
        """递归登记 Schema 内嵌套属性的说明文字。"""

        if isinstance(value, dict):
            add_text(value, "description", f"{path}.description")
            for key, child in value.items():
                if key != "description":
                    visit_schema(child, f"{path}.{key}")
        elif isinstance(value, list):
            for index, child in enumerate(value):
                visit_schema(child, f"{path}[{index}]")

    architecture = plan.get("architecture")
    if isinstance(architecture, dict):
        for key in ("frontend", "backend", "data"):
            add_text(architecture, key, f"architecture.{key}")

    for entity_index, entity in enumerate(plan.get("entities", [])):
        if not isinstance(entity, dict):
            continue
        entity_path = f"entities[{entity_index}]"
        add_text(entity, "description", f"{entity_path}.description")
        for field_index, field in enumerate(entity.get("fields", [])):
            if not isinstance(field, dict):
                continue
            field_path = f"{entity_path}.fields[{field_index}]"
            add_text(field, "label", f"{field_path}.label")
            add_text(field, "description", f"{field_path}.description")

    for contract_index, contract in enumerate(plan.get("api_contracts", [])):
        if not isinstance(contract, dict):
            continue
        contract_path = f"api_contracts[{contract_index}]"
        add_text(contract, "name", f"{contract_path}.name")
        for endpoint_index, endpoint in enumerate(contract.get("endpoints", [])):
            if not isinstance(endpoint, dict):
                continue
            endpoint_path = f"{contract_path}.endpoints[{endpoint_index}]"
            add_text(endpoint, "name", f"{endpoint_path}.name")
            add_text(endpoint, "summary", f"{endpoint_path}.summary")
        visit_schema(contract.get("schemas", {}), f"{contract_path}.schemas")

    for page_index, page in enumerate(plan.get("pages", [])):
        if not isinstance(page, dict):
            continue
        references = page.get("references")
        if not isinstance(references, dict):
            continue
        dependencies = references.get("endpoint_dependencies")
        if not isinstance(dependencies, list):
            continue
        for dependency_index, dependency in enumerate(dependencies):
            if isinstance(dependency, dict):
                add_text(
                    dependency,
                    "trigger",
                    f"pages[{page_index}].references.endpoint_dependencies[{dependency_index}].trigger",
                )
    return targets


def _has_chinese_text(value: str) -> bool:
    """判断自然语言值是否至少包含一个中文字符。"""

    return any("\u3400" <= character <= "\u9fff" for character in value)


def repair_technical_plan_chinese_prose(
    plan: dict[str, Any],
    *,
    invoke_model: Callable[[str], str],
    planning_context: dict[str, Any],
) -> dict[str, Any]:
    """发现整段英文说明时调用模型翻译，并仅写回对应的说明字段。"""

    targets = _technical_plan_chinese_prose_targets(plan)
    untranslated = {
        path: value
        for path, (_, _, value) in targets.items()
        if not _has_chinese_text(value)
    }
    if not untranslated:
        return plan

    response_text = invoke_model(
        "你负责修正 TechnicalPlan 的语言。把给定字段值翻译成简体中文，仅翻译自然语言说明；"
        "技术名词、ID、字段名、类型、HTTP 方法、路径和 Schema 引用等词形保留。"
        "返回 JSON：{\"translations\": {原路径: 中文字符串}}。必须为每个给定路径返回一个值，"
        "不得增加、遗漏路径或返回其他内容。每个翻译后的值必须包含中文字符，并保留原意。\n"
        f"待翻译字段：{json.dumps(untranslated, ensure_ascii=False)}\n"
        f"{technical_plan_feedback_prompt(planning_context)}"
    )
    response = extract_json_object(response_text)
    translations = response.get("translations") if isinstance(response, dict) else None
    if not isinstance(translations, dict) or set(translations) != set(untranslated):
        raise ValueError("TechnicalPlan 中文说明修正未完整返回待翻译字段。")

    for path, translated in translations.items():
        if not isinstance(translated, str) or not translated.strip() or not _has_chinese_text(translated):
            raise ValueError(f"TechnicalPlan 字段 {path} 的中文说明修正无效。")
        parent, key, _ = targets[path]
        parent[key] = translated.strip()
    return plan
