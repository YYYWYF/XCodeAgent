from __future__ import annotations

import json
from typing import Any

from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.database_execution import (
    classify_database_plan_risk,
    create_database_execution_context,
    database_plan_hash,
    execute_database_plan,
    request_database_approval_if_needed,
)
from app.services.database_schema_diff import diff_database_schema
from app.services.database_schema_summary import inspect_mysql_schema
from app.services.entity_definitions import entity_mysql_target_table, normalize_entity
from app.utils.model_output import extract_json_object


def _database_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    database_summary: dict[str, Any],
) -> str:
    """构造数据库 Agent 的计划生成提示词。"""

    return (
        "You are the Database Change Agent in an app-generation workflow.\n"
        "Use the latest real database summary below. Generate only the database "
        "change plan for the approved database tasks. Do not modify backend or "
        "frontend code. Do not execute SQL. Do not use ProjectPlan.data_sources as "
        "truth for existing tables; use the supplied database_summary.\n\n"
        "Return one final JSON object in this shape:\n"
        "{\n"
        '  "database_change_plan": {\n'
        '    "summary": "short summary",\n'
        '    "statements": ["SQL statement without trailing semicolon"],\n'
        '    "rollback": ["optional rollback SQL or explanation"],\n'
        '    "assumptions": ["bounded assumptions"],\n'
        '    "task_results": [\n'
        '      {"task_id": "task id", "status": "completed|already_satisfied|failed", "summary": "task summary"}\n'
        "    ]\n"
        "  }\n"
        "}\n\n"
        f"Approved database tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
        f"Latest database_summary:\n{json.dumps(database_summary, ensure_ascii=False, indent=2)}\n\n"
        f"BuildTaskPlan summary:\n{json.dumps(build_task_plan.get('summary', {}), ensure_ascii=False, indent=2)}\n\n"
        f"ProjectPlan API context:\n{json.dumps(project_plan.get('api_contracts', []), ensure_ascii=False, indent=2)}\n"
    )


def _target_from_tasks(tasks: list[dict[str, Any]]) -> dict[str, Any]:
    """从数据库任务里抽取可用于重新扫描数据库的目标信息。"""

    for task in tasks:
        database_scope = task.get("database_scope")
        if isinstance(database_scope, dict) and database_scope:
            return {
                "data_source_id": database_scope.get("data_source_id") or task.get("unit_id"),
                "data_source": {
                    "type": "database",
                    "tables": database_scope.get("tables") or database_scope.get("table_names") or [],
                },
                "method": "DATABASE",
                "path": task.get("id"),
                "endpoint_id": task.get("source_refs", {}).get("endpoint_id")
                if isinstance(task.get("source_refs"), dict)
                else None,
            }
    return {
        "data_source_id": "database",
        "data_source": {"type": "database"},
        "method": "DATABASE",
        "path": "database-task",
    }


def _invoke_live_database_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    database_summary: dict[str, Any],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """调用数据库 Deep Agent 生成 SQL 计划，执行由确定性服务负责。"""

    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).database,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _database_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
                        database_summary=database_summary,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )


