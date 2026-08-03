from __future__ import annotations

import json
from typing import Any

from app.agents.tool_activity_stream import (
    ToolActivityCallback,
    invoke_agent_with_tool_activity,
)
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_results
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def _app_name_from_plan(project_plan: dict[str, Any]) -> str:
    """Extract the application name from ProjectPlan.app.name (defensive)."""
    app = project_plan.get("app") or {}
    if isinstance(app, dict):
        name = app.get("name") or app.get("appName")
        if name:
            return str(name)
    return ""


def _page_template_instruction(page_template: dict[str, Any] | None) -> str:
    """Page template selected by user — have the agent read and use it as reference."""
    if not page_template:
        return ""
    template_id = str(page_template.get("id") or "")
    template_name = str(page_template.get("name") or "")
    template_path = str(page_template.get("sourcePath") or "")
    if not template_path:
        return ""
    return (
        f"## User-Selected Page Template: {template_name} ({template_id})\n"
        f"The user selected a page template for this design target. Before writing any code, "
        f"use read_file on the virtual path `/{template_path}/index.tsx` and optionally "
        f"`/{template_path}/types.ts` and `/{template_path}/api.ts` to study the template's "
        f"component structure, ProTable / ProForm configuration patterns, data types, and "
        f"API integration style. Use these patterns as the primary reference when generating "
        f"the page for this task. Adapt the template structure to match the page's specific "
        f"requirements as described in the task and ProjectPlan, but keep the overall "
        f"component selection, layout conventions, and data-fetching pattern consistent with "
        f"the template.\n\n"
    )


