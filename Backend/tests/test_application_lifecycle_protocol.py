from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.protocols.application_lifecycle import (
    application_lifecycle_capabilities,
    build_application_lifecycle_ag_ui_stream,
)
from app.services.application_lifecycle import (
    application_lifecycle_path,
    create_application_lifecycle,
    transition_application_lifecycle,
    write_application_lifecycle,
)


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
            ["create", "get", "prepare_template_generation", "complete_template_generation"],
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

    def test_prepare_action_accepts_structured_download_result(self) -> None:
        """模板准备动作应通过 AG-UI 接收下载明细并生成 manifest。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
            route = [
                ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
                ApplicationLifecycleStage.GENERATING_REQUIREMENT_SPEC,
                ApplicationLifecycleStage.GENERATING_PRODUCT_PLAN,
                ApplicationLifecycleStage.AWAITING_PRODUCT_PLAN_CONFIRMATION,
                ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
                ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
                ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
                ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
            ]
            for stage in route:
                state = transition_application_lifecycle(
                    state,
                    stage=stage,
                    status=ApplicationLifecycleStatus.RUNNING,
                )
            write_application_lifecycle(workspace, state)
            (workspace / ".xcodeagent/plans").mkdir(parents=True, exist_ok=True)
            (workspace / ".xcodeagent/specs").mkdir(parents=True, exist_ok=True)
            (workspace / ".xcodeagent/plans/product-plan.json").write_text(
                json.dumps(
                    {
                        "schema_version": "product-plan.v5",
                        "confirmation_status": "confirmed",
                        "pages": [],
                    }
                ),
                encoding="utf-8",
            )
            (workspace / ".xcodeagent/specs/ui-designs.json").write_text(
                json.dumps(
                    {
                        "schema_version": "ui-manifest.v3",
                        "confirmation_status": "skipped",
                        "pages": [],
                    }
                ),
                encoding="utf-8",
            )
            (workspace / "frontend/src/constants").mkdir(parents=True)
            (workspace / "frontend/package.json").write_text("{}", encoding="utf-8")
            (workspace / "frontend/src/constants/resources.ts").write_text("export const RESOURCES = {} as const;\n", encoding="utf-8")
            (workspace / "frontend/src/constants/routes.tsx").write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            (workspace / "backend").mkdir()
            (workspace / "backend/pom.xml").write_text("<project />", encoding="utf-8")
            stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "template-thread",
                    "runId": "template-run",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "prepare_template_generation",
                            "workspaceRoot": directory,
                            "downloadResult": {
                                "ok": True,
                                "status": "succeeded",
                                "failedTargets": [],
                                "targets": {
                                    "frontend": {
                                        "status": "succeeded",
                                        "attempt": 0,
                                        "path": str(workspace / "frontend"),
                                        "branch": "auth",
                                    },
                                    "backend": {
                                        "status": "succeeded",
                                        "attempt": 0,
                                        "path": str(workspace / "backend"),
                                        "branch": "auth",
                                    },
                                },
                            },
                        }
                    },
                }
            )

            async def collect_prepare() -> str:
                """消费模板准备动作的完整 AG-UI 事件流。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect_prepare())
            manifest = json.loads(
                (workspace / ".xcodeagent/template-generation-manifest.json").read_text(
                    encoding="utf-8"
                )
            )

        self.assertIn("页面和菜单增量初始化完成", frames)
        self.assertEqual(manifest["steps"]["download"]["status"], "succeeded")


if __name__ == "__main__":
    unittest.main()
