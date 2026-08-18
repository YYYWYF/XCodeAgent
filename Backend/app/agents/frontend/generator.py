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
from app.services.entity_definitions import plan_data_sources
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


def _ui_design_reference_instruction(ui_designs: dict[str, Any] | None) -> str:
    """构造 UI设计稿参考指令：pageId → 设计稿虚拟路径映射，让 agent 还原视觉。

    仿照 _page_template_instruction 的"read_file 读参考代码再生成"模式。设计稿是
    UI确认阶段生成并经用户确认的纯视觉 React+antd+pro-components mockup，落盘在
    /.xcodeagent/ui-design/pages/<page_key>/index.tsx；若 UI 阶段被明确跳过，则返回
    基于 ProductPlan、TechnicalPlan 和模板技能的无设计稿实现指令。前端 agent 处理某个
    page task 时（unit_id = page:<pageId>），按映射 read_file 对应设计稿，高保真
    还原其视觉结构，再把静态数据/无交互换成真实 API/数据层。
    """

    if not isinstance(ui_designs, dict):
        return ""
    if ui_designs.get("confirmation_status") == "skipped":
        return (
            "## UI Design Reference\n"
            "The user explicitly skipped UI design generation. No React visual reference is "
            "available. Generate each page from the ProductPlan, TechnicalPlan, compiled page "
            "implementation contract, and the code-block-template skill. Do not block or fail "
            "the task because a design file is absent; preserve the declared product actions, "
            "routes, permissions, endpoint bindings, and local behavior.\n\n"
        )
    pages = ui_designs.get("pages")
    if not isinstance(pages, list) or not pages:
        return ""
    entries: list[dict[str, str]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        page_id = str(page.get("pageId") or "").strip()
        page_key = str(page.get("page_key") or "").strip()
        if not page_id or not page_key:
            continue
        entries.append(
            {
                "pageId": page_id,
                "designPath": f"/.xcodeagent/ui-design/pages/{page_key}/index.tsx",
                "name": str(page.get("name") or page_id),
            }
        )
    if not entries:
        return ""
    return (
        "## UI Design Reference (MUST READ BEFORE WRITING EACH PAGE)\n"
        "For each frontend page task, the task's unit_id is `page:<pageId>`. Before writing "
        "the page, find the matching pageId in the design reference map below and use "
        "`read_file` on its designPath. The design file is a pure-visual React + antd + "
        "@ant-design/pro-components mockup created and confirmed by the user during the UI "
        "design phase. You MUST faithfully reproduce its visual structure: page layout, "
        "component selection (ProTable / ProForm / ProCard / ProList / ModalForm / "
        "DrawerForm / StepsForm), column definitions, form fields, card sections, and the "
        "overall page composition. The design's visual choices are the PRIMARY reference for "
        "what the page should look like.\n\n"
        "CRITICAL — the design is pure-visual; you MUST adapt it into real business code:\n"
        "- The design uses a static in-memory `dataSource` array. You MUST replace it with "
        "real data fetching: ProTable `request` calling the page's API (from ProjectPlan."
        "api_contracts and the task's endpoint_ids), OR the in-memory mock data layer "
        "pattern from the code-block-template skill when ProjectPlan.data_sources declares "
        "type=static with effective_source=frontend_mock. Never ship the design's raw visual "
        "sample array as the runtime data source.\n"
        "- Elements marked `data-preview-only=\"true\"` are UI-review tooling (for example a "
        "state switcher), not product UI. Omit them from the runtime page.\n"
        "- Implement only the ProductPlan actions identified by `data-action-id`. Follow the "
        "confirmed, deterministically compiled PageImplementationContract actionBindings exactly: endpoint actions call the "
        "declared endpoint, navigation actions route to the declared page, and local/external/sequence "
        "actions use the upstream ProductPlan/UiManifest behavior. Never guess that every control needs an API "
        "or reinterpret a non-endpoint behavior as a TechnicalPlan decision.\n"
        "- The design has no route params. Add React Router params (e.g. :id) and "
        "useSearchParams per the page's path and navigation requirements.\n"
        "- Keep the design's Pro component choices and layout intact; only swap the data "
        "and interaction layer. Do NOT downgrade a ProCard to antd Card, a ProTable to antd "
        "Table, or a ModalForm to antd Modal.\n"
        "- If a design file is missing or unreadable for a pageId, fall back to the "
        "code-block-template skill and ProjectPlan to generate that page; do not block or "
        "fail the task.\n\n"
        f"Design reference map (pageId → designPath):\n"
        f"{json.dumps(entries, ensure_ascii=False, indent=2)}\n\n"
    )


def _frontend_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    page_template: dict[str, Any] | None = None,
    ui_designs: dict[str, Any] | None = None,
) -> str:
    app_name = _app_name_from_plan(project_plan)
    # 直接平铺到根目录，不再嵌套 apps/<app_name>/ 前缀
    frontend_root = "frontend"
    # Static 的正式实现来源固定为前端内存数据模块，不兼容旧 mock 类型。
    data_source_list = plan_data_sources(project_plan)
    has_static_data_source = any(
        isinstance(source, dict)
        and str(source.get("type") or "") == "static"
        for source in data_source_list
    )
    data_source_instruction = (
        "## CRITICAL: Data source is STATIC with effective_source=frontend_mock\n"
        "The data source for this page's entities declares type=static. Implement the approved "
        "API contracts as a frontend in-memory data-access module. This page MUST "
        "use **in-memory mock functions** for all data — do NOT call real backend APIs, do NOT "
        "use `service.get('/api/...')`, and do NOT configure a real backend proxy in vite.config.ts. "
        "Instead, write the mock data layer in `src/apis/<biz>Api.ts` following the page template's "
        "`api.ts` pattern: a module-level in-memory array of fake records, a `delay(ms)` helper to "
        "simulate network latency, and async functions (fetchList/update/delete/...) that filter/"
        "paginate/mutate the in-memory array and return `{ data, success, total }` (list) or "
        "`{ success }` (mutation). The page component imports these functions and calls them from "
        "ProTable's `request` / button handlers, exactly like the template. Keep `service.ts` "
        "untouched. Do NOT modify vite.config.ts to add a mock plugin. Keep all runtime records "
        "inside the API module; page components must never contain standalone business-data arrays. "
        "Only implement operations and fields declared by the approved contracts.\n\n"
        if has_static_data_source
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
        + CODE_GRAPH_TASK_EXECUTION_GUIDANCE
        + "\n\n"
        + _page_template_instruction(page_template)
        + _ui_design_reference_instruction(ui_designs)
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
        "`already_satisfied` only when the exact target state already exists without writing. "
        "The scheduler independently validates every acceptance_check from workspace diffs and "
        "generated source; your natural-language claim is never acceptance evidence. A "
        "semantically similar file at "
        "another path never satisfies a task. Use `failed` plus `failure_category` and "
        "`failure_reason` when implementation could not be completed. Do not use free-form text "
        "outside the final JSON object. The JSON must be syntactically valid: escape every "
        "double quote inside summary text and do not wrap the object in a Markdown fence.\n\n"
        "## Verification boundary\n"
        "Do not run project-level dependency installation, build, lint, typecheck, unit-test, "
        "or dev-server commands during this task. Do not call `pnpm install`, `npm install`, "
        "or `npx tsc`. The outer integration-test phase performs the repository checks after "
        "all owner tasks complete. If a dependency or command appears to be missing, report "
        "it in the final JSON instead of installing dependencies or creating temporary scripts.\n\n"
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
    ui_designs: dict[str, Any] | None = None,
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
                        ui_designs=ui_designs,
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
    ui_designs: dict[str, Any] | None = None,
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
        ui_designs=ui_designs,
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
