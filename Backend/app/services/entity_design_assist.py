"""实体设计 AI 辅助：仅当用户显式请求时生成结构化设计建议。

建议以 ``{id, label, value?, payload?, note}`` 的通用形状返回，前端在表单内
逐条采纳或忽略；模型失败时降级为空建议 + 错误说明，不影响确定性设计流程。
``table_selection`` 会结合当前表清单建议目标表并可选附带字段绑定建议。
"""

from __future__ import annotations

import json
from typing import Any

from app.agents.model_factory import create_chat_model
from app.agents.messages import _coerce_content_text
from app.config import Settings
from app.services.entity_definitions import (
    MYSQL_TYPE_BY_FIELD_TYPE,
    entity_mysql_target_table,
    normalize_entity,
)
from app.services.database_schema_summary import inspect_mysql_table
from app.utils.model_output import extract_json_object


SUPPORTED_ASSIST_TYPES = {
    "bindings",
    "table_selection",
    "api_mapping",
    "seed_data",
    "business_rules",
    "relationships",
    "acceptance",
    "risks",
}

_MAX_CONTEXT_TABLES = 40

_ASSIST_TARGET_SCHEMAS: dict[str, str] = {
    "bindings": (
        '{"entity_field": "实体字段名", "table_column": "目标表列名", '
        '"rule": "same_name|manual", "note": "可选说明"}'
    ),
    "table_selection": (
        '{"table_name": "推荐的目标表名（无合适表时省略）", '
        '"create_table": {"note": "无合适表时说明为何需要新建"}, '
        '"bindings": [{"entity_field": "实体字段名", "table_column": "表列名", '
        '"note": "可选说明"}], "note": "可选说明"}'
    ),
    "api_mapping": (
        '{"entity_field": "实体字段名", "source_field": "返回体字段路径", '
        '"rule": "same_name|manual", "note": "可选说明"}'
    ),
    "seed_data": '{"seed_row": {"字段名": "示例值"}, "note": "可选说明"}',
    "business_rules": (
        '{"rule_type": "unique|required|custom", "name": "规则名", '
        '"description": "规则说明"}'
    ),
    "relationships": (
        '{"relation_type": "one_to_one|one_to_many|many_to_many", '
        '"target_entity_id": "目标实体", "description": "关系说明"}'
    ),
    "acceptance": '{"text": "验收标准文本"}',
    "risks": '{"text": "风险或待确认事项"}',
}


