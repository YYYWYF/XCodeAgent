"""静态核对页面 Skill 启动顺序和 API 轻量模式的提示边界。"""

import json
import tempfile
import unittest
from unittest.mock import patch

from deepagents.backends import StateBackend

from app.agents.frontend.agent import create_frontend_agent
from app.agents.frontend.execution_scope import FrontendTaskExecutionScope
from app.agents.frontend.generator import _scoped_generation_prompt
from app.agents.frontend.task_packet import API, PAGE


class FrontendSkillLoadingPromptTests(unittest.TestCase):
    """检查实际构造边界的文本，不将脚本调用视为模型行为保证。"""

    def test_page_and_api_prompt_boundaries(self):
        """页面先启动再读规范，API 保留原读取方式和无步骤执行。"""
        with tempfile.TemporaryDirectory() as workspace:
            for kind in (PAGE, API):
                with self.subTest(kind=kind):
                    packets = [{"id": "task", "kind": kind, "implementation_contract": {
                        "frontend_standards": ["FE-API-BOUNDARY"],
                    }}]
                    scope = FrontendTaskExecutionScope(workspace, packets)
                    with patch("app.agents.frontend.agent.create_deep_agent") as create:
                        create_frontend_agent(
                            object(), workspace, user_skills_backend=StateBackend(),
                            agent_memory_backend=StateBackend(), build_scope=scope,
                        )
                    system = create.call_args.kwargs["system_prompt"]
                    with patch("app.agents.frontend.generator.record_prompt") as record:
                        prompt = _scoped_generation_prompt(packets)
                    task_text = json.dumps(packets, ensure_ascii=False, separators=(",", ":"))
                    record.assert_called_once_with(prompt, task_context=task_text)
                    self.assertTrue(prompt.endswith(task_text))
                    for text in (system, prompt):
                        self.assertIn("implementation_contract", text)
                        self.assertIn("task_results", text)
                    for skill in ("frontend-engineering-standards", "frontend-template-modification-boundary"):
                        self.assertEqual(prompt.count(f"{skill}/SKILL.md"), 1)
                    if kind == PAGE:
                        self.assertIn("第一次工具调用必须单独调用 begin_frontend_task", system)
                        self.assertIn("等待其成功返回 Step 1 后", system)
                        self.assertIn("不能将 begin_frontend_task 与其他工具并行调用", system)
                        self.assertIn("等待成功返回 Step 1", prompt)
                        self.assertIn("不得与任何其他工具并发或同批发送", prompt)
                        self.assertIn("仅在进入 Step 1 后，读取", prompt)
                        self.assertIn("Step 1 之前不得尝试读取 Skill 或业务文件", prompt)
                        self.assertIn("不因切换步骤重复读取", prompt)
                        for skill in ("frontend-engineering-standards", "frontend-template-modification-boundary"):
                            self.assertIn(f"/.devagentstudio/builtin-skills/{skill}/SKILL.md", prompt)
                        for step in ("Step 1 只读", "Step 2/3 复用核对结果", "Step 4 只读"):
                            self.assertIn(step, prompt)
                        for boundary in ("不要求读完全部文件", "定向补读已授权精确文件",
                                         "读取失败不得猜测接口或扩大搜索范围"):
                            self.assertIn(boundary, prompt)
                        self.assertLess(prompt.index("等待成功返回 Step 1"),
                                        prompt.index("frontend-engineering-standards/SKILL.md"))
                    else:
                        self.assertIn("平台已初始化任务级权限", system)
                        self.assertIn("开始实施时读取", prompt)
                        self.assertIn("后续任务复用规范", prompt)
                        for control in ("begin_frontend_task", "advance_frontend_step", "complete_frontend_task"):
                            self.assertNotIn(control, system)
                            self.assertNotIn(control, prompt)
                        self.assertNotIn("Step 1", prompt)
