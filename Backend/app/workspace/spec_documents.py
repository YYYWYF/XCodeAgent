from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WORKFLOW_ARTIFACT_DIR = ".xcodeagent"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]


def workspace_root(state: dict[str, Any]) -> Path:
    workspace = state.get("workspace") or state.get("workspace_path")
    if workspace:
        path = Path(workspace)
        return path if path.is_absolute() else REPOSITORY_ROOT / path

    project_id = state.get("project_id") or "demo-project"
    return REPOSITORY_ROOT / "var" / "workspaces" / str(project_id)


def workflow_artifact_root(state: dict[str, Any]) -> Path:
    return workspace_root(state) / WORKFLOW_ARTIFACT_DIR


def _bullet_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_requirement_spec_markdown(spec: dict[str, Any]) -> str:
    modules = "\n".join(
        f"- `{module.get('id', 'module')}` {module.get('name', '业务模块')}："
        f"{module.get('description', '待补充模块说明')}（{module.get('priority', 'must')}）"
        for module in spec.get("feature_modules", [])
        if isinstance(module, dict)
    )
    pages = "\n".join(
        f"- `{page.get('path', '/')}` {page.get('name', page.get('id', '业务页面'))}："
        f"{page.get('description', '待补充页面说明')}"
        for page in spec.get("pages", [])
        if isinstance(page, dict)
    )
    data_sources = "\n".join(
        f"- `{source.get('id', 'source')}` {source.get('name', '业务数据源')}"
        f"（{source.get('type', 'mock')}）：{source.get('description', '待补充数据源说明')}"
        for source in spec.get("data_sources", [])
        if isinstance(source, dict)
    )
    roles = "\n".join(
        f"- `{role.get('id', 'user')}` {role.get('name', '用户')}："
        f"{role.get('description', '使用应用。')}"
        for role in spec.get("user_roles", [])
        if isinstance(role, dict)
    )
    flows = "\n".join(
        f"- {flow.get('name', '业务流程')}：{' → '.join(str(step) for step in flow.get('steps', []))}"
        for flow in spec.get("business_flows", [])
        if isinstance(flow, dict)
    )
    questions = "\n".join(
        f"- [{question.get('id') or question.get('header') or 'ask_user'}] "
        f"{question.get('question', '请补充需求细节。')}"
        f"{' 默认：' + question['default_assumption'] if question.get('default_assumption') else ''}"
        for question in spec.get("clarification_questions", [])
    )
    assumptions = _bullet_items(spec.get("assumptions", [])) or "- 暂无"

    app_info = spec.get("app_info", {})

    return f"""# {app_info.get('name', '未命名应用')}需求 Spec

## 应用信息

- 名称：{app_info.get('name', '未命名应用')}
- 目标：{app_info.get('target', '生成一个可在本地运行的前后端应用工程。')}
- 确认需求摘要：{spec.get('source_request', spec.get('summary', '待补充需求摘要'))}
- 状态：{spec.get('status', 'draft')}
- 版本：{spec.get('version', '0.1.0')}

## 用户角色

{roles}

## 功能模块

{modules}

## 页面清单

{pages}

## 数据源清单

{data_sources}

## 业务流程

{flows}

## 验收标准

{_bullet_items(spec.get('acceptance_criteria', []))}

## 待确认问题

{questions or "- 暂无"}

## 默认假设

{assumptions}
"""


def write_requirement_spec_document(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    existing_path = state.get("requirement_spec_path")
    path = (
        Path(existing_path)
        if existing_path and str(existing_path).endswith(".md")
        else workflow_artifact_root(state) / "specs" / "requirement-spec.md"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_requirement_spec_markdown(spec), encoding="utf-8")
    write_requirement_spec_json(state, spec)
    return str(path)


def requirement_spec_json_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("requirement_spec_json_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "specs" / "requirement-spec.json"
    )


def write_requirement_spec_json(state: dict[str, Any], spec: dict[str, Any]) -> str:
    path = requirement_spec_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_requirement_spec_json(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))