def generate_database_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
    database_change_plan: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """通过数据库 Deep Agent 生成计划，并在高危 SQL 执行前走审批。

    审批恢复时传入已批准的 database_change_plan 直接复用，避免重新生成计划
    导致审批指纹（plan hash）变化而使批准失效。
    """

    if not tasks:
        return []

    database_summary = inspect_mysql_schema(_target_from_tasks(tasks), workspace)
    if database_summary.get("status") != "completed":
        return [_database_failure_result(task, database_summary) for task in tasks]
    required_schema = _required_schema_from_tasks(tasks, database_summary)
    remaining_gaps = diff_database_schema(
        actual_schema=_actual_schema_from_summary(database_summary),
        required_schema=required_schema,
    )
    if required_schema.get("tables") and not remaining_gaps:
        return [
            _database_already_satisfied_result(task, database_summary)
            for task in tasks
        ]

    settings = Settings.from_env()
    if isinstance(database_change_plan, dict) and database_change_plan:
        plan = database_change_plan
        agent_note = str(
            plan.get("summary") or "已复用审批通过的数据库计划，直接执行。"
        )
    else:
        agent_note = _invoke_live_database_agent(
            project_plan=project_plan,
            build_task_plan=build_task_plan,
            tasks=tasks,
            database_summary={
                **database_summary,
                "required_schema": required_schema,
                "remaining_gaps": remaining_gaps,
            },
            workspace=workspace,
            selected_skill_names=selected_skill_names,
            on_tool_activity=on_tool_activity,
        )
        plan = _extract_database_change_plan(agent_note)
    execution_context = create_database_execution_context(database_summary)
    risk = classify_database_plan_risk(tasks=tasks, plan=plan)
    approval = request_database_approval_if_needed(
        tasks=tasks,
        plan=plan,
        risk=risk,
        execution_context=execution_context,
    )
    executed_by = {
        "agent": "database-change-agent",
        "mode": "live",
        "model": settings.model_name,
        "source": "database_deep_agent",
        "requiredSkillsLoaded": list(selected_skill_names or []),
    }
    if approval is not None:
        return [
            _database_approval_required_result(
                task=task,
                agent_note=agent_note,
                plan=plan,
                risk=risk,
                approval=approval,
                execution_context=execution_context,
                executed_by=executed_by,
            )
            for task in tasks
        ]

    execution = execute_database_plan(
        plan=plan,
        execution_context=execution_context,
        workspace_root=workspace,
    )
    verification = _verify_database_gaps(tasks, database_summary, workspace)
    if execution.get("status") == "completed" and verification.get("status") == "failed":
        execution = {
            **execution,
            "status": "failed",
            "failure_category": "database_post_verify_failed",
            "failure_reason": verification.get("summary"),
            "post_verification": verification,
        }
    elif execution.get("status") == "completed":
        execution = {**execution, "post_verification": verification}
    return [
        _database_task_result(
            task=task,
            agent_note=agent_note,
            plan=plan,
            risk=risk,
            execution=execution,
            executed_by=executed_by,
        )
        for task in tasks
    ]


def _entity_table_selection_prompt(
    entity: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """构造实体表选型的 Deep Agent 提示词，要求先调用工具读取真实表与列。"""

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
    history = [
        f"{item.get('role')}: {item.get('content')}"
        for item in context.get("messages", [])[-10:]
        if isinstance(item, dict)
        and str(item.get("role") or "").strip()
        and str(item.get("content") or "").strip()
    ]
    target_table = entity_mysql_target_table(entity)
    target_columns = [
        {
            "name": column.get("name"),
            "type": column.get("type"),
            "comment": column.get("comment"),
        }
        for column in (target_table.get("columns") or [])
        if isinstance(column, dict)
    ]
    return (
        "你是应用设计的数据库选表助手。请先用 get_mysql_table_info 工具读取真实数据库："
        "不带 table_name 调用可列出全部表，对候选表带 table_name 调用可读取真实列名；"
        "一切以工具返回为准，不要臆造表名或列名。\n"
        "不要生成 database_change_plan，不要执行 SQL。\n"
        "根据实体字段与真实库表，只输出 1 项你认为最接近的方案：\n"
        "- 存在语义匹配的库表：输出 table_name 与 bindings；"
        "bindings 的 table_column 必须来自该表真实列名，表中没有对应列的实体字段不要绑定；\n"
        "- 不存在合适表：省略 table_name，输出 create_table 并在 note 中说明原因，"
        "同时必须输出 bindings：把实体字段绑定到下方『建议目标表结构』中的列名"
        "（通常列名与实体字段同名，可按语义选择其它列），不要输出结构之外的列名。\n"
        "missing_fields 列出需要为该表新增列的实体字段：仅当该字段没有通过 bindings "
        "覆盖、且语义上确实需要补充时才列出；绑定列名与字段名不同但语义一致时"
        "（如 remark 绑定到 description）视为已覆盖，不要列出。\n"
        f"实体名称：{normalized.get('name') or ''}\n"
        f"实体字段：{json.dumps(fields, ensure_ascii=False)}\n"
        f"建议目标表结构：{json.dumps(target_columns, ensure_ascii=False)}\n"
        f"用户上下文：{json.dumps(context, ensure_ascii=False)}\n"
        f"对话历史：\n{json.dumps(history, ensure_ascii=False)}\n"
        "只输出 JSON：{\"text\": \"给用户的回复\", "
        "\"missing_fields\": [{\"entity_field\": \"需要新增列的实体字段名\"}], "
        "\"suggestions\": [{\"table_name\": \"推荐表名（无合适表时省略）\", "
        "\"create_table\": {\"note\": \"无合适表时说明原因\"}, "
        "\"bindings\": [{\"entity_field\": \"实体字段名\", "
        "\"table_column\": \"真实列名\", \"note\": \"可选说明\"}], "
        "\"note\": \"可选说明\"}]}，不要输出解释文字。"
    )


def entity_design_table_selection_with_agent(
    *,
    entity: dict[str, Any],
    context: dict[str, Any],
    workspace: str | None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> dict[str, Any]:
    """用数据库 Deep Agent 读取真实表/列后生成实体表选型建议。"""

    from app.agents import create_agent_bundle
    # 延迟导入避免 app.agents → services → app.agents 的模块级循环依赖。
    from app.services.entity_design_assist import (
        _filter_create_table_bindings,
        _filter_bindings_to_real_columns,
        _normalize_ai_missing_fields,
        _normalize_suggestions,
    )

    prompt = _entity_table_selection_prompt(entity, context)
    agent_note = invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).database,
        {"messages": [{"role": "user", "content": prompt}]},
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )
    parsed = extract_json_object(agent_note) or {}
    suggestions = _normalize_suggestions(
        "table_selection",
        parsed.get("suggestions") if isinstance(parsed, dict) else None,
    )
    for suggestion in suggestions:
        payload = suggestion.get("payload")
        if isinstance(payload, dict) and payload.get("create_table"):
            payload["create_table"] = entity_mysql_target_table(entity)
            _filter_create_table_bindings(payload, entity)
        if isinstance(payload, dict) and payload.get("table_name"):
            _filter_bindings_to_real_columns(payload, suggestion, workspace)
    text = (
        str(parsed.get("text") or "").strip()
        if isinstance(parsed, dict)
        else ""
    )
    missing_fields = None
    if suggestions:
        payload = suggestions[0].get("payload") or {}
        table_name = str(payload.get("table_name") or "").strip()
        if table_name:
            missing_fields = _normalize_ai_missing_fields(
                entity,
                table_name,
                parsed.get("missing_fields")
                if isinstance(parsed, dict)
                else None,
                workspace,
            )
    history = [
        item
        for item in context.get("messages", [])
        if isinstance(item, dict)
    ][-20:]
    messages = [*history]
    if text:
        messages.append({"role": "assistant", "content": text})
    return {
        "assist_type": "table_selection",
        "text": text,
        "messages": messages[-20:],
        "suggestions": suggestions,
        "missing_fields": missing_fields,
        "source": "ai",
        "note": "",
    }