def _assist_prompt(
    entity: dict[str, Any],
    *,
    assist_type: str,
    instruction: str,
    context: dict[str, Any],
) -> str:
    """构造 AI 辅助提示词：给出实体字段、目标结构与用户指令。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    fields = [
        {
            "name": field.get("name"),
            "label": field.get("label") or field.get("name"),
            "type": field.get("type") or "text",
            "required": bool(field.get("required")),
        }
        for field in normalized.get("fields", [])
        if field.get("name")
    ]
    schema = _ASSIST_TARGET_SCHEMAS.get(assist_type, "{}")
    max_items = 1 if assist_type == "table_selection" else 20
    guidance = ""
    if assist_type == "table_selection":
        guidance = (
            "只输出 1 项你认为最接近的方案。如果存在语义匹配的库表，"
            "给出 table_name 及其 bindings；如果不存在合适表，则省略 "
            "table_name，改为输出 create_table 并在 note 中说明原因，"
            "同时必须输出 bindings：把实体字段绑定到『建议目标表结构』中的列名"
            "（通常列名与实体字段同名，可按语义选择其它列），"
            "不要输出该结构之外的列名。"
            "用户上下文里的 available_tables 每项已包含真实列名 columns；"
            "bindings 的 table_column 必须从所选表的 columns 或建议目标表结构中选，"
            "不要臆造列名；表中没有对应列的字段不要绑定。"
            "missing_fields 列出需要为该表新增列的实体字段：仅当该字段没有通过 "
            "bindings 覆盖、且语义上确实需要补充时才列出；已通过语义映射覆盖的 "
            "字段（如 remark 绑定到 description）不要列出。\n"
        )
    elif assist_type == "bindings":
        guidance = (
            "missing_fields 列出需要为该表新增列的实体字段：仅当该字段没有绑定到 "
            "已有列、且语义上确实需要补充时才列出；绑定列名与字段名不同但语义一致时"
            "视为已覆盖，不要列出。\n"
        )
    elif assist_type == "api_mapping":
        guidance = (
            "source_field 必须是用户上下文 response_body 中真实存在的字段路径："
            "顶层字段直接写键名（如 name），嵌套字段用点路径（如 data.items[].name），"
            "数组元素用空下标 [] 表示；找不到对应字段时省略该实体字段的映射或给出空 "
            "source_field 并在 note 中说明，不要臆造返回体中不存在的字段名。\n"
        )
    elif assist_type == "seed_data":
        guidance = (
            "生成种子数据时，已定义取值约束的字段（上下文 field_values 中列出的字段）"
            "取值必须取自该字段的允许取值集合；未约束字段按实体业务语义生成合理示例值。\n"
        )
    history = context.get("messages") if isinstance(context.get("messages"), list) else []
    history_lines = []
    for item in history[-10:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip()
        content = str(item.get("content") or "").strip()
        if role and content:
            history_lines.append(f"{role}: {content}")
    return (
        "你是应用设计助手。请根据实体定义与用户提供的上下文，生成"
        f"{assist_type} 类型的实体设计建议。\n"
        f"{guidance}"
        f"实体名称：{normalized.get('name') or ''}\n"
        f"实体字段：{json.dumps(fields, ensure_ascii=False)}\n"
        f"用户上下文：{json.dumps(context, ensure_ascii=False)}\n"
        f"对话历史：\n{json.dumps(history_lines, ensure_ascii=False)}\n"
        f"本轮用户指令：{instruction or '（无，按最佳实践生成）'}\n"
        f"请只输出 JSON：{{\"text\": \"给用户的回复\", "
        f"\"missing_fields\": [{{\"entity_field\": \"需要新增列的实体字段名\"}}], "
        f"\"suggestions\": [每项按以下结构 {schema}]}}，"
        f"最多 {max_items} 项，不要输出解释文字。"
    )


def _normalize_suggestions(
    assist_type: str,
    items: Any,
) -> list[dict[str, Any]]:
    """把模型建议规整为前端可采纳的通用形状。"""

    normalized: list[dict[str, Any]] = []
    if not isinstance(items, list):
        return normalized
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        note = str(item.get("note") or "").strip()
        if assist_type in {"acceptance", "risks"}:
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            normalized.append(
                {
                    "id": f"{assist_type}-{index}",
                    "label": text,
                    "value": text,
                    "note": note,
                }
            )
            continue
        if assist_type == "seed_data":
            seed_row = item.get("seed_row")
            if not isinstance(seed_row, dict) or not seed_row:
                continue
            normalized.append(
                {
                    "id": f"{assist_type}-{index}",
                    "label": f"种子记录 {index + 1}",
                    "payload": {"seed_row": seed_row},
                    "note": note,
                }
            )
            continue
        payload = {
            key: value
            for key, value in item.items()
            if key != "note"
        }
        if not payload:
            continue
        if assist_type == "bindings":
            label = f"{payload.get('entity_field') or ''} → {payload.get('table_column') or ''}"
        elif assist_type == "table_selection":
            table_name = str(payload.get("table_name") or "").strip()
            create_table = payload.get("create_table")
            bindings = payload.get("bindings")
            label_parts: list[str] = []
            if table_name:
                label_parts.append(f"选择表 {table_name}")
            elif create_table:
                label_parts.append("新建表")
            if isinstance(bindings, list) and bindings:
                label_parts.append(f"绑定 {len(bindings)} 个字段")
            label = "，".join(label_parts) or "表选型建议"
        elif assist_type == "api_mapping":
            label = f"{payload.get('entity_field') or ''} ← {payload.get('source_field') or ''}"
        elif assist_type == "business_rules":
            label = str(payload.get("name") or "业务规则")
        elif assist_type == "relationships":
            label = (
                f"{payload.get('relation_type') or '关系'} → "
                f"{payload.get('target_entity_id') or ''}"
            )
        else:
            label = str(payload.get("name") or payload.get("description") or "设计建议")
        normalized.append(
            {
                "id": f"{assist_type}-{index}",
                "label": label,
                "payload": payload,
                "note": note,
            }
        )
    # 表选型只保留最接近的一个方案，避免给用户多个候选造成选择负担。
    if assist_type == "table_selection":
        return normalized[:1]
    return normalized


def _response_paths_from_context(context: dict[str, Any]) -> set[str]:
    """只从真实响应样例计算规范化路径，不信任调用方声明的 response_paths。"""

    paths: set[str] = set()
    payload = context.get("response_body")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except json.JSONDecodeError:
            payload = None

    def visit(node: Any, prefix: str, depth: int) -> None:
        """递归展开有限深度的响应样例，生成与前端一致的字段路径。"""

        if depth > 3 or len(paths) >= 300:
            return
        if isinstance(node, list):
            array_path = (
                f"{prefix}[]"
                if prefix and not prefix.endswith("[]")
                else (prefix or "[]")
            )
            if array_path:
                paths.add(array_path)
            if node:
                visit(node[0], array_path, depth)
            return
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            name = str(key).strip()
            if not name:
                continue
            path = f"{prefix}.{name}" if prefix else name
            paths.add(path)
            visit(value, path, depth + 1)

    visit(payload, "", 0)
    return paths


def _filter_api_mapping_suggestions(
    suggestions: list[dict[str, Any]],
    entity: dict[str, Any],
    response_paths: set[str],
) -> list[dict[str, Any]]:
    """过滤 AI 虚构的实体字段、响应路径及重复字段，确保建议可直接采纳。"""

    normalized_entity = normalize_entity(entity, 0, with_types=True)
    entity_fields = {
        str(field.get("name") or "").strip()
        for field in normalized_entity.get("fields", [])
        if field.get("name")
    }
    valid: list[dict[str, Any]] = []
    seen_fields: set[str] = set()
    for suggestion in suggestions:
        payload = suggestion.get("payload")
        if not isinstance(payload, dict):
            continue
        entity_field = str(payload.get("entity_field") or "").strip()
        source_field = str(payload.get("source_field") or "").strip()
        if not entity_field or entity_field not in entity_fields:
            continue
        if not source_field or source_field not in response_paths:
            continue
        if entity_field in seen_fields:
            continue
        seen_fields.add(entity_field)
        valid.append(suggestion)
    return valid


def _missing_entity_field_names_by_real_columns(
    entity: dict[str, Any],
    table_name: str,
    workspace_root: str | None,
) -> list[str]:
    """按真实列名对比兜底识别缺失字段，仅当模型未返回 missing_fields 时使用。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    column_names = _real_table_column_names(table_name, workspace_root)
    if column_names is None:
        return []
    return [
        str(field.get("name") or "")
        for field in normalized.get("fields", [])
        if str(field.get("name") or "").strip()
        and str(field.get("name") or "").strip() not in column_names
    ]


