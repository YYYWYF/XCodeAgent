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


def _confirmation_status_label(value: Any) -> str:
    """把需求确认状态转换为用户可读的中文标签。"""

    labels = {
        "draft": "草稿",
        "pending_user_input": "待补充",
        "pending_user_confirmation": "待确认",
        "confirmed": "已确认",
    }
    return labels.get(str(value), str(value or "草稿"))


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


def _authorization_markdown(spec: dict[str, Any]) -> str:
    """渲染权限业务候选、默认角色授权和固定系统页面说明。"""

    authorization = spec.get("authorization_requirements")
    if not isinstance(authorization, dict) or authorization.get("enabled") is not True:
        return "- 不涉及应用级资源授权。"

    roles = {
        str(role.get("id") or "").strip(): str(role.get("name") or "").strip()
        for role in spec.get("user_roles", [])
        if isinstance(role, dict) and str(role.get("id") or "").strip()
    }

    def granted_roles(item: dict[str, Any]) -> str:
        """把规则默认授权角色转换为用户可读文本。"""

        role_ids = item.get("defaultGrantedRoleIds")
        if not isinstance(role_ids, list):
            return "待确认"
        labels = [roles.get(str(role_id).strip(), str(role_id).strip()) for role_id in role_ids]
        return "、".join(label for label in labels if label) or "待确认"

    def authorization_items(field_name: str) -> list[Any]:
        """读取权限候选数组，避免损坏草稿渲染时中断整个确认面板。"""

        value = authorization.get(field_name)
        return value if isinstance(value, list) else []

    pages = "\n".join(
        f"- {item.get('name') or '受控页面'}："
        f"{item.get('description', '') or '待补充'}；理由：{item.get('rationale', '') or '待补充'}"
        f"；目标页面：{item.get('targetPageId', '') or '待确认'}"
        f"；默认授权：{granted_roles(item)}"
        f" <!-- ruleId:{item.get('ruleId', '')} -->"
        for item in authorization_items("restrictedPages")
        if isinstance(item, dict)
    )
    operations = "\n".join(
        f"- {item.get('name') or '受控操作'}：{item.get('description', '') or '待补充'}；"
        f"理由：{item.get('rationale', '') or '待补充'}"
        f"；默认授权：{granted_roles(item)}"
        f" <!-- ruleId:{item.get('ruleId', '')} -->"
        for item in authorization_items("restrictedOperations")
        if isinstance(item, dict)
    )
    fixed_page = (
        "- 模板固定页面 `/roles`（`system_authorization_management`）：提供角色、成员与资源关系的运行态管理；"
        "不属于业务页面清单，不进入 ProductPlan 或 UiDesign。"
    )
    initial_admin_role_id = str(authorization.get("initialAdminRoleId") or "").strip()
    initial_admin_role = roles.get(initial_admin_role_id, "待确认")
    return "\n".join(
        [
            "- 应用级资源授权：启用。",
            "- 固定无权行为：页面和操作入口隐藏；直接访问页面或后端 Endpoint 返回 403。",
            f"- 初始系统管理员角色：{initial_admin_role} <!-- initialAdminRoleId:{initial_admin_role_id} -->",
            "- 约束边界：身份认证不自动产生 RBAC 资源；以下仅列出用户需求明确提及的受控业务对象。",
            "",
            "### 受控页面",
            pages or "- 用户需求未提出页面级权限控制。",
            "",
            "### 受控操作",
            operations or "- 用户需求未提出操作级权限控制。",
            "",
            "### 数据权限边界",
            "- 第一阶段不实现数据范围授权。明确的数据授权需求会以 DATA_AUTHORIZATION_NOT_SUPPORTED 阻止需求文档确认；"
            "固定业务查询不因此自动成为数据权限。",
            "",
            "### 系统固定页面",
            fixed_page,
            "",
            "- 权限关系遵循 RBAC 资源模型：本需求确认首次默认角色授权和初始系统管理员角色；运行态成员与角色资源关系可继续动态配置。",
        ]
    )


def _agent_requirements_markdown(spec: dict[str, Any]) -> str:
    """把产品级业务智能体需求渲染为可编辑 Markdown。"""

    requirements = spec.get("agent_requirements")
    if not isinstance(requirements, list) or not requirements:
        return "- 当前需求不包含业务智能体。"
    blocks: list[str] = []
    for agent in requirements:
        if not isinstance(agent, dict):
            continue
        capabilities = agent.get("capabilities")
        entry_page_ids = agent.get("entryPageIds")
        boundaries = agent.get("boundaries")
        capability_text = (
            "、".join(str(item) for item in capabilities)
            if isinstance(capabilities, list) and capabilities
            else "待补充"
        )
        entry_page_text = (
            "、".join(f"`{item}`" for item in entry_page_ids)
            if isinstance(entry_page_ids, list) and entry_page_ids
            else "应用级入口"
        )
        boundary_text = (
            "、".join(str(item) for item in boundaries)
            if isinstance(boundaries, list) and boundaries
            else "暂无明确限制"
        )
        blocks.extend(
            [
                f"- `{agent.get('agentId', '')}` {agent.get('name', '未命名智能体')}",
                f"  - 职责：{agent.get('purpose', '待补充')}",
                f"  - 核心能力：{capability_text}",
                f"  - 入口页面：{entry_page_text}",
                f"  - 交互方式：{agent.get('interactionMode', '待补充')}",
                f"  - 业务边界：{boundary_text}",
            ]
        )
    return "\n".join(blocks) or "- 当前需求不包含业务智能体。"


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
        f"{'；系统角色' if role.get('isSystemRole') else ''}"
        f"{'；初始系统管理员' if role.get('isInitialAdminRole') else ''}"
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
- 状态：{_confirmation_status_label(spec.get('confirmation_status') or spec.get('status'))}
- 版本：{spec.get('version', '0.1.0')}

