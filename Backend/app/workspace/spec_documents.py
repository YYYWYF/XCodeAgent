from __future__ import annotations

import json
from pathlib import Path
import re
from typing import Any

WORKFLOW_ARTIFACT_DIR = ".xcodeagent"
REPOSITORY_ROOT = Path(__file__).resolve().parents[3]
WORKSPACES_BASE = REPOSITORY_ROOT


def workspace_root(state: dict[str, Any]) -> Path:
    workspace = state.get("workspace") or state.get("workspace_path")
    if workspace:
        path = Path(workspace)
        return path.resolve() if path.is_absolute() else WORKSPACES_BASE / path

    project_id = state.get("project_id") or "demo-project"
    return WORKSPACES_BASE / "var" / "workspaces" / str(project_id)


def workflow_artifact_root(state: dict[str, Any]) -> Path:
    return workspace_root(state) / WORKFLOW_ARTIFACT_DIR


def _bullet_items(items: list[str]) -> str:
    return "\n".join(f"- {item}" for item in items)


def _entity_markdown(entity: Any) -> str:
    """把单个实体渲染为 Markdown 列表项和字段表，兼容旧字符串实体。"""

    if not isinstance(entity, dict):
        return f"- 实体 {entity}"
    entity_id = str(entity.get("id") or entity.get("name") or "实体")
    entity_name = str(entity.get("name") or entity_id)
    description = str(entity.get("description") or "")
    lines = [f"- 实体 `{entity_id}` {entity_name}"]
    if description:
        lines.append(f"  - 说明：{description}")
    fields = entity.get("fields")
    if not isinstance(fields, list) or not fields:
        return "\n".join(lines)
    lines.append("  - 需要展示的信息：")
    lines.append("    | 名称 | 说明 |")
    lines.append("    | --- | --- |")
    for field in fields:
        if not isinstance(field, dict):
            continue
        field_label = str(field.get("label") or field.get("name") or "")
        field_description = str(field.get("description") or "")
        lines.append(
            f"    | {field_label} | {field_description} |"
        )
    return "\n".join(lines)


def render_requirement_spec_markdown(spec: dict[str, Any]) -> str:
    """把 RequirementSpec 渲染为用户可编辑的 Markdown 文档。"""

    modules = "\n".join(
        f"- `{module.get('id', 'module')}` {module.get('name', '业务模块')}："
        f"{module.get('description', '待补充模块说明')}（{module.get('priority', 'must')}）"
        for module in spec.get("feature_modules", [])
        if isinstance(module, dict)
    )
    pages = "\n".join(
        f"- `{page.get('path', '/')}` {page.get('name', page.get('id', '业务页面'))}："
        f"{page.get('description', '待补充页面说明')}"
        f"{'；组件：' + '、'.join(str(item) for item in page.get('components', [])) if page.get('components') else ''}"
        for page in spec.get("pages", [])
        if isinstance(page, dict)
    )
    entities = spec.get("entities") if isinstance(spec.get("entities"), list) else []
    entity_blocks = [
        _entity_markdown(entity)
        for entity in entities
        if isinstance(entity, (dict, str)) and str(entity).strip()
    ]
    entities_markdown = "\n".join(entity_blocks) or "- 暂无实体"
    roles = "\n".join(
        f"- `{role.get('id', 'user')}` {role.get('name', '用户')}："
        f"{role.get('description', '使用应用。')}"
        f"{'；权限：' + '、'.join(str(item) for item in role.get('permissions', [])) if role.get('permissions') else ''}"
        for role in spec.get("user_roles", [])
        if isinstance(role, dict)
    )
    flows = "\n".join(
        f"- {flow.get('name', '业务流程')}："
        f"{flow.get('description', '') + '；' if flow.get('description') else ''}"
        f"{' → '.join(str(step) for step in flow.get('steps', []))}"
        for flow in spec.get("business_flows", [])
        if isinstance(flow, dict)
    )
    questions = "\n".join(
        f"- [{question.get('id') or question.get('header') or 'ask_user'}] "
        f"{question.get('question', '请补充需求细节。')}"
        for question in spec.get("clarification_questions", [])
    )
    app_info = spec.get("app_info", {})

    return f"""# {app_info.get('name', '未命名应用')}需求 Spec

## 应用信息

- 名称：{app_info.get('name', '未命名应用')}
- 目标：{app_info.get('target', '生成一个可在本地运行的前后端应用工程。')}
- 确认需求摘要：{spec.get('summary') or spec.get('source_request', '待补充需求摘要')}
- 状态：{spec.get('status', 'draft')}
- 版本：{spec.get('version', '0.1.0')}

## 用户角色

{roles}

## 功能模块

{modules}

## 页面清单

{pages}

## 实体清单

{entities_markdown}

## 业务流程

{flows}

## 待确认问题

{questions or "- 暂无"}
"""


