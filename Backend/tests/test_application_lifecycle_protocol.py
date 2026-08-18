from __future__ import annotations

import asyncio
import json
import tempfile
import unittest

from app.protocols.application_lifecycle import (
    application_lifecycle_capabilities,
    build_application_lifecycle_ag_ui_stream,
)
from app.services.application_lifecycle import application_lifecycle_path


class ApplicationLifecycleProtocolTests(unittest.TestCase):
    """验证独立 lifecycle 端点保持完整 AG-UI 动作契约。"""

    def test_capabilities_publish_dedicated_endpoint(self) -> None:
        """能力元数据应发布语义明确的独立 AG-UI 地址。"""

        capability = application_lifecycle_capabilities()

        self.assertEqual(capability["endpoint"], "/application-lifecycle/run")
        self.assertEqual(capability["transport"], "ag-ui-sse")
        self.assertEqual(capability["stateSnapshotKey"], "applicationLifecycle")
        self.assertEqual(
            capability["actions"],
            ["create", "get", "complete_template_generation"],
        )

    def test_create_action_emits_complete_ag_ui_lifecycle(self) -> None:
        """独立端点创建状态时应发送事件、快照和完成事件。"""

        with tempfile.TemporaryDirectory() as directory:
            stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "lifecycle-thread",
                    "runId": "lifecycle-run",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "create",
                            "workspaceRoot": directory,
                            "application": {
                                "id": "app-1",
                                "appName": "任务中心",
                            },
                        }
                    },
                }
            )

            async def collect() -> str:
                """消费独立 lifecycle 事件流并合并文本。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())
            saved = json.loads(
                application_lifecycle_path(directory).read_text(encoding="utf-8")
            )

        self.assertIn("application-lifecycle", frames)
        self.assertIn("RUN_STARTED", frames)
        self.assertIn("STATE_SNAPSHOT", frames)
        self.assertIn("RUN_FINISHED", frames)
        self.assertEqual(saved["initialization"]["threadId"], "lifecycle-thread")

    def test_create_action_rejects_another_application_without_writing(self) -> None:
        """创建动作命中其他应用时应失败，并保持原生命周期文件不变。"""

        with tempfile.TemporaryDirectory() as directory:
            original_stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "lifecycle-thread",
                    "runId": "lifecycle-run",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "create",
                            "workspaceRoot": directory,
                            "application": {"id": "app-1", "appName": "原应用"},
                        }
                    },
                }
            )

            async def collect_original() -> None:
                """创建测试中的原始生命周期。"""

                [frame async for frame in original_stream]

            asyncio.run(collect_original())

            conflicting_stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "new-thread",
                    "runId": "new-run",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "create",
                            "workspaceRoot": directory,
                            "application": {"id": "app-2", "appName": "新应用"},
                        }
                    },
                }
            )

            async def collect_conflict() -> str:
                """消费冲突创建结果，确认动作以失败事件结束。"""

                return "".join([frame async for frame in conflicting_stream])

            frames = asyncio.run(collect_conflict())
            saved = json.loads(
                application_lifecycle_path(directory).read_text(encoding="utf-8")
            )

        self.assertIn("当前工作区已属于另一个应用", frames)
        self.assertEqual(saved["application"]["id"], "app-1")


if __name__ == "__main__":
    unittest.main()
