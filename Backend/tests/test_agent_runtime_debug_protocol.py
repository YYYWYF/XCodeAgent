from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.protocols import agent_runtime_debug


class AgentRuntimeDebugProtocolTests(unittest.TestCase):
    """验证独立 Runtime 调试动作的 AG-UI 生命周期和安全结果。"""

    def _workspace(self, directory: str) -> Path:
        """构造包含受管标记和 Runtime 入口的最小工作区。"""

        root = Path(directory)
        marker = root / ".xcodeagent/application.json"
        marker.parent.mkdir(parents=True)
        marker.write_text("{}\n", encoding="utf-8")
        runtime = root / "agent-runtime"
        runtime.mkdir()
        (runtime / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
        return root

    def test_capabilities_publish_independent_ag_ui_action(self) -> None:
        """验证健康元数据声明独立启动动作和事件名称。"""

        capabilities = agent_runtime_debug.agent_runtime_debug_capabilities()
        self.assertEqual(capabilities["endpoint"], "/agent-runtime-debug/run")
        self.assertEqual(capabilities["actions"], ["start"])
        self.assertTrue(capabilities["workflowIndependent"])

    def test_start_stream_returns_sanitized_runtime_status(self) -> None:
        """验证启动成功发送完整生命周期且不返回内部环境或地址。"""

        async def collect(workspace: Path) -> list[str]:
            """收集单次测试 AG-UI 流。"""

            with patch.object(
                agent_runtime_debug,
                "launch_agent_runtime_project",
                return_value={
                    "status": "running",
                    "message": "Agent Runtime 已启动并就绪。",
                    "runtime_url": "http://127.0.0.1:19000",
                    "server": {"pid": 12345, "ready": True},
                    "_debug_access": {
                        "runtime_url": "http://127.0.0.1:19000",
                        "gateway_token": "temporary-debug-token",
                    },
                    "_process": object(),
                    "secret": "must-not-leak",
                },
            ):
                stream = agent_runtime_debug.build_agent_runtime_debug_ag_ui_stream(
                    payload={
                        "threadId": "runtime-debug-thread",
                        "runId": "runtime-debug-run",
                        "forwardedProps": {
                            "agentRuntimeDebug": {
                                "action": "start",
                                "workspaceRoot": str(workspace),
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        with tempfile.TemporaryDirectory() as directory:
            payload = "\n".join(asyncio.run(collect(self._workspace(directory))))

        self.assertIn("RUN_STARTED", payload)
        self.assertIn("agent-runtime-debug", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn('"managedModelFallbackAvailable":true', payload)
        self.assertIn('"ready":true', payload)
        self.assertIn('"runtimeUrl":"http://127.0.0.1:19000"', payload)
        self.assertIn('"debugGatewayToken":"temporary-debug-token"', payload)
        self.assertNotIn("must-not-leak", payload)

    def test_start_failure_returns_handled_ag_ui_failure(self) -> None:
        """验证 Runtime 启动失败仍发送结构化失败和正常结束事件。"""

        async def collect(workspace: Path) -> list[str]:
            """收集失败分支的 AG-UI 流。"""

            with patch.object(
                agent_runtime_debug,
                "launch_agent_runtime_project",
                return_value={
                    "status": "failed",
                    "message": "未找到 uv。",
                    "failed_stage": "agent_runtime_validation",
                },
            ):
                stream = agent_runtime_debug.build_agent_runtime_debug_ag_ui_stream(
                    payload={
                        "forwardedProps": {
                            "agentRuntimeDebug": {
                                "action": "start",
                                "workspaceRoot": str(workspace),
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        with tempfile.TemporaryDirectory() as directory:
            payload = "\n".join(asyncio.run(collect(self._workspace(directory))))

        self.assertIn('"status":"failed"', payload)
        self.assertIn('"failedStage":"agent_runtime_validation"', payload)
        self.assertIn("未找到 uv", payload)
        self.assertIn("RUN_FINISHED", payload)

    def test_cleanup_failure_exposes_sanitized_reason(self) -> None:
        """验证调试入口显示安全裁剪后的旧进程清理原因。"""

        async def collect(workspace: Path) -> list[str]:
            """收集带清理详情的失败 AG-UI 流。"""

            with patch.object(
                agent_runtime_debug,
                "launch_agent_runtime_project",
                return_value={
                    "status": "failed",
                    "message": "无法安全停止上一次 Agent Runtime 进程。",
                    "failed_stage": "agent_runtime_cleanup",
                    "prelaunch_cleanup": {
                        "error": "PID 指向的进程不属于当前工作区\nAgent Runtime。"
                    },
                },
            ):
                stream = agent_runtime_debug.build_agent_runtime_debug_ag_ui_stream(
                    payload={
                        "forwardedProps": {
                            "agentRuntimeDebug": {
                                "action": "start",
                                "workspaceRoot": str(workspace),
                            }
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

        with tempfile.TemporaryDirectory() as directory:
            payload = "\n".join(asyncio.run(collect(self._workspace(directory))))

        self.assertIn(
            "无法安全停止上一次 Agent Runtime 进程。 "
            "PID 指向的进程不属于当前工作区 Agent Runtime。",
            payload,
        )


if __name__ == "__main__":
    unittest.main()
