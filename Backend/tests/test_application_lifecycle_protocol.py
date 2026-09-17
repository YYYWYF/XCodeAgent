from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.domain.application_lifecycle import ApplicationLifecycle
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
from app.services.preview_runtime_guard import claim_maintenance, release_maintenance
from app.workspace.task_documents import (
    load_pending_build_task_plan,
    write_pending_build_task_plan_atomic,
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
            [
                "create",
                "get",
                "bootstrap_template_generation",
                "retry_bootstrap_template_generation",
                "workspace_attach",
                "release_session_pending",
            ],
        )

    def test_release_session_pending_requires_session_id(self) -> None:
        """Session Pending 收口动作缺少合法 sessionId 时必须由协议拒绝。"""

        with tempfile.TemporaryDirectory() as directory:
            stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "release-thread",
                    "runId": "release-run",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "release_session_pending",
                            "workspaceRoot": directory,
                        }
                    },
                }
            )

            async def collect() -> str:
                """消费缺少 Session 身份的失败事件流。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())

        self.assertIn("release_session_pending", frames)
        self.assertIn("sessionId", frames)
        self.assertIn('"status":"failed"', frames)

    def test_release_session_pending_returns_recomputed_refresh(self) -> None:
        """owner 匹配时 AG-UI 应返回 released 标记和最新 idle planningRefresh。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            write_pending_build_task_plan_atomic(
                state,
                {
                    "schema_version": "build-dag.v4",
                    "status": "ready",
                    "task_graph": {"validation": {"is_valid": True, "errors": []}},
                },
                owner_session_id="session-release",
                planning_run_id="planning-release",
                workflow_run_id="workflow-release",
                base_confirmed_plan_digest=None,
                input_fingerprint="a" * 64,
                build_execution_scope={"type": "application", "targetId": "application"},
                created_at="2026-09-16T00:00:00Z",
                planning_provenance={
                    "schema_version": "planning-provenance.v2",
                    "review_task_ids": [],
                    "new_task_ids": [],
                    "reused_task_ids": [],
                },
            )
            pending = load_pending_build_task_plan(state)
            assert pending is not None
            lifecycle = ApplicationLifecycle.model_validate(
                {
                    "application": {"id": "app-release", "name": "收口测试"},
                    "updatedAt": "2026-09-16T00:00:00Z",
                    "revision": 1,
                    "initialization": {
                        "stage": "ready_for_workbench",
                        "status": "completed",
                    },
                    "activeRunId": "workflow-release",
                    "activeExecutions": {
                        "workflow-release": {
                            "scope": "application",
                            "targetId": "application",
                            "threadId": "thread-release",
                            "runId": "workflow-release",
                            "phase": "prepare_build_tasks",
                            "status": "awaiting_user",
                            "pendingInteraction": {
                                "id": "pending-release",
                                "type": "task_plan_confirmation",
                                "basedOnRevision": 1,
                                "payload": {"mode": "build_task_plan_confirmation"},
                                "artifactRefs": [],
                                "createdAt": "2026-09-16T00:00:00Z",
                            },
                            "startedAt": "2026-09-16T00:00:00Z",
                            "updatedAt": "2026-09-16T00:00:00Z",
                        }
                    },
                }
            )
            write_application_lifecycle(directory, lifecycle)
            stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "release-request-thread",
                    "runId": "release-request-run",
                    "forwardedProps": {
                        "applicationLifecycle": {
                            "action": "release_session_pending",
                            "workspaceRoot": directory,
                            "sessionId": "session-release",
                        }
                    },
                }
            )

            async def collect() -> str:
                """消费 Session Pending 收口的完整 AG-UI 事件流。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect())

        self.assertIn('"sessionPendingReleased":true', frames)
        self.assertIn('"source":"none"', frames)
        self.assertIn('"status":"completed"', frames)

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

    def test_removed_template_generation_action_fails_pydantic_validation(self) -> None:
        """旧 Renderer 下载回传动作必须被当前协议拒绝。"""

        with tempfile.TemporaryDirectory() as directory:
            stream = build_application_lifecycle_ag_ui_stream(
                payload={
                    "threadId": "template-thread",
                    "runId": "template-run",
                    "forwardedProps": {"applicationLifecycle": {"action": "prepare_template_generation", "workspaceRoot": directory}},
                }
            )

            async def collect_removed_action() -> str:
                """消费被 Pydantic 拒绝的旧动作事件流。"""

                return "".join([frame async for frame in stream])

            frames = asyncio.run(collect_removed_action())

        self.assertIn("validation error", frames)

    def test_get_action_never_runs_workspace_attach(self) -> None:
        """get 仅返回已持久化生命周期，不能隐式接管或修复 Workspace。"""

        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            write_application_lifecycle(directory, lifecycle)
            with patch(
                "app.protocols.application_lifecycle.template_mutation_coordinator.attach_workspace"
            ) as attach_workspace:
                stream = build_application_lifecycle_ag_ui_stream(
                    payload={
                        "threadId": "lifecycle-thread",
                        "runId": "lifecycle-run",
                        "forwardedProps": {
                            "applicationLifecycle": {
                                "action": "get",
                                "workspaceRoot": directory,
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费只读 get 事件流。"""

                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())

        attach_workspace.assert_not_called()
        self.assertIn("已读取应用生命周期", frames)

    def test_get_action_is_not_blocked_by_preview_maintenance(self) -> None:
        """工作台只读恢复不应被隐藏页面的预览维护状态阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            write_application_lifecycle(directory, lifecycle)
            claim_maintenance(directory, "preview-thread", "restart")
            try:
                stream = build_application_lifecycle_ag_ui_stream(
                    payload={
                        "threadId": "lifecycle-thread",
                        "runId": "lifecycle-run",
                        "forwardedProps": {
                            "applicationLifecycle": {
                                "action": "get",
                                "workspaceRoot": directory,
                            }
                        },
                    }
                )

                async def collect() -> str:
                    """消费维护期间的只读生命周期响应。"""
                    return "".join([frame async for frame in stream])

                frames = asyncio.run(collect())
            finally:
                release_maintenance(directory, "preview-thread")

        self.assertIn("已读取应用生命周期", frames)
        self.assertNotIn("当前应用正在进行预览服务维护", frames)

    def test_workspace_attach_is_not_blocked_by_preview_maintenance(self) -> None:
        """真实入口链路的 Workspace Attach 同样不能被预览维护阻断。"""
        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(
                application_id="app-1",
                application_name="任务中心",
            )
            write_application_lifecycle(directory, lifecycle)
            claim_maintenance(directory, "preview-thread", "restart")
            try:
                with patch(
                    "app.protocols.application_lifecycle.template_mutation_coordinator.attach_workspace",
                    return_value=SimpleNamespace(
                        action="preserved",
                        cleaned=False,
                        lifecycle_changed=False,
                    ),
                ):
                    stream = build_application_lifecycle_ag_ui_stream(
                        payload={
                            "threadId": "lifecycle-thread",
                            "runId": "lifecycle-run",
                            "forwardedProps": {
                                "applicationLifecycle": {
                                    "action": "workspace_attach",
                                    "workspaceRoot": directory,
                                }
                            },
                        }
                    )

                    async def collect() -> str:
                        """消费维护期间的 Workspace Attach 响应。"""
                        return "".join([frame async for frame in stream])

                    frames = asyncio.run(collect())
            finally:
                release_maintenance(directory, "preview-thread")

        self.assertIn("Workspace Attach 已完成", frames)
        self.assertNotIn("当前应用正在进行预览服务维护", frames)


if __name__ == "__main__":
    unittest.main()