def _frontend_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    page_template: dict[str, Any] | None = None,
) -> str:
    app_name = _app_name_from_plan(project_plan)
    # 直接平铺到根目录，不再嵌套 apps/<app_name>/ 前缀
    frontend_root = "frontend"
    # 判断本次任务的数据源是否含 mock/static：若是，前端用内存 mock 函数提供数据，
    # 不调用真实后端接口，也不在 vite.config.ts 里加 mock 插件。
    mock_source_types = {"mock", "static", "none", ""}
    raw_data_sources = project_plan.get("data_sources")
    data_source_list = raw_data_sources if isinstance(raw_data_sources, list) else []
    has_mock_data_source = any(
        isinstance(source, dict)
        and str(source.get("type") or source.get("source_type") or "").lower() in mock_source_types
        for source in data_source_list
    )
    data_source_instruction = (
        "## CRITICAL: Data source is MOCK — use in-memory mock functions, NOT real API calls\n"
        "The ProjectPlan data_sources for this page declare type=mock/static. This page MUST "
        "use **in-memory mock functions** for all data — do NOT call real backend APIs, do NOT "
        "use `service.get('/api/...')`, and do NOT configure a real backend proxy in vite.config.ts. "
        "Instead, write the mock data layer in `src/apis/<biz>Api.ts` following the page template's "
        "`api.ts` pattern: a module-level in-memory array of fake records, a `delay(ms)` helper to "
        "simulate network latency, and async functions (fetchList/update/delete/...) that filter/"
        "paginate/mutate the in-memory array and return `{ data, success, total }` (list) or "
        "`{ success }` (mutation). The page component imports these functions and calls them from "
        "ProTable's `request` / button handlers, exactly like the template. Keep `service.ts` "
        "untouched (it is not used under mock mode). Do NOT modify vite.config.ts to add a mock "
        "plugin — the in-memory functions are the mock.\n\n"
        if has_mock_data_source
        else ""
    )
    return (
        "You are the Frontend Generation Agent in an app-generation workflow.\n"
        "Execute only the approved frontend tasks below. Modify code only within "
        "each task's allowed_paths. Implement layout, components, interactions, "
        "permissions, API integration, loading/empty/error states. "
        "ProjectPlan.api_contracts is the only source of API fields. Render and submit only "
        "fields declared by the task's endpoint_ids, schema refs, and response_bindings; "
        "do not infer or add frontend-only API fields.\n"
        "Do not modify RequirementSpec, PageDetail, ProjectPlan, API contracts, or "
        "the task DAG. If an API contract or page plan cannot be implemented, "
        "return a change_request instead of silently changing it.\n"
        f"{VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS}\n"
        f"Frontend path convention: all frontend code for this application MUST be placed "
        f"under the virtual path `/{frontend_root}/` (resolved from ProjectPlan.app.name = "
        f"'{app_name}'). Every `src/...` path described in the "
        f"frontend-template-modification-boundary skill is relative to `/{frontend_root}/`, "
        f"so `src/pages/<PageKey>/index.tsx` becomes `/{frontend_root}/src/pages/<PageKey>/"
        f"index.tsx` in filesystem tool calls. Treat every allowed_paths entry as relative "
        f"to virtual root '/'. Do NOT write to `Frontend/src/`, bare `src/`, or `/app/"
        f"frontend/` — those are wrong for this workspace. Before writing, use list_files "
        f"on `/{frontend_root}/src/pages/` to confirm the scaffolded page directories.\n\n"
        + _page_template_instruction(page_template)
        + data_source_instruction
        + "## Required Skills (MUST READ BEFORE WRITING ANY CODE)\n"
        "Before generating or modifying any frontend code, you MUST read the following "
        "two built-in skills with read_file(limit=400) and follow their instructions. "
        "These are mandatory constraints:\n"
        "1. `/.xcodeagent/builtin-skills/frontend-template-modification-boundary/SKILL.md` — "
        "file modification boundary: which files you MUST NOT modify (framework skeleton, "
        "package.json, tailwind.config.js, vite.config.ts, etc.), which are append-only "
        "(menus.ts top-level BIZ_MENUS array, src/apis, src/typings, src/constants, src/hooks, "
        "src/utils, src/components), and where to place page types/constants/hooks/utils/"
        "components. Violating this destroys the template scaffold. READ THIS FIRST.\n"
        "2. `/.xcodeagent/builtin-skills/code-block-template/SKILL.md` — the AUTHORITATIVE "
        "spec for component selection and page assembly. HARD RULE: every page MUST be "
        "built with `@ant-design/pro-components` (ProTable, ProForm, ProFormText, "
        "ProFormSelect, ProList, ProCard, ModalForm, DrawerForm, StepsForm). It is "
        "FORBIDDEN to substitute bare `antd` components for the Pro equivalents — a "
        "dashboard / overview page MUST use ProCard (never antd Card), a list page MUST "
        "use ProTable or ProList, a create/edit dialog MUST use ModalForm or DrawerForm, "
        "a multi-step form MUST use StepsForm. Import Pro components from "
        "`@ant-design/pro-components`, never from `antd`.\n"
        "ON-DEMAND references (read only when you need them for the specific page you are "
        "building, do NOT read all of them up front): `code-block-template/references/"
        "blocks.md` (ProTable/ProForm/ProList/ProCard usage & decision trees) and "
        "`code-block-template/references/page-templates.md` (full page templates); "
        "`react-develop-specification/SKILL.md` (React coding conventions) for style rules "
        "when unsure. Reading these on demand keeps the context small.\n"
        "Only after reading the two required skills above may you start writing code.\n\n"
        "## Required final report\n"
        "Return one JSON object with `task_results`, containing exactly one result for each "
        "approved task. Each result must contain `task_id`, `status` "
        "(`completed`, `already_satisfied`, or `failed`), and `summary`. Use "
        "`already_satisfied` only when every exact target file and acceptance criterion was "
        "verified without writing. Then include `satisfaction_evidence.target_files` with every "
        "exact target path and `satisfaction_evidence.acceptance_criteria` with one object per "
        "criterion: `{criterion_index: 0, status: \"passed\", evidence}`. Use the zero-based "
        "criterion index from the approved task instead of copying the criterion text. A "
        "semantically similar file at "
        "another path never satisfies a task. Use `failed` plus `failure_category` and "
        "`failure_reason` when implementation could not be completed. Do not use free-form text "
        "outside the final JSON object.\n\n"
        "## CRITICAL: Do NOT create temporary script files\n"
        "Do NOT create shell scripts (.sh), Python scripts (.py), JavaScript files (.js/.mjs), "
        "or any other temporary script files to run build or typecheck commands. Instead, use "
        "the `execute` tool directly to run commands like `npx tsc --noEmit`, `pnpm run build`, "
        "`pnpm run dev`, etc. Creating temporary scripts pollutes the workspace with "
        "unnecessary files. The `execute` tool runs commands in the workspace root directory; "
        "prefix frontend commands with `cd frontend &&` when needed.\n\n"
        f"Approved frontend tasks:\n{json.dumps(tasks, ensure_ascii=False, indent=2)}\n\n"
        f"BuildTaskPlan summary:\n{json.dumps(build_task_plan.get('summary', {}), ensure_ascii=False, indent=2)}\n\n"
        f"ProjectPlan context:\n{json.dumps(project_plan, ensure_ascii=False, indent=2)}"
    )


def _invoke_live_frontend_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None,
    selected_skill_names: list[str] | None,
    page_template: dict[str, Any] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> str:
    """使用本次工作流的技能白名单调用前端 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    return invoke_agent_with_tool_activity(
        create_agent_bundle(workspace, selected_skill_names).frontend,
        {
            "messages": [
                {
                    "role": "user",
                    "content": _frontend_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
                        page_template=page_template,
                    ),
                }
            ]
        },
        workspace=workspace,
        on_tool_activity=on_tool_activity,
    )


def generate_frontend_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
    page_template: dict[str, Any] | None = None,
    on_tool_activity: ToolActivityCallback | None = None,
) -> list[dict[str, Any]]:
    """通过带技能白名单的 Frontend Deep Agent 执行已批准任务。"""

    if not tasks:
        return []

    settings = Settings.from_env()
    agent_note = _invoke_live_frontend_agent(
        project_plan=project_plan,
        build_task_plan=build_task_plan,
        tasks=tasks,
        workspace=workspace,
        selected_skill_names=selected_skill_names,
        page_template=page_template,
        on_tool_activity=on_tool_activity,
    )
    return create_agent_task_results(
        tasks,
        agent_note,
        executed_by={
            "agent": "frontend-generation-agent",
            "mode": "live",
            "model": settings.model_name,
            "source": "frontend_deep_agent",
            "requiredSkillsLoaded": list(selected_skill_names or []),
        },
        require_structured=True,
    )
