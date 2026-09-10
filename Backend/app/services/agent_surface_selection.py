from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.services.product_plan import validate_product_plan
from app.workspace.product_plan_documents import (
    product_plan_draft_json_path,
    product_plan_draft_markdown_path,
    render_product_plan_markdown,
    write_product_plan_documents,
)
from app.workspace.spec_documents import (
    load_requirement_spec_json,
    requirement_spec_draft_json_path,
)


class SaveAgentSurfaceSelectionRequest(BaseModel):
    """校验待确认 ProductPlan 中单页悬浮智能体的启停选择。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    action: Literal["save"]
    workspace_root: str = Field(alias="workspaceRoot", min_length=1)
    agent_id: str = Field(alias="agentId", min_length=1)
    page_id: str = Field(alias="pageId", min_length=1)
    enabled: bool


def _load_product_plan(path: Path) -> dict[str, Any]:
    """从固定草稿路径读取 ProductPlan JSON，并拒绝非对象内容。"""

    if not path.is_file():
        raise ValueError("尚未生成可编辑的 ProductPlan 草稿。")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("ProductPlan 草稿必须是 JSON 对象。")
    return value


def _find_surface(
    plan: dict[str, Any],
    *,
    agent_id: str,
    page_id: str,
) -> dict[str, Any]:
    """按稳定 Agent/Page 标识定位唯一页面 Surface。"""

    agents = [item for item in plan.get("agents", []) if isinstance(item, dict)]
    agent = next((item for item in agents if item.get("agentId") == agent_id), None)
    if agent is None:
        raise ValueError("待修改的智能体不存在，请刷新需求文档后重试。")
    bindings = [item for item in agent.get("pageActionBindings", []) if isinstance(item, dict)]
    matches = [item for item in bindings if item.get("pageId") == page_id]
    if len(matches) != 1:
        raise ValueError("待修改的页面智能体绑定不存在或不唯一，请刷新后重试。")
    surface = matches[0].get("surface")
    if not isinstance(surface, dict):
        raise ValueError("待修改的页面智能体载体无效，请重新生成 ProductPlan。")
    if surface.get("type") != "floating_panel":
        raise ValueError("只有悬浮问答面板允许通过页面开关启停。")
    return surface


def save_agent_surface_selection_draft(
    request: SaveAgentSurfaceSelectionRequest,
) -> dict[str, Any]:
    """保存单页悬浮智能体选择，并同步 ProductPlan 草稿 JSON 与 Markdown。"""

    workspace = Path(request.workspace_root).expanduser().resolve()
    if not workspace.is_dir():
        raise ValueError("ProductPlan 工作区不存在或不是目录。")
    state: dict[str, Any] = {"workspace": str(workspace)}
    requirement_path = requirement_spec_draft_json_path(state)
    if not requirement_path.is_file():
        raise ValueError("缺少与 ProductPlan 联合确认的 RequirementSpec 草稿。")
    requirement_spec = load_requirement_spec_json(requirement_path)
    if not isinstance(requirement_spec, dict):
        raise ValueError("RequirementSpec 草稿必须是 JSON 对象。")

    plan = _load_product_plan(product_plan_draft_json_path(state))
    if plan.get("confirmation_status") != "pending_user_confirmation":
        raise ValueError("只有待确认的 ProductPlan 才能修改智能体浮窗选择。")
    surface = _find_surface(
        plan,
        agent_id=request.agent_id,
        page_id=request.page_id,
    )
    surface["enabled"] = request.enabled
    errors = validate_product_plan(plan, requirement_spec)
    if errors:
        raise ValueError("智能体浮窗选择未通过 ProductPlan 校验：" + "；".join(errors))

    markdown_path, json_path = write_product_plan_documents(state, plan)
    return {
        "productPlan": plan,
        "artifact": {
            "id": "product_plan",
            "name": Path(markdown_path).name,
            "path": str(product_plan_draft_markdown_path(state)),
            "format": "markdown",
            "content": render_product_plan_markdown(plan),
        },
        "jsonPath": json_path,
    }