def synchronize_requirement_spec_markdown_datasource_types(
    markdown: str,
    spec: dict[str, Any],
) -> str:
    """只修正 Markdown 数据源类型，保留用户对其余文案的原始编辑。"""

    synchronized = markdown
    sources = spec.get("data_sources")
    if not isinstance(sources, list):
        return synchronized
    for source in sources:
        if not isinstance(source, dict):
            continue
        source_id = str(source.get("id") or "").strip()
        datasource_type = str(source.get("type") or "").strip()
        if not source_id or not datasource_type:
            continue
        pattern = re.compile(
            rf"(^-\s*`{re.escape(source_id)}`[^（\n]*（)[^）]*(）)",
            re.MULTILINE,
        )
        synchronized = pattern.sub(
            lambda match: f"{match.group(1)}{datasource_type}{match.group(2)}",
            synchronized,
            count=1,
        )
    return synchronized


def write_requirement_spec_document(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    path = requirement_spec_markdown_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_requirement_spec_markdown(spec), encoding="utf-8")
    write_requirement_spec_json(state, spec)
    return str(path)


def requirement_spec_markdown_path(state: dict[str, Any]) -> Path:
    existing_path = state.get("requirement_spec_path")
    return (
        Path(existing_path)
        if existing_path and str(existing_path).endswith(".md")
        else workflow_artifact_root(state) / "specs" / "requirement-spec.md"
    )


def edited_requirement_spec_markdown(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str | None:
    path = requirement_spec_markdown_path(state)
    if not path.is_file():
        return None
    content = path.read_text(encoding="utf-8")
    return content if content != render_requirement_spec_markdown(spec) else None


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


def ui_designs_json_path(state: dict[str, Any]) -> Path:
    """返回工作区下 UI设计稿索引 JSON 的路径，与 requirement-spec.json 同目录。"""

    return workflow_artifact_root(state) / "specs" / "ui-designs.json"


def write_ui_designs_json(state: dict[str, Any], ui_designs: dict[str, Any]) -> str:
    """把不含 TSX 正文和 ProductPlan 事实副本的 UI Manifest 落盘。"""

    from app.services.ui_design_manifest import persisted_ui_manifest

    path = ui_designs_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(persisted_ui_manifest(ui_designs), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


def load_ui_designs_json(path: str | Path) -> dict[str, Any]:
    """读取 UI Manifest，并从受控设计目录恢复仅供运行时预览的 TSX。"""

    resolved = Path(path)
    if not resolved.is_file():
        return {}
    try:
        manifest = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(manifest, dict):
        return {}
    ui_root = (resolved.parent.parent / "ui-design").resolve()
    pages = manifest.get("pages")
    if not isinstance(pages, list):
        return manifest
    hydrated_pages: list[dict[str, Any]] = []
    for page in pages:
        if not isinstance(page, dict):
            continue
        hydrated = dict(page)
        code_path = Path(str(page.get("code_path") or ""))
        try:
            candidate = code_path.expanduser().resolve()
            candidate.relative_to(ui_root)
            if candidate.is_file() and candidate.stat().st_size <= 2_000_000:
                hydrated["code"] = candidate.read_text(encoding="utf-8")
        except (OSError, UnicodeError, ValueError):
            pass
        hydrated_pages.append(hydrated)
    return {**manifest, "pages": hydrated_pages}
