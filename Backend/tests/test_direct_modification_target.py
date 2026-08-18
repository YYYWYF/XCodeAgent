from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.graph.nodes.direct_modification import _workspace_snapshot_for_classification
from app.protocols.direct_modification import DirectModificationInput, conversation_capabilities
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
    }

    context = _workspace_snapshot_for_classification(state)
    summary = direct_summary(state, status="in_progress")
    public_state = public_direct_state(state)

    assert context["currentTarget"] == state["change_target"]
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