def _normalize_ai_missing_fields(
    entity: dict[str, Any],
    table_name: str,
    raw: Any,
    workspace_root: str | None,
) -> dict[str, Any]:
    """按 AI 决定补列的字段生成 DDL 规格；模型未返回时按真实列对比兜底。"""

    normalized = normalize_entity(entity, 0, with_types=True)
    field_by_name = {
        str(field.get("name") or ""): field
        for field in normalized.get("fields", [])
        if field.get("name")
    }
    names: list[str] = []
    if isinstance(raw, list):
        names = [
            str(
                item.get("entity_field")
                if isinstance(item, dict)
                else item or ""
            ).strip()
            for item in raw
        ]
    else:
        # 模型未返回或返回了非列表（如 {}）时，按真实列对比兜底；
        # 只有显式返回空列表才表示“不需要补列”。
        names = _missing_entity_field_names_by_real_columns(
            entity,
            table_name,
            workspace_root,
        )
    fields: list[dict[str, Any]] = []
    for name in names:
        field = field_by_name.get(name)
        if not field:
            continue
        fields.append(
            {
                "entity_field": name,
                "label": str(field.get("label") or name),
                "type": MYSQL_TYPE_BY_FIELD_TYPE.get(
                    str(field.get("type") or "text"),
                    "VARCHAR(255)",
                ),
                "nullable": not bool(field.get("required")),
                "comment": str(
                    field.get("label") or field.get("description") or name
                ),
            }
        )
    return {
        "table_name": table_name,
        # 用户已确认该表为实体目标表，缺失字段即可通过 DDL 补列。
        "eligible": bool(table_name),
        "fields": fields,
    }


