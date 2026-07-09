from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def workspace_root(state: dict[str, Any]) -> Path:
    workspace = state.get("workspace") or state.get("workspace_path")
    if workspace:
        return Path(workspace)

    project_id = state.get("project_id") or "demo-project"
    return Path("var") / "workspaces" / str(project_id)


def _bullet_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def render_requirement_spec_markdown(spec: dict[str, Any]) -> str:
    modules = "\n".join(
        f"- `{module['id']}` {module['name']}：{module['description']}（{module['priority']}）"
        for module in spec["feature_modules"]
    )
    pages = "\n".join(
        f"- `{page['path']}` {page['name']}：{page['description']}"
        for page in spec["pages"]
    )
    data_sources = "\n".join(
        f"- `{source['id']}` {source['name']}（{source['type']}）：{source['description']}"
        for source in spec["data_sources"]
    )
    roles = "\n".join(
        f"- `{role['id']}` {role['name']}：{role['description']}"
        for role in spec["user_roles"]
    )
    flows = "\n".join(
        f"- {flow['name']}：{' → '.join(flow['steps'])}"
        for flow in spec["business_flows"]
    )
    questions = "\n".join(
        f"- [{question.get('id') or question.get('header') or 'ask_user'}] "
        f"{question.get('question', '请补充需求细节。')}"
        f"{' 默认：' + question['default_assumption'] if question.get('default_assumption') else ''}"
        for question in spec.get("clarification_questions", [])
    )
    assumptions = _bullet_items(spec.get("assumptions", [])) or "- 暂无"

    return f"""# {spec['app_info']['name']}需求 Spec

## 应用信息

- 名称：{spec['app_info']['name']}
- 目标：{spec['app_info']['target']}
- 确认需求摘要：{spec['source_request']}
- 状态：{spec['status']}
- 版本：{spec['version']}

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

{_bullet_items(spec['acceptance_criteria'])}

## 待确认问题

{questions or "- 暂无"}

## 默认假设

{assumptions}
"""


def write_requirement_spec_document(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    specs_dir = workspace_root(state) / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    path = specs_dir / "requirement-spec.md"
    path.write_text(render_requirement_spec_markdown(spec), encoding="utf-8")
    write_requirement_spec_json(state, spec)
    return str(path)


def requirement_spec_json_path(state: dict[str, Any]) -> Path:
    return workspace_root(state) / "specs" / "requirement-spec.json"


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