def _entity_bindings_prompt(
    entity: dict[str, Any],
    context: dict[str, Any],
) -> str:
    """构造“指定表字段映射”的 Deep Agent 提示词，要求读取该表真实列。"""

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
    table_name = str(context.get("table_name") or "").strip()
    history = [
        f"{item.get('role')}: {item.get('content')}"
        for item in context.get("messages", [])[-10:]
        if isinstance(item, dict)
        and str(item.get("role") or "").strip()
        and str(item.get("content") or "").strip()
    ]
    return (
        "你是应用设计的数据库字段映射助手。用户已选定目标表 "
        f"`{table_name}`。请先用 get_mysql_table_info 工具（带 table_name="
        f"{table_name}）读取该表的真实列名，一切以工具返回为准，不要臆造列名。\n"
        "不要生成 database_change_plan，不要执行 SQL。\n"
        "根据实体字段与该表真实列输出字段映射 bindings：bindings 的 table_column "
        "必须来自该表真实列名；表中没有对应列的实体字段不要绑定，改为列入 "
        "missing_fields（仅当语义上确实需要补列时；绑定列名与字段名不同但语义"
        "一致时视为已覆盖，不列入）。\n"
        f"实体名称：{normalized.get('name') or ''}\n"
        f"实体字段：{json.dumps(fields, ensure_ascii=False)}\n"
        f"用户上下文：{json.dumps(context, ensure_ascii=False)}\n"
        f"对话历史：\n{json.dumps(history, ensure_ascii=False)}\n"
        "只输出 JSON：{\"text\": \"给用户的回复\", "
        "\"missing_fields\": [{\"entity_field\": \"需要新增列的实体字段名\"}], "
        "\"suggestions\": [{\"entity_field\": \"实体字段名\", "
        "\"table_column\": \"真实列名\", \"rule\": \"same_name|manual\", "
        "\"note\": \"可选说明\"}]}，不要输出解释文字。"
    )