def _real_table_column_names(
    table_name: str,
    workspace_root: str | None,
) -> set[str] | None:
    """读取指定表的真实列名；无工作区或读取失败时返回 None。"""

    if not table_name or not workspace_root:
        return None
    schema = inspect_mysql_table(
        {"data_source_id": "entity_design_assist"},
        table_name,
        workspace_root=workspace_root,
    )
    if schema.get("status") != "completed":
        return None
    names: set[str] = set()
    for table in schema.get("tables") or []:
        if (
            str(table.get("table_name") or table.get("name") or "") != table_name
        ):
            continue
        for column in table.get("columns") or []:
            if isinstance(column, dict) and column.get("name"):
                names.add(str(column.get("name")))
    return names or None


def _enrich_tables_with_columns(
    available_tables: Any,
    workspace_root: str | None,
) -> list[dict[str, Any]]:
    """把候选表的真实列名补进上下文，供模型据此生成绑定而非臆造。"""

    if not workspace_root or not isinstance(available_tables, list):
        return available_tables if isinstance(available_tables, list) else []
    enriched: list[dict[str, Any]] = []
    for table in available_tables[: _MAX_CONTEXT_TABLES]:
        if not isinstance(table, dict):
            continue
        item = dict(table)
        table_name = str(table.get("name") or "").strip()
        columns = _real_table_column_names(table_name, workspace_root)
        item["columns"] = sorted(columns) if columns else []
        enriched.append(item)
    return enriched


def _filter_bindings_to_real_columns(
    payload: dict[str, Any],
    suggestion: dict[str, Any],
    workspace_root: str | None,
) -> None:
    """把表选型建议的绑定过滤到所选表真实存在的列，避免 AI 臆造列名。"""

    table_name = str(payload.get("table_name") or "").strip()
    bindings = payload.get("bindings")
    if not table_name or not isinstance(bindings, list):
        return
    real_columns = _real_table_column_names(table_name, workspace_root)
    if real_columns is None:
        return
    valid: list[dict[str, Any]] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        column = str(item.get("table_column") or "").strip()
        if column and column in real_columns:
            valid.append(item)
    dropped = len(bindings) - len(valid)
    payload["bindings"] = valid
    if dropped:
        note = str(suggestion.get("note") or "").strip()
        suggestion["note"] = (
            f"{note} 已忽略 {dropped} 个所选表中不存在的列绑定。"
        ).strip()


def _filter_create_table_bindings(payload: dict[str, Any], entity: Any) -> None:
    """把建表建议中 AI 生成的绑定过滤到确定性目标表列，保留 AI 的映射关系。

    建表绑定关系由 AI 在提示词给出目标表结构后自行生成，这里只校验列名
    是否落在目标表内，不做任何硬编码映射。
    """

    target_table = entity_mysql_target_table(entity)
    columns = {
        str(column.get("name") or "").strip()
        for column in (target_table.get("columns") or [])
        if isinstance(column, dict)
    }
    bindings = payload.get("bindings")
    if not isinstance(bindings, list):
        payload["bindings"] = []
        return
    valid: list[dict[str, Any]] = []
    for item in bindings:
        if not isinstance(item, dict):
            continue
        entity_field = str(item.get("entity_field") or "").strip()
        table_column = str(item.get("table_column") or "").strip()
        if entity_field and table_column in columns:
            valid.append(item)
    payload["bindings"] = valid


