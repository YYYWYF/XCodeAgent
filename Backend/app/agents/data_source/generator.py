from __future__ import annotations

import json
from typing import Any

from app.agents.code_graph_guidance import CODE_GRAPH_TASK_EXECUTION_GUIDANCE
from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def _task_data_source_types(tasks: list[dict[str, Any]]) -> set[str]:
    """从当前派发任务的 source_refs 提取数据源类型，避免读取项目全局来源。"""

    source_types: set[str] = set()
    for task in tasks:
        source_refs = task.get("source_refs")
        if not isinstance(source_refs, dict):
            continue
        designs = source_refs.get("entity_designs")
        if not isinstance(designs, list):
            continue
        for design in designs:
            if isinstance(design, dict) and design.get("data_source_type"):
                source_types.add(str(design["data_source_type"]).strip())
    return source_types


def _data_source_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    source_types = _task_data_source_types(tasks)
    has_database = "database" in source_types
    has_external_api = "external_api" in source_types
    source_boundary = (
        "当前批次包含数据库实体任务：允许按已确认表设计生成持久化对象、Mapper、"
        "Repository、应用服务和 Controller；必须使用 springboot-mybatis-generate Skill。\n"
        if has_database
        else ""
    ) + (
        "当前批次包含外部 API 实体任务：只生成上游 DTO/HTTP Client 或 Gateway、字段映射、"
        "应用服务和内部 Controller；必须使用 springboot-external-api-generate Skill。"
        "禁止生成 Entity/PO、Mapper、Mapper.xml、Repository、迁移、seed SQL 或数据库配置。\n"
        if has_external_api
        else ""
    )
    if not source_boundary:
        source_boundary = (
            "当前任务缺少可识别的数据源 source_refs。仅执行任务自身明确的文件范围；"
            "不要根据 ProjectPlan 的全局 data_sources 推断数据库或外部 API 实现。\n"
        )
    skill_invocation = (
        ("Read `/.xcodeagent/builtin-skills/springboot-mybatis-generate/SKILL.md` "
         "before database implementation.\n" if has_database else "")
        +
        ("Read `/.xcodeagent/builtin-skills/springboot-external-api-generate/SKILL.md` "
         "before external API implementation.\n" if has_external_api else "")
    )
    database_missing_rule = (
        "If a requested database table does not exist, ask the user for its schema or SQL DDL.\n"
        if has_database
        else ""
    )
    return (
        "You are the Data Source Generation Agent in an app-generation workflow.\n"
        "Execute only the approved data-source tasks below. Modify code only within "
        "each task's allowed_paths. Generate only the source-specific layers required by "
        "the current tasks. Obey the confirmed "
        "API contract exactly. ProjectPlan.api_contracts is the only source of model fields; "
        "data_sources.schema_refs select those schemas and must not be expanded with new fields. "
        "If the contract cannot be implemented, return a "
        "change_request instead of silently changing it.\n"
        "Do not modify RequirementSpec, PageDetail, ProjectPlan, API contracts, or "
        "the task DAG directly.\n"
        
        "--- SOURCE-SPECIFIC SKILL INVOCATION ---\n"
        + source_boundary
        + skill_invocation
        + "Do not apply database persistence rules to external API entities. Follow each loaded "
        "Skill only for the matching source type. "
        "The backend runs on Java 8: write Java 8 compatible code only. Do NOT use Java 9+ "
        "syntax or APIs, e.g. `var`, text blocks (\"\"\"), `List.of()`, `Map.of()`, "
        "`String.isBlank()`/`strip()`, `Optional.stream()`, `Stream.toList()`, `record`, "
        "`sealed`, or `switch` expressions/arrow labels. Use classic Java 8 constructs "
        "instead (explicit types, `Arrays.asList()`/`Collections.singletonList()`, "
        "`Optional.ofNullable()`, loop-based/`Collectors.toList()` streaming, classic "
        "switch). "
        + database_missing_rule
        + "After generating the new module files, stop after "
        "implementation and report any missing dependency or command; the outer integration-test "
        "phase owns backend verification.\n"
        "--- END SKILL INVOCATION ---\n"
        "\n"
        "Do not run project-level dependency installation, build, lint, typecheck, unit-test, "
        "or dev-server commands during this task. Do not install dependencies to recover from a "
        "missing command; report the issue in the final JSON instead. Never create temporary "
        "scripts for verification.\n"
        
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n"
        "Treat every allowed_paths entry as relative to virtual root '/'. For example, "
        "app/backend/** means /app/backend/** in filesystem tool calls.\n\n"
        f"{CODE_GRAPH_TASK_EXECUTION_GUIDANCE}\n\n"
        "Return one final JSON object with `task_results`, containing exactly one result per "
        "approved task. Each result must include `task_id`, `status` (`completed`, "
        "`already_satisfied`, or `failed`), and `summary`. Use `already_satisfied` only when "
        "the exact target state already exists without writing. The scheduler independently "
        "validates every acceptance_check from workspace diffs and generated source; "
        "natural-language claims are never acceptance evidence. "
        "A similar file at another path is not "
        "valid evidence. Failed work must include `failure_category` and `failure_reason`.\n\n"
        "The JSON must be syntactically valid: escape every double quote inside summary text "
        "and do not wrap the object in a Markdown fence.\n\n"
        f"Approved data-source tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
        f"BuildTaskPlan summary:\n{json.dumps(build_task_plan.get('summary', {}), ensure_ascii=False, indent=2)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}\n\n"
        "## Verification boundary\n"
        "Do not create temporary scripts or invoke Maven/Gradle/package-manager install, build, "
        "lint, typecheck, unit-test, or dev-server commands from a data-source task. The outer "
        "integration-test phase runs repository checks after all owner tasks complete.\n"
    )


def _invoke_live_data_source_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """使用本次工作流的技能白名单调用数据源 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).data_source,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _data_source_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )


def generate_data_sources_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """通过带技能白名单的 Data Source Deep Agent 执行已批准任务。"""

    if not tasks:
        return []

    settings = Settings.from_env()
    agent_note = _invoke_live_data_source_agent(
        project_plan=project_plan,
        build_task_plan=build_task_plan,
        tasks=tasks,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        on_tool_activity=on_tool_activity,
    )
    return create_agent_task_results(
        tasks,
        agent_note,
        executed_by={
            "agent": "data-source-generation-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "data_source_deep_agent",
            "requiredSkillsLoaded": list(selected_skill_names or []),
        },
        require_structured=True,
    )
