from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.code_review_repair.agent import create_code_review_repair_agent
from app.agents.code_review_repair.repairer import (
    build_code_review_repair_prompt,
    invoke_code_review_repair_agent,
)


def _issue(*, repair_actions: list[str] | None = None) -> dict:
    """构造测试用的前端审查问题。"""

    return {
        "id": "frontend-review-1",
        "side": "frontend",
        "rule_id": "frontend-review",
        "severity": "high",
        "title": "前端问题",
        "summary": "按 Skill 修复问题。",
        "file": "frontend/package.json",
        "line": 1,
        "repair_actions": repair_actions or [],
    }


class CodeReviewRepairAgentTests(unittest.TestCase):
    """验证修复 Agent 的 pnpm 能力按问题包显式授权。"""

    def test_default_agent_does_not_register_pnpm_tool(self) -> None:
        """普通问题使用的默认 Agent 不得注册 pnpm 安装工具。"""

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.agents.code_review_repair.agent.create_deep_agent",
            return_value="agent",
        ) as create_agent, patch(
            "app.agents.code_review_repair.agent.create_code_review_pnpm_install_tool"
        ) as create_pnpm_tool:
            result = create_code_review_repair_agent("model", workspace)

        self.assertEqual(result, "agent")
        self.assertEqual(create_agent.call_args.kwargs["tools"], [])
        self.assertNotIn(
            "pnpm_install_frontend",
            create_agent.call_args.kwargs["system_prompt"],
        )
        create_pnpm_tool.assert_not_called()

    def test_authorized_agent_registers_pnpm_tool(self) -> None:
        """依赖问题专用 Agent 必须且只能注册固定 pnpm 工具。"""

        with tempfile.TemporaryDirectory() as workspace, patch(
            "app.agents.code_review_repair.agent.create_deep_agent",
            return_value="agent",
        ) as create_agent, patch(
            "app.agents.code_review_repair.agent.create_code_review_pnpm_install_tool",
            return_value="pnpm-tool",
        ) as create_pnpm_tool:
            create_code_review_repair_agent(
                "model",
                workspace,
                allow_pnpm_install=True,
            )

        self.assertEqual(create_agent.call_args.kwargs["tools"], ["pnpm-tool"])
        self.assertIn(
            "pnpm_install_frontend",
            create_agent.call_args.kwargs["system_prompt"],
        )
        create_pnpm_tool.assert_called_once_with(workspace)

    def test_invocation_selects_agent_from_repair_actions(self) -> None:
        """调用层只能为显式声明 pnpm_install 的问题选择安装能力。"""

        bundle = SimpleNamespace(
            code_review_repair="file-agent",
            code_review_repair_with_pnpm="pnpm-agent",
        )
        selected_agents: list[str] = []

        def invoke(agent, *_args, **_kwargs) -> str:
            """记录实际选择的 Agent 并返回合法修复结果。"""

            selected_agents.append(agent)
            return json.dumps(
                {
                    "status": "completed",
                    "summary": "done",
                    "attempted_issue_ids": ["frontend-review-1"],
                    "changed_files": ["frontend/package.json"],
                    "failure_reason": None,
                }
            )

        with patch("app.agents.create_agent_bundle", return_value=bundle), patch(
            "app.agents.code_review_repair.repairer.invoke_agent_with_tool_activity",
            side_effect=invoke,
        ), patch(
            "app.agents.code_review_repair.repairer.read_pnpm_install_evidence",
            return_value=None,
        ):
            invoke_code_review_repair_agent(
                issues=[_issue()],
                build_failures=[],
                attempt=1,
                max_attempts=3,
                workspace=None,
            )
            invoke_code_review_repair_agent(
                issues=[_issue(repair_actions=["pnpm_install"])],
                build_failures=[],
                attempt=1,
                max_attempts=3,
                workspace=None,
            )

        self.assertEqual(selected_agents, ["file-agent", "pnpm-agent"])

    def test_prompt_does_not_offer_pnpm_for_plain_issue(self) -> None:
        """普通问题任务包不得提示模型执行安装命令。"""

        prompt = build_code_review_repair_prompt(
            issues=[_issue()],
            build_failures=[],
            attempt=1,
            max_attempts=3,
        )

        self.assertIn("authorizes no package installation action", prompt)
        self.assertNotIn("call pnpm_install_frontend", prompt)


if __name__ == "__main__":
    unittest.main()