def entity_design_ai_suggestions(
    entity: dict[str, Any],
    *,
    assist_type: str,
    instruction: str = "",
    context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """生成实体设计 AI 建议；模型失败时返回空建议与错误说明。"""

    if assist_type not in SUPPORTED_ASSIST_TYPES:
        return {
            "assist_type": assist_type,
            "text": "",
            "messages": [],
            "suggestions": [],
            "missing_fields": None,
            "source": "error",
            "note": f"不支持的 AI 辅助类型：{assist_type}",
        }
    # 表选型提示词补入候选表真实列名，避免模型因缺少列信息而无法绑定或臆造列。
    prompt_context = dict(context or {})
    api_mapping_response_paths: set[str] = set()
    if assist_type == "api_mapping":
        # 服务端从响应样例重新计算结构；模型只接收实体字段、路径结构和当前映射，
        # 不接收样例值，也不信任客户端可伪造的 response_paths。
        api_mapping_response_paths = _response_paths_from_context(prompt_context)
        prompt_context = {
            "response_paths": sorted(api_mapping_response_paths),
            "current_mappings": prompt_context.get("current_mappings") or [],
        }
    if assist_type == "table_selection":
        target_table = entity_mysql_target_table(entity)
        prompt_context = {
            **prompt_context,
            "available_tables": _enrich_tables_with_columns(
                prompt_context.get("available_tables"),
                str(prompt_context.get("workspace_root") or "").strip() or None,
            ),
            "suggested_target_table": {
                "name": target_table.get("name"),
                "columns": [
                    {
                        "name": column.get("name"),
                        "type": column.get("type"),
                        "comment": column.get("comment"),
                    }
                    for column in (target_table.get("columns") or [])
                    if isinstance(column, dict)
                ],
            },
        }
    prompt = _assist_prompt(
        entity,
        assist_type=assist_type,
        instruction=instruction,
        context=prompt_context,
    )
    history = [
        item
        for item in (context or {}).get("messages", [])
        if isinstance(item, dict)
    ][-20:]
    try:
        settings = Settings.from_env()
        result = create_chat_model(settings).bind(
            max_tokens=settings.default_max_tokens,
        ).invoke(prompt)
        content = _coerce_content_text(getattr(result, "content", result))
        parsed = extract_json_object(content) or {}
        suggestions = _normalize_suggestions(
            assist_type,
            parsed.get("suggestions") if isinstance(parsed, dict) else None,
        )
        if assist_type == "api_mapping":
            suggestions = _filter_api_mapping_suggestions(
                suggestions,
                entity,
                api_mapping_response_paths,
            )
        # 表选型若判定无合适表需新建，用实体确定性目标表结构替换 AI 自由文本，
        # 保证建表提案能通过既有校验并在确认后执行。
        workspace_root = (
            str((context or {}).get("workspace_root") or "").strip() or None
        )
        if assist_type == "table_selection":
            for suggestion in suggestions:
                payload = suggestion.get("payload")
                if isinstance(payload, dict) and payload.get("create_table"):
                    payload["create_table"] = entity_mysql_target_table(entity)
                    _filter_create_table_bindings(payload, entity)
                if isinstance(payload, dict) and payload.get("table_name"):
                    _filter_bindings_to_real_columns(
                        payload,
                        suggestion,
                        workspace_root,
                    )
        text = (
            str(parsed.get("text") or "").strip()
            if isinstance(parsed, dict)
            else ""
        )
        # 多轮对话：返回完整历史 + 本轮助手回复，前端按消息重建对话区。
        messages = [*history]
        if text:
            messages.append({"role": "assistant", "content": text})
        # 绑定辅助与表选型都要返回缺失字段：由 AI 决定哪些列需要补全，
        # 后端只负责把 AI 点名的字段补成合法 DDL 规格并判断是否可补列。
        missing_fields = None
        table_name = ""
        if assist_type == "bindings":
            table_name = str((context or {}).get("table_name") or "").strip()
        elif assist_type == "table_selection" and suggestions:
            payload = suggestions[0].get("payload") or {}
            table_name = str(payload.get("table_name") or "").strip()
        if table_name:
            missing_fields = _normalize_ai_missing_fields(
                entity,
                table_name,
                parsed.get("missing_fields") if isinstance(parsed, dict) else None,
                workspace_root,
            )
        return {
            "assist_type": assist_type,
            "text": text,
            "messages": messages[-20:],
            "suggestions": suggestions,
            "missing_fields": missing_fields,
            "source": "ai",
            "note": "",
        }
    except Exception as exc:
        return {
            "assist_type": assist_type,
            "text": "",
            "messages": history[-20:],
            "suggestions": [],
            "missing_fields": None,
            "source": "error",
            "note": f"AI 辅助生成失败：{type(exc).__name__}: {exc}",
        }
