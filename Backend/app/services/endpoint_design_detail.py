"""Endpoint API 设计正式产物的详情、准备与保存服务。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.workspace.endpoint_design_documents import (
    endpoint_design_paths,
    endpoint_design_status,
    read_endpoint_design,
    technical_plan_path,
)
from app.services.api_design import (
    ApiDesignError,
    confirm_api_design,
    initial_api_design_payload,
    invalidate_api_design_consumers,
)


class EndpointDesignDetailRequest(BaseModel):
    """校验 Endpoint 设计查询与编辑动作的目标输入。"""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    workspace_root: str = Field(alias="workspaceRoot", min_length=1, max_length=4096)
    api_contract_id: str = Field(alias="apiContractId", min_length=1, max_length=256)
    endpoint_id: str = Field(alias="endpointId", min_length=1, max_length=256)


class EndpointDesignPrepareRequest(EndpointDesignDetailRequest):
    """校验独立字段映射编辑器的准备请求。"""


class EndpointDesignSaveRequest(EndpointDesignDetailRequest):
    """校验独立字段映射保存请求及其乐观并发版本。"""

    draft: dict[str, Any] = Field(default_factory=dict)
    base_revision: str | None = Field(
        default=None,
        alias="baseRevision",
        pattern=r"^[0-9a-f]{32}$",
    )


def read_endpoint_design_detail(request: EndpointDesignDetailRequest) -> dict[str, Any]:
    """读取当前 Endpoint 的状态、正式 JSON 设计和用户可读 Markdown。"""

    workspace = Path(request.workspace_root).expanduser().resolve()
    status = endpoint_design_status(workspace, request.api_contract_id, request.endpoint_id)
    # stale 结果仍允许只读查看，便于用户判断上游变更造成的失效原因。
    design = read_endpoint_design(
        workspace,
        request.api_contract_id,
        request.endpoint_id,
        require_current=False,
    )
    markdown = ""
    if design is not None:
        _json_path, markdown_path = endpoint_design_paths(
            workspace, request.api_contract_id, request.endpoint_id
        )
        try:
            markdown = markdown_path.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            markdown = ""
    return {
        "apiContractId": request.api_contract_id,
        "endpointId": request.endpoint_id,
        "status": status.get("status", "stale"),
        "designed": bool(status.get("designed")),
        "reason": str(status.get("reason") or ""),
        "design": design,
        "markdown": markdown,
    }


def prepare_endpoint_design(request: EndpointDesignPrepareRequest) -> dict[str, Any]:
    """读取 TechnicalPlan 并构造独立 API 映射编辑器的完整输入。"""

    workspace = Path(request.workspace_root).expanduser().resolve()
    project_plan = _read_current_technical_plan(workspace)
    payload = initial_api_design_payload(
        workspace,
        project_plan,
        request.api_contract_id,
        request.endpoint_id,
    )
    existing = read_endpoint_design(
        workspace,
        request.api_contract_id,
        request.endpoint_id,
        require_current=False,
    )
    return {
        "apiContractId": request.api_contract_id,
        "endpointId": request.endpoint_id,
        "payload": payload,
        "artifactRevision": str(existing.get("artifactRevision") or "") if existing else None,
    }


def save_endpoint_design(request: EndpointDesignSaveRequest) -> dict[str, Any]:
    """校验独立编辑器草稿、检查修订冲突并保存当前 Endpoint 映射。"""

    workspace = Path(request.workspace_root).expanduser().resolve()
    project_plan = _read_current_technical_plan(workspace)
    existing = read_endpoint_design(
        workspace,
        request.api_contract_id,
        request.endpoint_id,
        require_current=False,
    )
    current_revision = str(existing.get("artifactRevision") or "") if existing else ""
    requested_revision = str(request.base_revision or "")
    if requested_revision != current_revision:
        raise ApiDesignError("Endpoint 映射已被其他操作修改，请重新加载后再保存。")
    result = confirm_api_design(
        workspace,
        project_plan,
        {
            "action": "confirm",
            "apiContractId": request.api_contract_id,
            "endpointId": request.endpoint_id,
            "draft": request.draft,
        },
    )
    invalidate_api_design_consumers(workspace)
    return {
        "status": "saved",
        "apiContractId": request.api_contract_id,
        "endpointId": request.endpoint_id,
        "artifactRevision": str(result.get("design", {}).get("artifactRevision") or ""),
        "design": result.get("design"),
        "artifacts": result.get("artifacts"),
        "detail": read_endpoint_design_detail(request),
    }


def _read_current_technical_plan(workspace: Path) -> dict[str, Any]:
    """读取当前确认的 TechnicalPlan，拒绝缺失或非对象内容。"""

    path = technical_plan_path(workspace)
    if not path.is_file():
        raise ApiDesignError("缺少已确认的 technical-plan.json，无法配置 API 映射。")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ApiDesignError("当前 technical-plan.json 无法读取。") from exc
    if not isinstance(value, dict):
        raise ApiDesignError("当前 technical-plan.json 格式无效。")
    return value
