from __future__ import annotations

import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from deepagents.middleware.filesystem import _check_fs_permission

from app.agents.revision_investigator.agent import (
    RevisionInvestigatorMiddleware,
    _read_only_tools,
    create_revision_investigator_agent,
    revision_investigator_prompt,
)


class RevisionInvestigatorTests(unittest.TestCase):
    """验证二次修改调查 Agent 的只读能力和结构化输入。"""

    def test_agent_hard_filters_to_read_only_navigation_tools(self) -> None:
        """模型工具列表必须移除写入、执行、待办和委派能力。"""

        tools = [
            SimpleNamespace(name=name)
            for name in (
                "ls",
                "read_file",
                "glob",
                "grep",
                "write_file",
                "edit_file",
                "execute",
                "task",
                "write_todos",
            )
        ]

        self.assertEqual(
            [tool.name for tool in _read_only_tools(tools)],
            ["ls", "read_file", "glob", "grep"],
        )
        self.assertEqual(
            RevisionInvestigatorMiddleware._ALLOWED_TOOLS,
            {"ls", "read_file", "glob", "grep"},
        )

    def test_agent_uses_read_only_workspace_permissions(self) -> None:
        """Agent 工厂必须绑定只读工作区权限和独立身份。"""

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.agents.revision_investigator.agent.create_deep_agent",
            side_effect=lambda **kwargs: kwargs,
        ):
            agent = create_revision_investigator_agent(
                "model",
                workspace_root=workspace,
            )

        self.assertEqual(agent["name"], "revision-investigator-agent")
        self.assertEqual(len(agent["middleware"]), 2)
        self.assertEqual(agent["middleware"][1].run_limit, 6)
        self.assertEqual(
            _check_fs_permission(
                agent["permissions"],
                "read",
                "/.xcodeagent/specs/requirement-spec.json",
            ),
            "allow",
        )
        self.assertEqual(
            _check_fs_permission(agent["permissions"], "write", "/README.md"),
            "deny",
        )

    def test_prompt_exposes_fast_failure_and_current_target(self) -> None:
        """慢路径必须看到快速分类失败原因和当前页面目标。"""

        prompt = revision_investigator_prompt(
            request="把这个按钮改成提交订单",
            conversation_summary="当前正在订单详情页",
            fast_decision={
                "route": "clarification",
                "confidence": 0.42,
                "reason": "语义与实现边界不明确",
            },
            current_target={"type": "page", "pageId": "order-detail"},
        )

        self.assertIn("Fast router result", prompt)
        self.assertIn("语义与实现边界不明确", prompt)
        self.assertIn('"pageId": "order-detail"', prompt)
        self.assertIn("formal_revision", prompt)
        self.assertIn("implementation_fix", prompt)


if __name__ == "__main__":
    unittest.main()
