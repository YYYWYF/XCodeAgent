"""验证生成与修复 Agent 都实际携带默认导出 mock 规则且保留权限约束。"""

import tempfile
import unittest
from unittest.mock import patch

from deepagents.backends import StateBackend

from app.agents.frontend_test_instructions import FRONTEND_TEST_INSTRUCTIONS
from app.agents.small_task.agent import create_small_task_agent
from app.agents.test_generation.agent import create_test_generation_agent


class FrontendTestInstructionsTests(unittest.TestCase):
    """在 Agent 构造边界核对交给模型的真实系统提示。"""

    def test_generation_and_repair_receive_mock_contract(self) -> None:
        """两种 Agent 共享规则，同时维持生成禁执行和修复路径边界。"""

        factories = (
            (create_test_generation_agent, "app.agents.test_generation.agent", "Do not run commands."),
            (create_small_task_agent, "app.agents.small_task.agent", "packet.allowedPaths"),
        )
        with tempfile.TemporaryDirectory() as workspace:
            for factory, module, boundary in factories:
                with self.subTest(agent=module), patch(f"{module}.create_deep_agent") as create:
                    factory(
                        model=object(), workspace_root=workspace,
                        user_skills_backend=StateBackend(), agent_memory_backend=StateBackend(),
                    )
                    prompt = create.call_args.kwargs["system_prompt"]
                    self.assertIn(FRONTEND_TEST_INSTRUCTIONS, prompt)
                    self.assertIn(boundary, prompt)
                    self.assertIn("__esModule: true, default: { post: jest.fn() }", prompt)
                    self.assertIn("Do not apply this shape blindly", prompt)
                    self.assertIn("Do not change business", prompt)
