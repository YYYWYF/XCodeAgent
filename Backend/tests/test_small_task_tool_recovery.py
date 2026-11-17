"""验证 SmallTask 沿标准工具循环纠错，不被额外调用预算提前结束。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from app.agents.small_task.agent import create_small_task_agent
from app.agents.small_task.runner import build_small_task_prompt
from app.agents.small_task.scope import small_task_path_scope
from app.agents.workspace_scope import create_workspace_backend


class _ToolRecoveryModel(FakeMessagesListChatModel):
    """使用预设工具决策驱动生产 Agent、工具和文件 backend。"""

    def bind_tools(self, tools, **kwargs):
        """沿用预设消息，同时支持生产 Agent 的工具绑定。"""
        return self


class SmallTaskToolRecoveryTests(unittest.IsolatedAsyncioTestCase):
    """同步和异步入口都应在多次工具错误后继续执行。"""

    async def test_corrected_edit_succeeds_after_old_model_limit(self) -> None:
        """超过十二轮后连续编辑失败，仍可重读并修正参数完成真实修改。"""
        path = "/frontend/src/value.ts"
        original = "".join(f"// line {index}\n" for index in range(12)) + "export const value = 1;\n"
        final = json.dumps({"status": "completed", "summary": "已修正编辑参数并完成修改"})
        # 十二次真实读取耗尽旧额度，再制造两次匹配错误，确认错误不会终止循环。
        responses = [AIMessage(content="", tool_calls=[{
            "name": "read_file", "id": f"read-{index}",
            "args": {"file_path": path, "offset": index, "limit": 1},
        }]) for index in range(12)]
        responses.extend(AIMessage(content="", tool_calls=[{
            "name": "edit_file", "id": f"failed-edit-{index}",
            "args": {"file_path": path, "old_string": f"missing-{index}", "new_string": "value = 2"},
        }]) for index in range(2))
        responses.extend([
            AIMessage(content="", tool_calls=[{
                "name": "read_file", "id": "reread", "args": {"file_path": path},
            }]),
            AIMessage(content="", tool_calls=[{
                "name": "edit_file", "id": "corrected-edit",
                "args": {"file_path": path, "old_string": "value = 1", "new_string": "value = 2"},
            }]),
            AIMessage(content=final),
        ])
        for asynchronous in (False, True):
            with self.subTest(asynchronous=asynchronous), tempfile.TemporaryDirectory() as workspace:
                source = Path(workspace) / path.lstrip("/")
                source.parent.mkdir(parents=True)
                source.write_text(original, encoding="utf-8")
                backend = create_workspace_backend(workspace)
                agent = create_small_task_agent(
                    _ToolRecoveryModel(responses=responses), workspace,
                    user_skills_backend=backend, agent_memory_backend=backend,
                )
                payload = {"messages": [{"role": "user", "content": build_small_task_prompt({
                    "allowedPaths": ["frontend/src/value.ts"], "description": "把 value 改为 2",
                })}]}
                # 局部工具回归只验证工作区，不发送独立的入库率统计。
                with patch("app.agents.auto_dedup_backend.report_inclusion_rate"), small_task_path_scope(["frontend/src/value.ts"]):
                    result = await agent.ainvoke(payload) if asynchronous else agent.invoke(payload)
                tools = [message for message in result["messages"] if isinstance(message, ToolMessage)]
                self.assertEqual([message.tool_call_id for message in tools if message.status == "error"],
                                 ["failed-edit-0", "failed-edit-1"])
                self.assertEqual(tools[-1].tool_call_id, "corrected-edit")
                self.assertEqual(tools[-1].status, "success")
                self.assertEqual(source.read_text(encoding="utf-8"), original.replace("value = 1", "value = 2"))
                self.assertEqual(json.loads(result["messages"][-1].content)["status"], "completed")
