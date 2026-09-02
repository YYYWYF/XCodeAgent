from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.graph.nodes.direct_modification import _workspace_snapshot_for_classification
from app.protocols.direct_modification import (
    DirectModificationInput,
    conversation_capabilities,
    resolve_direct_element_context,
)
from app.protocols.direct_modification_projection import direct_summary, public_direct_state


def test_direct_modification_input_accepts_endpoint_target() -> None:
    """接口自由协作请求应保留当前 API 和 endpoint 标识。"""

    request = DirectModificationInput.model_validate(
        {
            "workspaceRoot": "/tmp/demo",
            "target": {
                "type": "endpoint",
                "apiContractId": "orders",
                "endpointId": "orders.list",
            },
            "changeId": "chg_01",
        }
    )

    assert request.change_id == "chg_01"
    assert request.target is not None
    assert request.target.model_dump(by_alias=True, exclude_none=True) == {
        "type": "endpoint",
        "apiContractId": "orders",
        "endpointId": "orders.list",
    }


def test_direct_modification_input_rejects_incomplete_target() -> None:
    """接口目标缺少稳定标识时应在协议边界拒绝请求。"""

    with pytest.raises(ValidationError, match="apiContractId 和 endpointId"):
        DirectModificationInput.model_validate(
            {
                "workspaceRoot": "/tmp/demo",
                "target": {"type": "endpoint", "apiContractId": "orders"},
            }
        )


def test_direct_modification_input_accepts_element_context() -> None:
    """自由协作请求应保留结构化 DOM 标签和源码行列。"""

    request = DirectModificationInput.model_validate(
        {
            "workspaceRoot": "/tmp/demo",
            "elementContext": {
                "tagName": "BUTTON",
                "sourcePath": "/src/pages/PageAgeEntry/index.tsx",
                "line": 24,
                "column": 7,
            },
        }
    )

    assert request.element_context is not None
    assert request.element_context.model_dump(by_alias=True) == {
        "tagName": "button",
        "sourcePath": "/src/pages/PageAgeEntry/index.tsx",
        "line": 24,
        "column": 7,
    }


@pytest.mark.parametrize(
    "element_context",
    [
        {
            "tagName": "div onclick=bad",
            "sourcePath": "/src/page.tsx",
            "line": 1,
            "column": 1,
        },
        {
            "tagName": "div",
            "sourcePath": "/src/../secret.tsx",
            "line": 1,
            "column": 1,
        },
        {
            "tagName": "div",
            "sourcePath": "/src/page.tsx",
            "line": 0,
            "column": 1,
        },
    ],
)
def test_direct_modification_input_rejects_invalid_element_context(
    element_context: dict[str, object],
) -> None:
    """协议边界应拒绝非法标签、越界路径和非正行列。"""

    with pytest.raises(ValidationError):
        DirectModificationInput.model_validate(
            {"workspaceRoot": "/tmp/demo", "elementContext": element_context}
        )


def test_resolve_direct_element_context_maps_preview_path(tmp_path) -> None:
    """预览 /src/ 路径应解析到当前工作区实际前端文件。"""

    source_file = tmp_path / "frontend" / "src" / "pages" / "PageAgeEntry" / "index.tsx"
    source_file.parent.mkdir(parents=True)
    source_file.write_text("export default null\n", encoding="utf-8")
    request = DirectModificationInput.model_validate(
        {
            "workspaceRoot": str(tmp_path),
            "elementContext": {
                "tagName": "button",
                "sourcePath": "/src/pages/PageAgeEntry/index.tsx",
                "line": 24,
                "column": 7,
            },
        }
    )

    resolved = resolve_direct_element_context(
        request.element_context,
        workspace_root=str(tmp_path),
    )

    assert resolved["workspacePath"] == "frontend/src/pages/PageAgeEntry/index.tsx"
    assert resolved["tagName"] == "button"


def test_resolve_direct_element_context_rejects_missing_file(tmp_path) -> None:
    """预览源码文件不存在时应要求重新选择，而不是静默降级定位。"""

    request = DirectModificationInput.model_validate(
        {
            "workspaceRoot": str(tmp_path),
            "elementContext": {
                "tagName": "div",
                "sourcePath": "/src/missing.tsx",
                "line": 1,
                "column": 1,
            },
        }
    )

    with pytest.raises(ValueError, match="重新选择元素"):
        resolve_direct_element_context(request.element_context, workspace_root=str(tmp_path))


def test_direct_classification_context_and_projection_include_target() -> None:
    """分类上下文和公开投影都应携带当前目标，避免指代丢失。"""

    state = {
        "request": "把这个接口的错误提示改清楚",
        "phase": "conversation",
        "status": "in_progress",
        "change_id": "chg_01",
        "change_target": {
            "type": "endpoint",
            "apiContractId": "orders",
            "endpointId": "orders.list",
        },
        "workspace_snapshot_summary": {"revision": "abc"},
        "element_context": {
            "tagName": "button",
            "sourcePath": "/src/pages/Orders.tsx",
            "line": 12,
            "column": 5,
            "workspacePath": "frontend/src/pages/Orders.tsx",
        },
    }

    context = _workspace_snapshot_for_classification(state)
    summary = direct_summary(state, status="in_progress")
    public_state = public_direct_state(state)

    assert context["currentTarget"] == state["change_target"]
    assert context["currentElement"] == state["element_context"]
    assert summary["changeId"] == "chg_01"
    assert summary["target"] == state["change_target"]
    assert public_state["change_target"] == state["change_target"]


def test_conversation_capabilities_publish_optional_target_contract() -> None:
    """健康能力元数据应公开 target 和 changeId 的兼容边界。"""

    capabilities = conversation_capabilities()

    assert capabilities["targetRequired"] is False
    assert capabilities["target"]["types"]["page"] == ["pageId"]
    assert capabilities["target"]["types"]["endpoint"] == [
        "apiContractId",
        "endpointId",
    ]
    assert capabilities["changeIdSupported"] is True
    assert capabilities["elementContext"]["fields"] == [
        "tagName",
        "sourcePath",
        "line",
        "column",
    ]
