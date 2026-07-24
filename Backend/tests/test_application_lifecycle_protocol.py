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


if __name__ == "__main__":
    unittest.main()
