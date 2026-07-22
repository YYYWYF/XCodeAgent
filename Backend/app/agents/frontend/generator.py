from __future__ import annotations

import json
from typing import Any

from app.agents.messages import last_agent_text
from app.config import Settings
from app.services.build_result_coordinator import create_agent_task_result
from app.workspace.virtual_paths import VIRTUAL_WORKSPACE_PATH_INSTRUCTIONS


def _app_name_from_plan(project_plan: dict[str, Any]) -> str:
    """Extract the application name from ProjectPlan.app.name (defensive)."""
    app = project_plan.get("app") or {}
    if isinstance(app, dict):
        name = app.get("name") or app.get("appName")
        if name:
            return str(name)
    return ""


def _frontend_generation_prompt(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
) -> str:
    app_name = _app_name_from_plan(project_plan)
    # 直接平铺到根目录，不再嵌套 apps/<app_name>/ 前缀
    frontend_root = "frontend"
    return (
        "You are the Frontend Generation Agent in an app-generation workflow.\n"
        "Execute only the approved frontend tasks below. Modify code only within "
        "each task's allowed_paths. Implement layout, components, interactions, "
        "permissions, API integration, loading/empty/error states, and page tests. "
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
        "## Required Skills (MUST READ BEFORE WRITING ANY CODE)\n"
        "Before generating or modifying any frontend code, you MUST read the following "
        "two built-in skills with read_file(limit=400) and follow their instructions. "
        "These are mandatory constraints:\n"
        "1. `/.xcodeagent/builtin-skills/frontend-template-modification-boundary/SKILL.md` — "
        "file modification boundary: which files you MUST NOT modify (framework skeleton, "
        "package.json, tailwind.config.js, vite.config.ts, etc.), which are append-only "
        "(menus.ts firstLevel.children, src/apis, src/typings, src/constants, src/hooks, "
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
        "## CRITICAL: Do NOT create temporary script files\n"
        "Do NOT create shell scripts (.sh), Python scripts (.py), JavaScript files (.js/.mjs), "
        "or any other temporary script files to run build commands. Instead, use the "
        "`terminal.exec` tool directly to execute commands like `pnpm run build`, `pnpm run dev`, "
        "etc. Creating temporary scripts pollutes the workspace with unnecessary files.\n\n"
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
) -> str:
    """使用本次工作流的技能白名单调用前端 Deep Agent。"""

    # 延迟创建可确保 Agent 的工作区和技能权限只属于本次运行。
    from app.agents import create_agent_bundle

    result = create_agent_bundle(workspace, selected_skill_names).frontend.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": _frontend_generation_prompt(
                        project_plan=project_plan,
                        build_task_plan=build_task_plan,
                        tasks=tasks,
                    ),
                }
            ]
        }
    )
    return last_agent_text(result)


def generate_frontend_with_deep_agent(
    *,
    project_plan: dict[str, Any],
    build_task_plan: dict[str, Any],
    tasks: list[dict[str, Any]],
    workspace: str | None = None,
    selected_skill_names: list[str] | None = None,
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
    )
    return [
        create_agent_task_result(
            task,
            agent_note,
            executed_by={
                "agent": "frontend-generation-agent",
                "mode": "live",
                "model": settings.model_name,
                "source": "frontend_deep_agent",
                "requiredSkillsLoaded": list(selected_skill_names or []),
            },
        )
        for task in tasks
    ]
