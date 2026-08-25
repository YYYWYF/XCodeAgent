from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.services.frontend_page_tree import flatten_frontend_pages, is_menu_node
from app.services.entity_definitions import data_source_type_label, plan_data_sources
from app.services.project_plan import TECHNICAL_PLAN_ARTIFACT_TYPE
from app.workspace.spec_documents import workflow_artifact_root


def _bullet_items(items: Any) -> str:
    if not isinstance(items, list):
        return ""
    return "\n".join(f"- {_text_item(item)}" for item in items)


def _dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _label_items(value: Any) -> list[str]:
    return (
        [
            (
                str(item.get("name") or item.get("id") or item)
                if isinstance(item, dict)
                else str(item)
            )
            for item in value
            if str(item).strip()
        ]
        if isinstance(value, list)
        else []
    )


def _joined_labels(value: Any, *, empty: str = "无") -> str:
    items = _label_items(value)
    return "、".join(items) if items else empty


def _text_item(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "label", "title", "description", "id"):
            if value.get(key):
                return str(value[key])
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value).strip()


def _text_items(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [text for item in value if (text := _text_item(item))]


def _joined_items(value: Any, *, empty: str = "无") -> str:
    items = _text_items(value)
    return "、".join(items) if items else empty


def _json_brief(value: Any, *, empty: str = "无") -> str:
    """把结构化字段压缩成 Markdown 中可读的一行 JSON 摘要。"""

    if value in (None, "", [], {}):
        return empty
    return f"`{json.dumps(value, ensure_ascii=False, sort_keys=True)}`"


def _status_label(value: Any) -> str:
    labels = {
        "draft": "草稿",
        "pending_user_confirmation": "待确认",
        "confirmed": "已确认",
    }
    return labels.get(str(value), str(value or "草稿"))


def _code_items(value: Any, *, empty: str = "无") -> str:
    items = _text_items(value)
    return "、".join(f"`{item}`" for item in items) if items else empty


def _parameter_items(value: Any) -> str:
    parameters = []
    for item in _dict_items(value):
        name = item.get("name")
        if not name:
            continue
        location = item.get("in", "query")
        required = "必填" if item.get("required") else "可选"
        schema = item.get("schema")
        schema_type = _schema_type(schema) if isinstance(schema, dict) else "unknown"
        parameters.append(f"`{name}` {location}/{schema_type}/{required}")
    return "、".join(parameters) if parameters else "无"


def _layout_design_markdown(value: Any, fallback_layout: dict[str, Any]) -> str:
    layout = value if isinstance(value, dict) else {}
    regions = _dict_items(layout.get("regions"))
    region_lines = [
        f"- {region.get('name', '页面区域')}：{region.get('responsibility', '待补充区域职责')}"
        for region in regions
    ]
    if not region_lines:
        region_lines = [
            f"- {region}：待补充区域职责"
            for region in _text_items(fallback_layout.get("structure"))
        ]
    return "\n".join(
        [
            f"- 整体布局：{layout.get('overall_layout', '待补充整体布局')}",
            "",
            "区域划分：",
            *(region_lines or ["- 待补充区域划分"]),
            "",
            f"- 主要内容呈现方式：{layout.get('primary_content_presentation', '待补充主要内容呈现方式')}",
            f"- 操作入口位置：{layout.get('operation_entry_position', '待补充操作入口位置')}",
            f"- 响应式与信息密度：{layout.get('responsive_strategy') or fallback_layout.get('responsive') or '待补充响应式策略'}",
        ]
    )


def _api_dependencies_markdown(value: Any) -> str:
    items = []
    for item in _dict_items(value):
        endpoint_id = item.get("endpoint_id") or "endpoint"
        method = item.get("method") or "GET"
        path = item.get("path") or ""
        usage = item.get("usage") or "read"
        summary = item.get("summary") or "待补充 API 用途"
        initial = "，首屏加载依赖" if item.get("required_for_initial_load") else ""
        request_schema = item.get("request_schema_ref") or "无"
        response_schema = item.get("response_schema_ref") or "无"
        trigger = item.get("trigger") or "页面交互触发"
        binds_to = _joined_items(item.get("binds_to", []))
        items.append(
            f"- `{endpoint_id}` · `{method} {path}`：{summary}；用途 {usage}{initial}；"
            f"触发 {trigger}；绑定 {binds_to}；"
            f"请求 Schema {request_schema}，响应 Schema {response_schema}"
        )
    return "\n".join(items) if items else "- 暂无页面 API 依赖"


def _operation_interactions_markdown(value: Any) -> str:
    items = []
    for item in _dict_items(value):
        action = item.get("action") or item.get("name") or "页面操作"
        trigger = item.get("trigger") or "用户操作"
        behavior = item.get("behavior") or item.get("description") or "待补充"
        endpoint = f"，API `{item['endpoint_id']}`" if item.get("endpoint_id") else ""
        items.append(f"- {action}：触发 {trigger}；行为 {behavior}{endpoint}")
    return "\n".join(items) if items else "- 待补充主要交互"


def _state_feedback_markdown(value: Any) -> str:
    items = []
    for item in _dict_items(value):
        state = item.get("state") or item.get("name") or "反馈状态"
        trigger = item.get("trigger") or "页面交互"
        behavior = item.get("behavior") or item.get("description") or "待补充"
        scope = item.get("scope") or "相关业务区域"
        items.append(f"- {state}：触发 {trigger}；作用于 {scope}；反馈 {behavior}")
    return "\n".join(items) if items else "- 待补充状态反馈"


def _response_bindings_markdown(value: Any) -> str:
    items = []
    for item in _dict_items(value):
        endpoint_id = item.get("endpoint_id") or "endpoint"
        source_path = item.get("source_path") or ""
        page_field = item.get("page_field") or source_path or "页面字段"
        items.append(f"- `{endpoint_id}`：`{source_path}` -> {page_field}")
    return "\n".join(items) if items else "- 暂无响应字段绑定"


def _page_navigation_markdown(value: Any) -> str:
    items = []
    for item in _dict_items(value):
        trigger = item.get("trigger") or item.get("action") or "页面跳转"
        target = item.get("targetPageId") or item.get("target_path") or "待补充目标页面"
        behavior = item.get("behavior") or item.get("description") or "待补充"
        items.append(f"- {trigger}：跳转到 {target}；行为 {behavior}")
    return "\n".join(items) if items else "- 暂无页面跳转依赖"


def _operation_visibility_markdown(value: Any) -> str:
    items = []
    for item in _dict_items(value):
        action = item.get("action") or "页面操作"
        visible_to = _joined_items(item.get("visible_to", []), empty="待补充")
        unauthorized = item.get("unauthorized_behavior") or "隐藏操作入口或展示无权限提示。"
        items.append(f"- {action}：可见角色 {visible_to}；无权限时 {unauthorized}")
    return "\n".join(items) if items else "- 待补充操作可见性"








def _entity_design_source_sections(detail: dict[str, Any]) -> list[str]:
    """按实体设计数据源类型渲染分方案设计段落（数据库 / 外部 API / 静态数据）。"""

    source_type = str(detail.get("data_source_type") or "")
    if source_type == "database":
        return _database_design_markdown_section(detail)
    if source_type == "external_api":
        return _external_api_design_markdown_section(detail)
    if source_type == "static":
        return _static_design_markdown_section(detail)
    return []


def _database_design_markdown_section(detail: dict[str, Any]) -> list[str]:
    """渲染数据库方案的可用表、绑定、差异、表操作与落地证据。"""

    database_design = (
        detail.get("database_design")
        if isinstance(detail.get("database_design"), dict)
        else {}
    )
    schema_context = (
        database_design.get("schema_context")
        if isinstance(database_design.get("schema_context"), dict)
        else {}
    )
    available_tables = _dict_items(database_design.get("available_tables"))
    bindings = _dict_items(database_design.get("bindings"))
    differences = _dict_items(database_design.get("differences"))
    operations = _dict_items(database_design.get("database_operations"))
    table_generation = (
        database_design.get("table_generation")
        if isinstance(database_design.get("table_generation"), dict)
        else {}
    )
    lines = [
        "## 三-A、数据库方案",
        "",
        f"- 数据库连接：{schema_context.get('status') or '未检测'}",
        f"- 可用表数量：{len(available_tables)}",
        f"- 绑定状态：{database_design.get('binding_status') or '待绑定'}",
        f"- 目标表：`{database_design.get('matched_table') or '未匹配'}`",
    ]
    if bindings:
        lines.extend(["", "字段绑定："])
        lines.extend(
            f"- `{binding.get('entity_field', '')}` -> "
            f"`{binding.get('table', '')}.{binding.get('table_column', '')}`"
            f"（{binding.get('rule', 'manual')}）"
            for binding in bindings
        )
    if differences:
        lines.extend(["", "结构化差异："])
        lines.extend(
            f"- {difference.get('kind', 'unknown')}：{difference.get('field', '')}，"
            f"期望 {difference.get('expected', '')} / 实际 {difference.get('actual', '')}"
            f"（{difference.get('resolution', '')}）"
            for difference in differences
        )
    if operations:
        lines.extend(["", "数据库操作："])
        lines.extend(
            (
                f"- `{operation.get('operation', '')}` "
                f"{_operation_table_text(operation)}"
            )
            for operation in operations
        )
    if table_generation.get("required"):
        proposal = (
            table_generation.get("proposal")
            if isinstance(table_generation.get("proposal"), dict)
            else {}
        )
        proposal_columns = _dict_items(proposal.get("columns"))
        lines.extend(
            [
                "",
                "目标表结构（需用户审批）：",
                f"- 表名：`{proposal.get('name', '')}`",
                f"- 表注释：{proposal.get('comment', '') or '未生成'}",
                *(
                    f"  - `{column.get('name', '')}` {column.get('type', '')} "
                    f"{'非空' if not column.get('nullable') else '可空'}"
                    for column in proposal_columns
                ),
                f"- 审批状态：{'已审批' if table_generation.get('approved') else '待审批'}",
            ]
        )
    execution = (
        detail.get("database_execution")
        if isinstance(detail.get("database_execution"), dict)
        else {}
    )
    if execution:
        lines.extend(
            [
                "",
                f"- 表操作落地：{execution.get('status')}，{execution.get('summary') or ''}",
            ]
        )
    lines.append("")
    return lines


def _operation_table_text(operation: dict[str, Any]) -> str:
    """渲染单条数据库操作的目标对象。"""

    raw_table = operation.get("table")
    if isinstance(raw_table, dict):
        return f"`{raw_table.get('name', '')}`"
    table = str(raw_table or "")
    column = str(operation.get("column") or "")
    return f"`{table}.{column}`" if column else f"`{table}`"


def _external_api_design_markdown_section(detail: dict[str, Any]) -> list[str]:
    """渲染外部 API 方案的接口信息与字段映射。"""

    external_api_design = (
        detail.get("external_api_design")
        if isinstance(detail.get("external_api_design"), dict)
        else {}
    )
    api_info = (
        external_api_design.get("api_info")
        if isinstance(external_api_design.get("api_info"), dict)
        else {}
    )
    mappings = _dict_items(external_api_design.get("field_mappings"))
    lines = [
        "## 三-A、外部 API 方案",
        "",
        f"- 请求路径：`{api_info.get('path') or '待补充'}`",
        f"- 请求方式：`{api_info.get('method') or '待补充'}`",
        f"- 请求体：{_json_brief(api_info.get('request_body'))}",
        f"- 返回体：{_json_brief(api_info.get('response_body'))}",
    ]
    if mappings:
        lines.extend(["", "返回体字段绑定："])
        lines.extend(
            f"- `{mapping.get('entity_field', '')}` <- `{mapping.get('source_field', '')}`"
            f"（{mapping.get('rule', 'manual')}）"
            for mapping in mappings
        )
    lines.append("")
    return lines


def _static_design_markdown_section(detail: dict[str, Any]) -> list[str]:
    """渲染静态数据方案的种子数据与字段取值。"""

    static_design = (
        detail.get("static_design")
        if isinstance(detail.get("static_design"), dict)
        else {}
    )
    seed_rows = _dict_items(static_design.get("seed_rows"))
    field_values = (
        static_design.get("field_values")
        if isinstance(static_design.get("field_values"), dict)
        else {}
    )
    lines = ["## 三-A、静态数据方案", ""]
    if seed_rows:
        lines.extend(["种子数据：", *[f"- {_json_brief(row)}" for row in seed_rows]])
    if field_values:
        lines.extend(["", "字段取值/枚举："])
        for key, values in field_values.items():
            lines.append(f"- `{key}`：{_joined_items(values)}")
    if not seed_rows and not field_values:
        lines.append("- 暂无静态数据，请补充字段取值、枚举或种子数据。")
    lines.append("")
    return lines


def render_entity_detail_markdown(detail: dict[str, Any]) -> str:
    """渲染单个实体详细设计的独立 Markdown 文档。"""

    entity_name = str(detail.get("entity_name") or detail.get("entity_id") or "未命名实体")
    entity_id = str(detail.get("entity_id") or "unknown")
    data_source_type = str(detail.get("data_source_type") or "")
    fields = _dict_items(detail.get("fields"))
    field_lines = (
        "\n".join(
            f"| `{field.get('name', '')}` | {field.get('label', '')} | "
            f"{field.get('type', 'text')} | {'必填' if field.get('required') else '可选'} | "
            f"`{field.get('column_type', '')}` | {field.get('description', '')} |"
            for field in fields
        )
        if fields
        else "| - | - | - | - | - | - |"
    )
    table_design = (
        detail.get("table_design")
        if isinstance(detail.get("table_design"), dict)
        else {}
    )
    columns = _dict_items(table_design.get("columns"))
    column_lines = "\n".join(
        f"- `{column.get('name', '')}` {column.get('type', '')} "
        f"{'非空' if not column.get('nullable') else '可空'}：{column.get('comment', '')}"
        for column in columns
    )
    relationships = _dict_items(detail.get("relationships"))
    return "\n".join(
        [
            f"# 实体详细设计：{entity_name}（`{entity_id}`）",
            "",
            "## 一、实体基本信息",
            "",
            f"- 实体 ID：`{entity_id}`",
            f"- 实体名称：{entity_name}",
            f"- 实体说明：{detail.get('description') or '待补充'}",
            f"- 所属模块：`{detail.get('module_id') or '未归属'}`",
            f"- 数据源：{data_source_type_label(data_source_type)}"
            f"（`{detail.get('data_source_id') or data_source_type}`）",
            f"- 确认状态：{_status_label(detail.get('status', 'draft'))}",
            "",
            "## 二、字段设计",
            "",
            "| 字段名 | 展示名称 | 语义类型 | 必填 | 列类型 | 说明 |",
            "| --- | --- | --- | --- | --- | --- |",
            field_lines,
            "",
            "## 三、目标表结构",
            "",
            (
                "\n".join(
                    [
                        f"- 表名：`{table_design.get('name', '未生成')}`",
                        f"- 表注释：{table_design.get('comment', '') or '未生成'}",
                        f"- 主键：{_code_items(table_design.get('primary_key', []), empty='未生成')}",
                        "",
                        "列清单：",
                        column_lines or "- 未生成",
                    ]
                )
                if table_design
                else f"- 数据源类型为 {data_source_type_label(data_source_type)}，不生成数据库表。"
            ),
            "",
            *_entity_design_source_sections(detail),
            "## 四、业务规则",
            "",
            _bullet_items(
                [
                    f"{rule.get('name', '业务规则')}：{rule.get('description', '')}"
                    for rule in _dict_items(detail.get("business_rules"))
                ]
            )
            or "- 暂无业务规则",
            "",
            "## 五、关系设计",
            "",
            (
                "\n".join(
                    f"- `{rel.get('relation_type', '关系')}`："
                    f"{rel.get('target_entity_id', '')}，{rel.get('description', '')}"
                    for rel in relationships
                )
                if relationships
                else "- 本轮项目计划未声明实体关系，按无关系处理。"
            ),
            "",
            "## 六、验收标准",
            "",
            _bullet_items(detail.get("acceptance_criteria", [])) or "- 待补充实体验收标准",
            "",
            "## 七、风险与待确认事项",
            "",
            _bullet_items(detail.get("risks", [])) or "- 暂无明确风险",
            "",
        ]
    )


def _schema_type(schema: Any) -> str:
    if not isinstance(schema, dict):
        return "unknown"
    schema_type = schema.get("type")
    if schema_type:
        return str(schema_type)
    if schema.get("$ref"):
        return f"ref:{schema['$ref']}"
    return "object" if schema.get("properties") else "unknown"


def _schema_summary(name: str, schema: Any) -> str:
    if not isinstance(schema, dict):
        return f"- `{name}`：{_text_item(schema)}"
    properties = schema.get("properties")
    required = set(schema.get("required", [])) if isinstance(schema.get("required"), list) else set()
    if isinstance(properties, dict) and properties:
        fields = "；".join(
            f"`{field}` {_schema_type(field_schema)}"
            f"{' 必填' if field in required else ''}"
            for field, field_schema in properties.items()
        )
        return f"- `{name}`：{fields}"
    return f"- `{name}`：{_schema_type(schema)}"


def _api_contract_markdown(contract: dict[str, Any]) -> str:
    schemas = contract.get("schemas", {})
    schema_lines = (
        "\n".join(
            _schema_summary(str(name), schema)
            for name, schema in schemas.items()
        )
        if isinstance(schemas, dict)
        else "- 暂无 Schema 字段"
    )
    endpoint_lines = "\n".join(
        "\n".join(
            [
                f"- `{endpoint.get('id', 'endpoint')}` · `{endpoint.get('method', 'GET')} {endpoint.get('path', '')}`：{endpoint.get('summary', '待补充接口说明')}",
                f"  - 参数：{_parameter_items(endpoint.get('parameters', []))}",
                f"  - 请求 Schema：{endpoint.get('request_schema_ref') or '无'}",
                f"  - 响应 Schema：{endpoint.get('response_schema_ref') or '无'}",
                f"  - 错误码：{_code_items(endpoint.get('error_codes', []))}",
            ]
        )
        for endpoint in _dict_items(contract.get("endpoints", []))
    )
    return "\n".join(
        [
            f"### `{contract.get('base_path', '/api/resource')}` {contract.get('resource', contract.get('id', 'Resource'))}",
            "",
            "#### 字段 Schema",
            "",
            schema_lines,
            "",
            "#### Endpoint",
            "",
            endpoint_lines or "- 暂无 Endpoint",
        ]
    )


def _page_references_markdown(page: dict[str, Any]) -> str:
    """渲染 ProjectPlan 中页面的不可变引用依赖，供用户确认。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    permissions = references.get("permissions") or page.get("permissions", [])
    endpoints = references.get("endpoint_dependencies") or page.get("endpoint_dependencies", [])
    navigation = references.get("navigation_targets") or page.get("navigation_targets", [])
    implementations = references.get("action_implementations") or page.get("action_implementations", [])
    endpoint_lines = [
        f"  - `{item.get('endpoint_id', 'unknown')}`：{item.get('usage', 'read')}；"
        f"触发 {item.get('trigger', '页面交互')}；"
        f"{'首屏依赖' if item.get('required_for_initial_load') else '非首屏依赖'}"
        for item in _dict_items(endpoints)
    ]
    navigation_lines = [
        f"  - `{item.get('targetPageId', 'unknown')}`：{item.get('trigger', '页面跳转')}"
        for item in _dict_items(navigation)
    ]
    implementation_lines = [
        f"  - action `{item.get('actionId', 'unknown')}`："
        + (
            f"endpoint `{item.get('endpointId', '')}`"
            if item.get("endpointId")
            else "；".join(
                f"step `{step.get('stepId', '')}` → endpoint `{step.get('endpointId', '')}`"
                for step in _dict_items(item.get("stepBindings"))
            )
        )
        for item in _dict_items(implementations)
    ]
    return "\n".join(
        [
            f"### `{page.get('pageId', 'unknown')}` {page.get('name', '未命名页面')} `{page.get('path', '/')}`",
            "",
            f"- 模块：`{page.get('module_id', 'core')}`",
            f"- 页面权限：{_joined_items(permissions)}",
            "- endpoint_dependencies:",
            *(endpoint_lines or ["  - 无；静态页面或需在项目计划中补充接口依赖"]),
            "- navigation_targets:",
            *(navigation_lines or ["  - 无"]),
            "- action_implementations（仅 endpoint 技术选择）:",
            *(implementation_lines or ["  - 无；本页没有需要 endpoint 的业务操作"]),
        ]
    )


def _frontend_page_tree_markdown(nodes: Any, *, level: int = 0) -> list[str]:
    """递归渲染 frontend_pages 的菜单层级，便于用户确认目录关系。"""

    lines: list[str] = []
    indent = "  " * level
    for node in _dict_items(nodes):
        if is_menu_node(node):
            name = str(node.get("name") or "未命名菜单").strip() or "未命名菜单"
            unique_path = str(node.get("unique_path") or "").strip()
            child_pages = len(flatten_frontend_pages(node.get("children")))
            if unique_path:
                lines.append(
                    f"{indent}- 菜单 `{name}` · {child_pages} 个页面 · 路由 `{unique_path}`"
                )
            else:
                lines.append(f"{indent}- 菜单 `{name}` · {child_pages} 个页面")
            child_lines = _frontend_page_tree_markdown(node.get("children"), level=level + 1)
            lines.extend(child_lines or [f"{indent}  - 暂无子页面"])
            continue
        page_name = str(node.get("name") or node.get("pageId") or "未命名页面")
        page_id = str(node.get("pageId") or node.get("id") or "unknown")
        page_path = str(node.get("path") or "/")
        page_desc = str(node.get("description") or "").strip()
        description_suffix = f"：{page_desc}" if page_desc else ""
        lines.append(
            f"{indent}- 页面 `{page_name}` (`{page_id}` · `{page_path}`){description_suffix}"
        )
    return lines


def _action_binding_target(binding: dict[str, Any]) -> str:
    """把类型化操作绑定压缩成开发确认文档中的可读目标。"""

    binding_type = str(binding.get("bindingType") or "unknown")
    if binding_type == "endpoint":
        return f"API `{binding.get('endpointId', '')}`"
    if binding_type == "navigation":
        return f"跳转页面 `{binding.get('targetPageId', '')}`"
    if binding_type == "local":
        return f"本地行为：{binding.get('localEffect', '')}"
    if binding_type == "external":
        return f"外部目标：{binding.get('externalTarget', '')}"
    if binding_type == "sequence":
        steps = [
            _action_binding_target({**step, "bindingType": step.get("type")})
            for step in _dict_items(binding.get("steps"))
        ]
        return "按顺序执行 " + " → ".join(steps)
    return "未明确实现方式"


def _page_implementation_contract_markdown(contract: dict[str, Any]) -> str:
    """逐条展示 UI 控件、产品操作与技术实现的绑定链路。"""

    binding_lines = []
    for binding in _dict_items(contract.get("actionBindings")):
        controls = [
            str(control.get("label") or control.get("controlId") or "未命名控件")
            for control in _dict_items(binding.get("uiControlRefs"))
        ]
        binding_lines.append(
            f"  - UI `{('、'.join(controls) or '无独立控件')}` → "
            f"action `{binding.get('actionId', '')}` {binding.get('actionName', '')} → "
            f"{_action_binding_target(binding)}"
        )
    return "\n".join(
        [
            f"- `{contract.get('pageId', 'unknown')}`：UI `{contract.get('uiDesignRef', {}).get('path', '')}`；"
            f"接口 {_code_items(contract.get('requiredEndpointIds', []))}",
            *(binding_lines or ["  - 无业务操作绑定"]),
        ]
    )


def _technical_page_references_markdown(page: dict[str, Any]) -> str:
    """渲染当前页面新增的 endpoint 依赖与业务 action 实现。"""

    references = page.get("references") if isinstance(page.get("references"), dict) else {}
    dependencies = [
        f"  - `{item.get('endpoint_id', '')}`：{item.get('usage', '')}；"
        f"触发={item.get('trigger', '')}；首屏必需={bool(item.get('required_for_initial_load'))}"
        for item in _dict_items(references.get("endpoint_dependencies"))
    ]
    implementations = [
        f"  - `{item.get('actionId', '')}`："
        f"{json.dumps({key: value for key, value in item.items() if key != 'actionId'}, ensure_ascii=False)}"
        for item in _dict_items(references.get("action_implementations"))
    ]
    return "\n".join(
        [
            f"### `{page.get('pageId', 'unknown')}`",
            "",
            "Endpoint 依赖：",
            *(dependencies or ["  - 无"]),
            "",
            "业务 Action 实现：",
            *(implementations or ["  - 无"]),
        ]
    )


def _technical_entity_markdown(entity: dict[str, Any]) -> str:
    """把 TechnicalPlan 权威实体字段渲染为开发可审核的 Markdown。"""

    fields = _dict_items(entity.get("fields"))
    field_rows = [
        "| 字段名 | 展示名 | 类型 | 必填 | 说明 |",
        "| --- | --- | --- | --- | --- |",
        *[
            f"| `{field.get('name', '')}` | {field.get('label', '')} | "
            f"{field.get('type', 'text')} | {'是' if field.get('required') else '否'} | "
            f"{field.get('description', '') or '-'} |"
            for field in fields
        ],
    ]
    return "\n".join(
        [
            f"### {entity.get('name', entity.get('id', '未命名实体'))} "
            f"(`{entity.get('id', 'unknown')}`)",
            "",
            str(entity.get("description") or "暂无实体描述。"),
            "",
            *(field_rows if fields else ["- 暂无字段"]),
        ]
    )


def _render_technical_plan_markdown(plan: dict[str, Any]) -> str:
    """渲染只供开发审核且不重复产品事实的 TechnicalPlan。"""

    architecture = plan.get("architecture") if isinstance(plan.get("architecture"), dict) else {}
    api_contracts = "\n\n".join(
        _api_contract_markdown(contract)
        for contract in _dict_items(plan.get("api_contracts"))
    )
    entities = "\n\n".join(
        _technical_entity_markdown(entity)
        for entity in _dict_items(plan.get("entities"))
    )
    pages = "\n\n".join(
        _technical_page_references_markdown(page)
        for page in _dict_items(plan.get("pages"))
    )
    return f"""# 技术规划

- 状态：{_status_label(plan.get('confirmation_status', 'draft'))}
- 类型：{plan.get('artifact_type', TECHNICAL_PLAN_ARTIFACT_TYPE)}

## 技术架构

- 前端：{architecture.get('frontend', '待补充')}
- 后端：{architecture.get('backend', '待补充')}
- 数据：{architecture.get('data', '待补充')}

## 业务实体

{entities or '- 无'}

## API 契约

{api_contracts or '- 无'}

## 页面技术引用

{pages or '- 无'}
"""


def render_project_plan_markdown(plan: dict[str, Any]) -> str:
    """按 ProjectPlan 数据源类型渲染真实 HTTP 或前端 Mock 契约文档。"""

    if plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE:
        return _render_technical_plan_markdown(plan)

    overview = plan.get("requirements_overview", {})
    acceptance_criteria = plan.get("project_acceptance_criteria") or plan.get(
        "acceptance_criteria",
        [],
    )
    api_contracts = "\n\n".join(
        _api_contract_markdown(contract)
        for contract in _dict_items(plan.get("api_contracts", []))
    )
    page_tree = "\n".join(_frontend_page_tree_markdown(plan.get("frontend_pages", [])))
    pages = "\n\n".join(
        _page_references_markdown(page)
        for page in flatten_frontend_pages(plan.get("frontend_pages", []))
    )
    data_sources = "\n".join(
        f"- `{source.get('id', 'unknown')}` {source.get('name', '未命名数据源')}："
        f"{source.get('description', '') or '暂无业务描述'}；"
        f"实体 {_joined_items(source.get('entities', []))}，"
        f"Schema 引用 {_code_items(source.get('schema_refs', []))}，"
        f"类型 {source.get('type', 'database')}"
        for source in plan_data_sources(plan)
    )
    permissions = plan.get("permission_model", {})
    page_access = "\n".join(
        f"- `{item.get('path', item.get('pageId', 'unknown'))}`："
        f"{_joined_items(item.get('allowed_roles', []))}"
        for item in _dict_items(permissions.get("page_access", []))
    )
    operation_permissions = "\n".join(
        f"- `{item.get('role_id', 'unknown')}`："
        f"{_joined_items(item.get('operations', []))}"
        for item in _dict_items(permissions.get("operation_permissions", []))
    )
    page_contracts = "\n".join(
        _page_implementation_contract_markdown(contract)
        for contract in _dict_items(plan.get("page_implementation_contracts", []))
    )
    entity_details = "\n".join(
        f"- {entity.get('name', entity.get('id', '未命名实体'))}："
        f"{entity.get('detail_design', {}).get('markdown_path', '尚未生成独立详细设计')}"
        for entity in _dict_items(plan.get("entities"))
        if isinstance(entity.get("detail_design"), dict)
    )
    app = plan.get("app", {})
    architecture = plan.get("architecture", {})
    source_types = {
        str(source.get("type"))
        for source in plan_data_sources(plan)
        if source.get("type")
    }
    contract_title = (
        "前端 Mock 数据契约"
        if source_types and source_types <= {"static"}
        else "真实 HTTP API 契约"
    )
    backend_stack = (
        architecture.get("backend_tech_stack")
        if isinstance(architecture.get("backend_tech_stack"), dict)
        else {}
    )
    backend_stack_text = "；".join(
        f"{label}{backend_stack[key]}"
        for key, label in (
            ("language", "开发语言 "),
            ("framework", "开发框架 "),
            ("database", "数据库 "),
            ("cache", "缓存 "),
        )
        if backend_stack.get(key)
    ) or "待补充后端技术栈"
    engineering_design = (
        plan.get("engineering_design")
        if isinstance(plan.get("engineering_design"), dict)
        else {}
    )
    engineering_sections = "\n\n".join(
        f"### {label}\n\n{_bullet_items(engineering_design.get(key, [])) or '- 无'}"
        for key, label in (
            ("module_boundaries", "模块边界与所有权"),
            ("data_models", "数据模型与约束"),
            ("storage_and_indexes", "存储、索引与迁移"),
            ("cache_strategy", "缓存策略"),
            ("transactions_and_idempotency", "事务、并发与幂等"),
            ("error_model", "错误模型与重试"),
            ("security_controls", "认证、授权与审计"),
            ("observability", "日志、指标与链路追踪"),
            ("performance_targets", "性能与容量目标"),
            ("deployment_constraints", "部署、迁移与回滚约束"),
            ("test_strategy", "工程测试策略"),
        )
    )

    document_title = (
        "技术规划"
        if plan.get("artifact_type") == TECHNICAL_PLAN_ARTIFACT_TYPE
        else "总体计划书"
    )
    return f"""# {app.get('name', '未命名应用')}{document_title}

## 项目概述

- 应用：{app.get('name', '未命名应用')}
- 摘要：{app.get('summary', '待补充项目摘要')}
- 目标：{overview.get('target', '生成一个可在本地运行的前后端应用工程。')}
- 状态：{_status_label(plan.get('confirmation_status') or plan.get('status', 'draft'))}
- 版本：{plan.get('version', '0.1.0')}

## 需求概述

- 需求摘要：{overview.get('summary', app.get('summary', '待补充需求摘要'))}
- 用户角色：{_joined_labels(overview.get('roles', []))}
- 功能模块：{_joined_labels(overview.get('modules', []))}
- 业务流程：{_joined_labels(overview.get('business_flows', []))}

## 整体需求验收标准

{_bullet_items(acceptance_criteria) or "- 待补充整体验收标准"}

## 技术架构

- 前端：{architecture.get('frontend', '待补充前端架构')}
- 后端：{architecture.get('backend', '待补充后端架构')}
- 后端技术栈：{backend_stack_text}
- 数据：{architecture.get('data', '待补充数据架构')}
- 测试：{architecture.get('testing', '待补充测试策略')}

## 工程设计

{engineering_sections or "- 暂无工程设计"}

## {contract_title}

{api_contracts or "- 暂无数据契约"}

## 前端页面清单

### 菜单结构

{page_tree or "- 暂无菜单结构"}

### 页面详情

{pages or "- 暂无前端页面"}

## 数据源清单

{data_sources or "- 暂无数据源"}

## 权限体系

- 默认策略：{permissions.get('default_policy', 'deny_unlisted')}

### 页面访问

{page_access or "- 暂无页面权限规则"}

### 操作权限

{operation_permissions or "- 暂无操作权限规则"}

## 页面实现契约

{page_contracts or "- 暂无页面实现契约"}

## 实体详细设计

{entity_details or "- 尚未确认实体详细设计"}

## 风险与待细化点

{_bullet_items(plan.get('risks', [])) or "- 暂无风险与待细化点"}
"""


def write_project_plan_document(state: dict[str, Any], plan: dict[str, Any]) -> str:
    """写入当前规划文档；创建流程只写当前 TechnicalPlan。"""

    if state.get("workflow_scope") == "application_planning":
        markdown_path, _ = write_technical_plan_document(state, plan)
        return markdown_path

    path = project_plan_markdown_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_project_plan_json(state, plan)
    compact_plan = load_project_plan_json(project_plan_json_path(state))
    path.write_text(render_project_plan_markdown(compact_plan), encoding="utf-8")
    return str(path)


def technical_plan_markdown_path(state: dict[str, Any]) -> Path:
    """返回开发确认使用的 TechnicalPlan Markdown 正式路径。"""

    return workflow_artifact_root(state) / "plans" / "technical-plan.md"


def technical_plan_json_path(state: dict[str, Any]) -> Path:
    """返回开发确认使用的 TechnicalPlan JSON 正式路径。"""

    return workflow_artifact_root(state) / "plans" / "technical-plan.json"


def edited_technical_plan_markdown(
    state: dict[str, Any],
    plan: dict[str, Any],
) -> str | None:
    """读取用户在开发确认阶段直接修改的 TechnicalPlan Markdown。"""

    path = technical_plan_markdown_path(state)
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    return content if content != render_project_plan_markdown(plan) else None


def write_technical_plan_document(
    state: dict[str, Any],
    plan: dict[str, Any],
) -> tuple[str, str]:
    """写入当前 TechnicalPlan 正式文件。"""

    from app.workspace.detail_design_documents import write_compact_project_plan

    json_path = technical_plan_json_path(state)
    markdown_path = technical_plan_markdown_path(state)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    write_compact_project_plan(state, json_path, plan)
    compact_plan = load_project_plan_json(json_path)
    markdown_path.write_text(render_project_plan_markdown(compact_plan), encoding="utf-8")
    return str(markdown_path), str(json_path)


def project_plan_markdown_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("project_plan_path")
    return (
        Path(existing_path)
        if existing_path and str(existing_path).endswith(".md")
        else workflow_artifact_root(state) / "plans" / "project-plan.md"
    )


def edited_project_plan_markdown(
    state: dict[str, Any],
    plan: dict[str, Any],
) -> str | None:
    path = project_plan_markdown_path(state)
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    return content if content != render_project_plan_markdown(plan) else None


def project_plan_json_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("project_plan_json_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "plans" / "project-plan.json"
    )


def write_project_plan_json(state: dict[str, Any], plan: dict[str, Any]) -> str:
    """持久化轻量 ProjectPlan，并把页面和 endpoint 详情拆分为独立文件。"""

    from app.workspace.detail_design_documents import write_compact_project_plan

    path = project_plan_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    write_compact_project_plan(state, path, plan)
    return str(path)


def load_project_plan_json(
    path: str | Path,
    *,
    hydrate_detail_designs: bool = False,
) -> dict[str, Any]:
    """读取 ProjectPlan JSON；按需把外置详情文件读回内存。"""

    plan_path = Path(path)
    project_plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not hydrate_detail_designs or not isinstance(project_plan, dict):
        return project_plan

    from app.workspace.detail_design_documents import hydrate_external_detail_designs

    return hydrate_external_detail_designs(plan_path, project_plan)