def entity_design_bindings_with_agent(
    *,
    entity: dict[str, Any],
    context: dict[str, Any],
    workspace: str | None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> dict[str, Any]:
    """用同一个数据库 Deep Agent 读取指定表真实列后生成字段映射建议。"""

    from app.agents import create_agent_bundle
    # 延迟导入避免 app.agents → services → app.agents 的模块级循环依赖。
    from app.services.entity_design_assist import (
        _normalize_ai_missing_fields,
        _normalize_suggestions,
        _real_table_column_names,
    )

    prompt = _entity_bindings_prompt(entity, context)
    agent_note = invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).database,
        {"messages": [{"role": "user", "content": prompt}]},
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )
    parsed = extract_json_object(agent_note) or {}
    suggestions = _normalize_suggestions(
        "bindings",
        parsed.get("suggestions") if isinstance(parsed, dict) else None,
    )
    table_name = str(context.get("table_name") or "").strip()
    # 绑定列必须落在指定表的真实列内，防止模型臆造列名。
    if table_name:
        real_columns = _real_table_column_names(table_name, workspace)
        if real_columns is not None:
            suggestions = [
                suggestion
                for suggestion in suggestions
                if isinstance(suggestion.get("payload"), dict)
                and str(suggestion["payload"].get("table_column") or "").strip()
                in real_columns
            ]
    missing_fields = (
        _normalize_ai_missing_fields(
            entity,
            table_name,
            parsed.get("missing_fields") if isinstance(parsed, dict) else None,
            workspace,
        )
        if table_name
        else None
    )
    text = (
        str(parsed.get("text") or "").strip()
        if isinstance(parsed, dict)
        else ""
    )
    history = [
        item
        for item in context.get("messages", [])
        if isinstance(item, dict)
    ][-20:]
    messages = [*history]
    if text:
        messages.append({"role": "assistant", "content": text})
    return {
        "assist_type": "bindings",
        "text": text,
        "messages": messages[-20:],
        "suggestions": suggestions,
        "missing_fields": missing_fields,
        "source": "ai",
        "note": "",
    }


def _extract_database_change_plan(agent_note: str) -> dict[str, Any]:
    """从 Agent 最终 JSON 中提取数据库变更计划。"""

    payload = extract_json_object(agent_note)
    if not isinstance(payload, dict):
        return {"summary": "Database Agent 未返回合法 JSON。", "statements": [], "task_results": []}
    plan = payload.get("database_change_plan")
    return plan if isinstance(plan, dict) else payload


def _database_failure_result(task: dict[str, Any], database_summary: dict[str, Any]) -> dict[str, Any]:
    """把数据库扫描失败转换为调度器可识别的任务失败。"""

    reason = str(
        database_summary.get("message")
        or database_summary.get("failure_reason")
        or "数据库信息获取失败。"
    )
    return {
        "task_id": task["id"],
        "owner": "database",
        "status": "failed",
        "failure_category": "tool_error",
        "failure_reason": reason,
        "agent_note": reason,
        "changed_files": [],
        "commands": [],
        "change_request": None,
    }


def _database_already_satisfied_result(
    task: dict[str, Any],
    database_summary: dict[str, Any],
) -> dict[str, Any]:
    """构造执行前复查已满足的数据库任务结果。"""

    summary = str(database_summary.get("summary") or "数据库结构已满足任务要求。")
    return {
        "task_id": task["id"],
        "owner": "database",
        "status": "already_satisfied",
        "failure_category": None,
        "failure_reason": None,
        "agent_note": summary,
        "changed_files": [],
        "commands": [],
        "change_request": None,
        "database_execution": {
            "status": "skipped",
            "summary": summary,
            "reason": "schema_gaps_already_satisfied",
        },
        "acceptance_evidence": _database_acceptance_evidence(task, passed=True, summary=summary),
    }


def _database_approval_required_result(
    *,
    task: dict[str, Any],
    agent_note: str,
    plan: dict[str, Any],
    risk: dict[str, Any],
    approval: dict[str, Any],
    execution_context: Any,
    executed_by: dict[str, Any],
) -> dict[str, Any]:
    """构造高危数据库计划待审批结果，调度器会暂停而不是执行 SQL。"""

    summary = f"数据库计划 {database_plan_hash(plan)[:12]} 需要用户审批后执行。"
    return {
        "task_id": task["id"],
        "owner": "database",
        "status": "failed",
        "failure_category": "database_approval_required",
        "failure_reason": summary,
        "agent_note": summary,
        "changed_files": [],
        "commands": [],
        "change_request": None,
        "database_change_plan": plan,
        "database_risk": risk,
        "database_approval": approval,
        "database_execution_context": {
            "schema_hash": execution_context.schema_hash,
            "database": execution_context.database,
        },
        "executed_by": executed_by,
        "raw_agent_note": agent_note,
    }


