from __future__ import annotations

import unittest
from types import SimpleNamespace

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import HumanMessage

from app.middleware.direct_modification import (
    DIRECT_MODIFICATION_MODE_MARKER,
    _prepare_direct_model_request,
)


class _FakeModel:
    """提供无需真实 Provider 的最小测试模型。"""

    def __init__(self, **values):
        """保存测试模型的公开配置。"""

        self.values = values

def _request(messages):
    """构造无需真实 Provider 的最小 ModelRequest。"""

    return ModelRequest(
        model=_FakeModel(request_timeout=120.0, max_retries=2),
        messages=messages,
        tools=[
            SimpleNamespace(name="task"),
            SimpleNamespace(name="write_todos"),
            SimpleNamespace(name="read_file"),
            SimpleNamespace(name="edit_file"),
            SimpleNamespace(name="execute"),
        ],
    )


class DirectModificationMiddlewareTests(unittest.TestCase):
    """验证共用 Agent 只在快速模式移除复杂编排工具。"""

    def test_direct_mode_removes_complex_orchestration_tools(self) -> None:
        """快速请求不得向模型暴露 task 和 write_todos。"""

        request = _request([HumanMessage(content=DIRECT_MODIFICATION_MODE_MARKER)])
        prepared = _prepare_direct_model_request(request)

        self.assertEqual(
            [tool.name for tool in prepared.tools],
            ["read_file", "edit_file", "execute"],
        )
        self.assertIs(prepared.model, request.model)

    def test_main_workflow_request_is_not_changed(self) -> None:
        """没有快速标记的主工作流请求必须保持原工具和模型。"""

        request = _request([HumanMessage(content="正式生成任务")])

        self.assertIs(_prepare_direct_model_request(request), request)

if __name__ == "__main__":
    unittest.main()