## 业务参与者（非授权角色）

{roles}

## 权限需求

{_authorization_markdown(spec)}

## 功能模块

{modules}

## 页面清单

{pages}

## 智能体需求

{_agent_requirements_markdown(spec)}

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


def synchronize_requirement_spec_markdown_confirmation_status(
    markdown: str,
    spec: dict[str, Any],
) -> str:
    """确认需求时只刷新 Markdown 状态行，保留用户对正文的直接编辑。"""

    status_line = (
        "- 状态："
        f"{_confirmation_status_label(spec.get('confirmation_status') or spec.get('status'))}"
    )
    return re.sub(r"^- 状态：.*$", status_line, markdown, count=1, flags=re.MULTILINE)


def write_requirement_spec_document(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    """把已确认的 RequirementSpec 写入当前状态指定的 Markdown 与 JSON。"""

    path = requirement_spec_markdown_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_requirement_spec_markdown(spec), encoding="utf-8")
    write_requirement_spec_json(state, spec)
    return str(path)


def requirement_spec_markdown_path(state: dict[str, Any]) -> Path:
    """返回当前状态关联的需求 Markdown 路径。"""

    existing_path = state.get("requirement_spec_path")
    return (
        Path(existing_path)
        if existing_path and str(existing_path).endswith(".md")
        else workflow_artifact_root(state) / "specs" / "requirement-spec.md"
    )


def _is_requirement_spec_draft_path(
    state: dict[str, Any],
    path: Path,
) -> bool:
    """判断路径是否位于当前工作区的需求草稿目录。"""

    draft_root = (workflow_artifact_root(state) / "drafts").resolve()
    try:
        path.resolve().relative_to(draft_root)
    except ValueError:
        return False
    return True


def requirement_spec_draft_markdown_path(state: dict[str, Any]) -> Path:
    """返回待确认 RequirementSpec 的 Markdown 草稿路径。"""

    existing_path = state.get("requirement_spec_path")
    candidate = Path(existing_path) if existing_path else None
    if (
        candidate
        and candidate.suffix == ".md"
        and _is_requirement_spec_draft_path(state, candidate)
    ):
        return candidate
    return workflow_artifact_root(state) / "drafts" / "specs" / "requirement-spec.md"


def confirmed_requirement_spec_markdown_path(state: dict[str, Any]) -> Path:
    """返回用户确认后正式 RequirementSpec Markdown 的路径。"""

    return workflow_artifact_root(state) / "specs" / "requirement-spec.md"


def confirmed_requirement_spec_json_path(state: dict[str, Any]) -> Path:
    """返回用户确认后正式 RequirementSpec JSON 的路径。"""

    return workflow_artifact_root(state) / "specs" / "requirement-spec.json"


def write_requirement_spec_draft_document(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    """把模型生成的 RequirementSpec 写入待确认草稿目录。"""

    path = requirement_spec_draft_markdown_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_requirement_spec_markdown(spec), encoding="utf-8")
    write_requirement_spec_draft_json(state, spec)
    return str(path)


def write_confirmed_requirement_spec_document(
    state: dict[str, Any],
    spec: dict[str, Any],
    markdown: str | None = None,
) -> str:
    """将已确认需求从草稿提升为正式 Markdown/JSON，并清理生成的草稿副本。"""

    markdown_path = confirmed_requirement_spec_markdown_path(state)
    json_path = confirmed_requirement_spec_json_path(state)
    markdown_path.parent.mkdir(parents=True, exist_ok=True)
    confirmed_markdown = synchronize_requirement_spec_markdown_confirmation_status(
        markdown if markdown is not None else render_requirement_spec_markdown(spec),
        spec,
    )
    markdown_path.write_text(confirmed_markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    draft_markdown_path = requirement_spec_draft_markdown_path(state)
    draft_json_path = requirement_spec_draft_json_path(state)
    for draft_path in (draft_markdown_path, draft_json_path):
        try:
            draft_path.unlink()
        except FileNotFoundError:
            pass
    return str(markdown_path)


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
    """返回当前状态关联的需求 JSON 路径。"""

    existing_path = state.get("requirement_spec_json_path")
    return (
        Path(existing_path)
        if existing_path
        else workflow_artifact_root(state) / "specs" / "requirement-spec.json"
    )


def requirement_spec_draft_json_path(state: dict[str, Any]) -> Path:
    """返回待确认 RequirementSpec 的内部 JSON 草稿路径。"""

    existing_path = state.get("requirement_spec_json_path")
    candidate = Path(existing_path) if existing_path else None
    if (
        candidate
        and candidate.suffix == ".json"
        and _is_requirement_spec_draft_path(state, candidate)
    ):
        return candidate
    return workflow_artifact_root(state) / "drafts" / "specs" / "requirement-spec.json"


def write_requirement_spec_draft_json(
    state: dict[str, Any],
    spec: dict[str, Any],
) -> str:
    """把待确认 RequirementSpec 的内部结构化状态写入草稿 JSON。"""

    path = requirement_spec_draft_json_path(state)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(spec, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return str(path)


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