def _database_task_result(
    *,
    task: dict[str, Any],
    agent_note: str,
    plan: dict[str, Any],
    risk: dict[str, Any],
    execution: dict[str, Any],
    executed_by: dict[str, Any],
) -> dict[str, Any]:
    """合并计划、风险和执行证据为数据库任务结果。"""

    if execution.get("status") == "failed" or execution.get("status") == "error":
        status = "failed"
        failure_category = execution.get("failure_category") or "tool_error"
        failure_reason = execution.get("failure_reason") or "数据库执行失败。"
    else:
        status = "already_satisfied" if execution.get("status") == "skipped" else "completed"
        failure_category = None
        failure_reason = None
    return {
        "task_id": task["id"],
        "owner": "database",
        "status": status,
        "failure_category": failure_category,
        "failure_reason": failure_reason,
        "agent_note": str(execution.get("summary") or plan.get("summary") or agent_note),
        "changed_files": [],
        "commands": [],
        "change_request": None,
        "database_change_plan": plan,
        "database_risk": risk,
        "database_execution": execution,
        "acceptance_evidence": _database_acceptance_evidence(
            task,
            passed=status in {"completed", "already_satisfied"},
            summary=str(execution.get("summary") or plan.get("summary") or agent_note),
        ),
        "executed_by": executed_by,
    }


def _database_acceptance_evidence(
    task: dict[str, Any],
    *,
    passed: bool,
    summary: str,
) -> list[dict[str, Any]]:
    """把确定性数据库复查和审批结果投射为逐项工程验收证据。"""

    return [
        {
            "check_id": check.get("id"),
            "kind": check.get("kind"),
            "status": "passed" if passed else "failed",
            "evidence": summary,
        }
        for check in task.get("acceptance_checks", [])
        if isinstance(check, dict)
    ]


def _verify_database_gaps(
    tasks: list[dict[str, Any]],
    before_summary: dict[str, Any],
    workspace: str | None = None,
) -> dict[str, Any]:
    """执行后重新扫描数据库并确认任务声明的 gaps 已消除。"""

    latest_summary = inspect_mysql_schema(_target_from_tasks(tasks), workspace)
    # 执行后的验收必须重新读取真实 Schema，不能用 SQL 执行返回成功替代结构复核。
    if latest_summary.get("status") != "completed":
        return {
            "status": "failed",
            "summary": str(latest_summary.get("message") or "数据库执行后复查失败。"),
            "remaining_gaps": [],
            "latest_summary": latest_summary,
        }
    required_schema = _required_schema_from_tasks(tasks, before_summary)
    remaining_gaps = diff_database_schema(
        actual_schema=_actual_schema_from_summary(latest_summary),
        required_schema=required_schema,
    )
    return {
        "status": "failed" if remaining_gaps else "completed",
        "summary": (
            f"数据库执行后仍存在 {len(remaining_gaps)} 个目标结构差异。"
            if remaining_gaps
            else "数据库执行后复查通过，目标结构差异已消除。"
        ),
        "remaining_gaps": remaining_gaps,
        "latest_summary": latest_summary,
    }


def _required_schema_from_tasks(
    tasks: list[dict[str, Any]],
    database_summary: dict[str, Any],
) -> dict[str, Any]:
    """从数据库任务中的 gap 还原执行前后可复查的目标 schema。"""

    database = str(database_summary.get("database") or "")
    tables: dict[str, dict[str, Any]] = {}
    for task in tasks:
        scope = task.get("database_scope") if isinstance(task.get("database_scope"), dict) else {}
        database = str(scope.get("database") or database)
        for gap in scope.get("gaps") if isinstance(scope.get("gaps"), list) else []:
            if not isinstance(gap, dict):
                continue
            table_name = str(gap.get("table") or "")
            if not table_name:
                continue
            required = gap.get("required") if isinstance(gap.get("required"), dict) else {}
            if gap.get("kind") == "missing_table" and required.get("columns"):
                tables[table_name] = required
                continue
            table = tables.setdefault(table_name, {"name": table_name, "columns": []})
            if gap.get("column") and required:
                table.setdefault("columns", []).append(required)
    return {"database": database, "tables": list(tables.values())}


def _actual_schema_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    """把数据库摘要投影成 schema diff 使用的 actual_schema。"""

    return {
        "database": summary.get("database"),
        "database_exists": summary.get("database_exists") is not False,
        "tables": list(summary.get("tables") or []),
    }
