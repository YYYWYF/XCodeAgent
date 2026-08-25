from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from langgraph.graph import END, START, StateGraph

from app.graph.state import ProjectState
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import (
    _workflow_confirmation_artifact,
    _workflow_next_nodes,
    _workflow_node_detail,
    _workflow_progress_summary,
)
from app.protocols.workflow.stream_events import (
    integration_test_check_summary,
    integration_test_checks,
)
from app.protocols.workflow_visualization import (
    _workflow_summary,
    _workflow_visual_payload,
)


def _decode_agent_process_frames(frames: list[str]) -> list[dict]:
    """把 ``build_workflow_ag_ui_stream`` 产出的 SSE 帧解析为 ``agent-process`` 事件列表。

    每个 ``agent-process`` 事件对应一次过程步骤的更新,带有 ``id`` / ``status`` / ``sequence`` /
    ``checks`` 等字段,供回归测试按时间顺序校验实时进度。
    """

    decoded: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            payload = line[len("data:"):].strip()
            if not payload:
                continue
            try:
                event = json.loads(payload)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "CUSTOM" or event.get("name") != "agent-process":
                continue
            value = event.get("value")
            if isinstance(value, dict):
                decoded.append(value)
    return decoded


def _decode_workflow_run_frames(frames: list[str]) -> list[dict]:
    """解析 workflow-run 自定义帧，用于确认临时 UI 活动不会进入持久化事件。"""

    decoded: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") == "CUSTOM" and event.get("name") == "workflow-run":
                value = event.get("value")
                if isinstance(value, dict):
                    decoded.append(value)
    return decoded


def _decode_custom_frames(frames: list[str]) -> list[dict]:
    """解析全部 AG-UI 自定义事件，用于验证 lifecycle 与 Workflow 的投影顺序。"""

    decoded: list[dict] = []
    for frame in frames:
        for line in frame.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                event = json.loads(line[len("data:"):].strip())
            except json.JSONDecodeError:
                continue
            if event.get("type") == "CUSTOM":
                decoded.append(event)
    return decoded


class FakeWorkflowGraph:
    def __init__(self) -> None:
        self.initial_states: list[dict] = []

    async def astream(self, initial_state, *, config, stream_mode):
        self.initial_states.append(initial_state)
        yield "updates", {
            "classify_request_complexity": {
                "phase": "classify_request_complexity",
                "status": "completed",
                "request_complexity": "complex",
                "message": "classified",
                "timeline": ["classified"],
            }
        }
        yield "updates", {
            "requirements": {
                "phase": "requirements",
                "requirement_spec_path": "var/specs/requirement-spec.md",
                "clarification": {
                    "status": "requires_user_input",
                    "questions": [
                        {
                            "id": "user_roles",
                            "header": "用户角色",
                            "question": "需要哪些角色？",
                            "type": "choice",
                        }
                    ],
                },
                "timeline": ["requirements"],
            }
        }

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "finalize_project",
                "status": "requires_user_input",
                "summary": "done",
                "timeline": ["classified", "done"],
                "quality_gate_passed": None,
                "clarification": {
                    "status": "requires_user_input",
                    "questions": [{"id": "user_roles", "question": "需要哪些角色？"}],
                },
            }
        )

    async def aget_state(self, config):
        """模拟真实 LangGraph 的异步状态读取接口。"""

        return self.get_state(config)


class FakeProjectPlanningWaitGraph:
    def __init__(self, project_plan_path: str = "var/plans/project-plan.md") -> None:
        self.project_plan_path = project_plan_path

    async def astream(self, initial_state, *, config, stream_mode):
        yield "updates", {
            "project_planning": {
                "phase": "project_planning",
                "status": "requires_user_input",
                "project_plan_path": self.project_plan_path,
                "project_plan_json_path": "var/plans/project-plan.json",
                "project_plan": {"confirmation_status": "pending_user_confirmation"},
                "clarification": {
                    "mode": "project_plan_confirmation",
                    "status": "requires_user_input",
                    "questions": [
                        {
                            "header": "计划确认",
                            "question": "请确认项目规划书是否正确。",
                            "type": "text",
                        }
                    ],
                },
                "timeline": ["project_planning"],
            }
        }

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "project_planning",
                "status": "requires_user_input",
                "project_plan_path": self.project_plan_path,
                "project_plan_json_path": "var/plans/project-plan.json",
                "project_plan": {"confirmation_status": "pending_user_confirmation"},
                "clarification": {
                    "mode": "project_plan_confirmation",
                    "status": "requires_user_input",
                    "questions": [{"header": "计划确认", "question": "请确认项目规划书是否正确。"}],
                },
                "timeline": ["project_planning"],
            }
        )


class FakeWorkspaceInspectionGraph:
    """模拟工作区快照检查完成并命中缓存。"""

    async def astream(self, initial_state, *, config, stream_mode):
        """发送包含安全摘要和内部路径的节点更新。"""

        del initial_state, config, stream_mode
        yield "updates", {
            "inspect_workspace": {
                "phase": "inspect_workspace",
                "status": "completed",
                "workspace_revision": "revision-1234567890",
                "workspace_snapshot_path": "/private/workspace/cache/snapshot.json",
                "workspace_snapshot_hash": "secret-hash",
                "workspace_snapshot_summary": {
                    "schema_version": "1.0.0",
                    "workspace_revision": "revision-1234567890",
                    "tech_stack": ["FastAPI", "React"],
                    "project_roots": [
                        {"path": "Backend/app", "kind": "backend"},
                        {"path": "/private/workspace", "kind": "unsafe"},
                    ],
                    "entrypoints": [
                        {"path": "Backend/app/main.py", "kind": "backend_api"}
                    ],
                    "file_manifest": {
                        "total_files_indexed": 128,
                        "source_files_indexed": 96,
                        "truncated": False,
                    },
                    "code_graph": {
                        "provider": "code-review-graph",
                        "providerVersion": "2.3.7",
                        "status": "ready",
                        "available": True,
                        "buildType": "full",
                        "filesIndexed": 41,
                        "symbolsIndexed": 122,
                        "relationsIndexed": 495,
                        "languages": ["java", "typescript"],
                        "nodesByKind": [{"kind": "Function", "count": 78}],
                        "relationsByKind": [{"kind": "CALLS", "count": 332}],
                        "sampleSymbols": [
                            {
                                "name": "login",
                                "kind": "function",
                                "language": "typescript",
                                "path": "frontend/src/api/auth.ts",
                                "lineStart": 12,
                                "lineEnd": 24,
                            }
                        ],
                        "warningCount": 0,
                        "warnings": [],
                        "durationMs": 4700,
                    },
                },
                "timeline": ["inspect_workspace:cache_hit"],
            }
        }

    async def aget_state(self, config):
        """返回工作区检查后的最小最终状态。"""

        del config
        return SimpleNamespace(
            values={
                "phase": "prepare_build_tasks",
                "status": "completed",
                "timeline": ["inspect_workspace:cache_hit"],
            }
        )


class FakeCodeChangesGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        yield "updates", {
            "direct_modification": {
                "phase": "direct_modification",
                "status": "completed",
                "code_changes": _fake_code_change_set(),
                "code_change_sets": [_fake_code_change_set()],
                "timeline": ["direct_modification"],
            }
        }

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "finalize_project",
                "status": "completed",
                "quality_gate_passed": True,
                "code_change_sets": [_fake_code_change_set()],
                "timeline": ["direct_modification", "finalize_project"],
            }
        )


class FakeStreamingToolGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        yield (
            "messages",
            (
                SimpleNamespace(
                    id="assistant-tool-message",
                    content="",
                    additional_kwargs={},
                    tool_call_chunks=[
                        {"id": "call-1", "name": "read_file", "args": '{"path":', "index": 0}
                    ],
                ),
                {"langgraph_node": "direct_modification"},
            ),
        )
        yield (
            "messages",
            (
                SimpleNamespace(
                    id="assistant-tool-message",
                    content="",
                    additional_kwargs={},
                    tool_call_chunks=[
                        {"id": None, "name": None, "args": '"README.md"}', "index": 0}
                    ],
                ),
                {"langgraph_node": "direct_modification"},
            ),
        )
        yield (
            "messages",
            (
                SimpleNamespace(
                    id="tool-result-message",
                    content="read result",
                    additional_kwargs={},
                    tool_call_id="call-1",
                    tool_call_chunks=[],
                ),
                {"langgraph_node": "direct_modification"},
            ),
        )
        yield (
            "updates",
            {
                "direct_modification": {
                    "phase": "direct_modification",
                    "status": "completed",
                    "timeline": ["direct_modification"],
                }
            },
        )

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "finalize_project",
                "status": "completed",
                "summary": "done",
                "timeline": ["direct_modification", "finalize_project"],
            }
        )


class FakeIntegrationProgressGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        """模拟集成测试检查项的 custom stream 与最终节点更新。"""

        yield "custom", {
            "type": "integration_test.checks",
            "checks": [
                {
                    "id": "frontend_build",
                    "name": "前端构建检查",
                    "status": "running",
                    "required": True,
                    "evidence": "正在执行检查。",
                }
            ],
        }
        yield "custom", {
            "type": "integration_test.checks",
            "checks": [
                {
                    "id": "frontend_build",
                    "name": "前端构建检查",
                    "status": "passed",
                    "required": True,
                    "evidence": "命令执行通过：pnpm run build",
                }
            ],
        }
        yield "updates", {
            "integration_test": {
                "phase": "integration_test",
                "status": "completed",
                "quality_gate_passed": True,
                "integration_next_action": "launch_project",
                "test_results": [
                    {
                        "id": "frontend_build",
                        "name": "前端构建检查",
                        "passed": True,
                        "skipped": False,
                        "required": True,
                        "evidence": "命令执行通过：pnpm run build",
                    }
                ],
                "test_report": {"passed": True, "summary": {"passed": 1, "total": 1}},
                "timeline": ["integration_test"],
            }
        }

    def get_state(self, config):
        """返回集成测试后的最小工作流状态。"""

        return SimpleNamespace(
            values={
                "phase": "launch_project",
                "status": "completed",
                "quality_gate_passed": True,
                "timeline": ["integration_test"],
            }
        )


class FakeImmediateRepairProgressGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        """模拟失败门禁先发修复准备进度，再完成集成测试节点。"""

        yield "custom", {
            "type": "integration_test.checks",
            "checks": [
                {
                    "id": "backend_build",
                    "name": "后端构建检查",
                    "status": "failed",
                    "required": True,
                    "evidence": "compilation failure",
                }
            ],
        }
        yield "custom", {
            "type": "integration_test.repair.started",
            "message": "质量门禁未通过，正在分析失败原因并准备局部修复。",
        }
        yield "updates", {
            "integration_test": {
                "phase": "integration_test",
                "status": "completed",
                "quality_gate_passed": False,
                "integration_next_action": "small_task_repair",
                "test_results": [
                    {
                        "id": "backend_build",
                        "name": "后端构建检查",
                        "passed": False,
                        "required": True,
                        "evidence": "compilation failure",
                    }
                ],
                "test_report": {"passed": False, "summary": {"passed": 0, "total": 1}},
                "timeline": ["integration_test"],
            }
        }

class FakeDagGenerationProgressGraph:
    """模拟任务 DAG 子阶段实时更新并正常完成。"""

    async def astream(self, initial_state, *, config, stream_mode):
        """依次发送骨架、模型规划快照和节点完成更新。"""

        del initial_state, config, stream_mode
        stages = [
            {"id": "unit_skeleton", "name": "生成 Unit DAG 骨架", "status": "completed", "detail": "已完成"},
            {"id": "model_planning", "name": "生成候选构建任务", "status": "running", "detail": "模型规划中"},
        ]
        yield "custom", {
            "type": "prepare_build_tasks.progress",
            "message": "Unit DAG 骨架已生成。",
            "dag_generation": {
                "stages": [{**stages[0], "status": "running"}, {**stages[1], "status": "pending"}],
                "tasks": [],
                "summary": {"unitCount": 0, "taskCount": 0},
                "artifacts": [],
            },
        }
        yield "custom", {
            "type": "prepare_build_tasks.progress",
            "message": "正在调用任务规划模型。",
            "dag_generation": {
                "stages": stages,
                "tasks": [],
                "summary": {"unitCount": 2, "taskCount": 0},
                "artifacts": [],
            },
        }
        final_snapshot = {
            "stages": [{**stage, "status": "completed"} for stage in stages],
            "tasks": [{"id": "page", "title": "实现页面", "owner": "frontend", "status": "pending"}],
            "summary": {"unitCount": 2, "taskCount": 1},
            "artifacts": [
                {
                    "id": "plan",
                    "name": "build-task-plan.json",
                    "kind": "json",
                    "status": "saved",
                    "confirmationStatus": "pending",
                }
            ],
        }
        yield "updates", {
            "prepare_build_tasks": {
                "phase": "prepare_build_tasks",
                "status": "completed",
                "tasks": [{"id": "page"}],
                "dag_generation_progress": final_snapshot,
                "timeline": ["prepare_build_tasks"],
            }
        }

    async def aget_state(self, config):
        """返回任务 DAG 生成后的最小最终状态。"""

        del config
        return SimpleNamespace(
            values={
                "phase": "build",
                "status": "completed",
                "timeline": ["prepare_build_tasks"],
            }
        )


class FakeRepairLoopGraph:
    """模拟 build → test failed → small task repair → retest 的更新序列。"""

    async def astream(self, initial_state, *, config, stream_mode):
        build_slice = {
            "scope": {"type": "page", "targetId": "orders"},
            "tasks": [{"id": "orders-page", "status": "completed"}],
            "summary": {"total": 1, "completed": 1, "failed": 0, "pending": 0},
        }
        yield "updates", {
            "build": {
                "phase": "build",
                "status": "completed",
                "build_summary": {"status": "completed", "completed": 1, "failed": 0},
                "build_execution_slice": build_slice,
            }
        }
        yield "updates", {
            "integration_test": {
                "phase": "integration_test",
                "status": "completed",
                "quality_gate_passed": False,
                "integration_next_action": "repair_build",
                "test_results": [{"id": "frontend_build", "name": "前端构建检查", "passed": False, "required": True}],
            }
        }
        yield "updates", {
            "small_task_repair": {
                "phase": "small_task_repair",
                "status": "completed",
                "small_task_results": [
                    {"taskId": "repair:orders-page", "status": "completed"}
                ],
                "small_task_tasks": [
                    {"id": "repair:orders-page", "kind": "repair", "status": "completed"}
                ],
            }
        }
        yield "updates", {
            "integration_test": {
                "phase": "integration_test",
                "status": "completed",
                "quality_gate_passed": True,
                "integration_next_action": "launch_project",
                "test_results": [{"id": "frontend_build", "name": "前端构建检查", "passed": True, "required": True}],
            }
        }

    async def aget_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "launch_project",
                "status": "completed",
                "quality_gate_passed": True,
                "timeline": ["build", "integration_test", "small_task_repair", "integration_test"],
            }
        )


class FakeEphemeralBuildActivityGraph:
    """模拟一个带临时工具活动、随后正常完成的构建节点。"""

    async def astream(self, initial_state, *, config, stream_mode):
        del initial_state, config, stream_mode
        yield "custom", {
            "type": "workflow.build.progress",
            "node_name": "build",
            "status": "running",
            "message": "正在执行构建任务：page",
            "ephemeral": True,
            "state": {
                "phase": "build",
                "build_summary": {"status": "running", "running": 1},
                "build_execution_slice": {
                    "scope": {"type": "page", "targetId": "home"},
                    "tasks": [
                        {
                            "id": "page",
                            "status": "running",
                            "activeToolActivity": {
                                "callId": "read-page",
                                "tool": "read_file",
                                "category": "read",
                                "status": "running",
                                "message": "正在读取文件：/src/Page.tsx",
                                "path": "/src/Page.tsx",
                            },
                        }
                    ],
                    "summary": {"total": 1, "running": 1},
                },
            },
        }
        yield "updates", {
            "build": {
                "phase": "build",
                "status": "completed",
                "build_summary": {"status": "completed", "completed": 1},
                "build_execution_slice": {
                    "scope": {"type": "page", "targetId": "home"},
                    "tasks": [{"id": "page", "status": "completed"}],
                    "summary": {"total": 1, "completed": 1},
                },
            }
        }

    async def aget_state(self, config):
        del config
        return SimpleNamespace(
            values={
                "phase": "integration_test",
                "status": "completed",
                "timeline": ["build"],
            }
        )

class FakeAskUserToolGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        yield (
            "messages",
            (
                SimpleNamespace(
                    id="assistant-ask-user",
                    content="",
                    additional_kwargs={},
                    tool_call_chunks=[
                        {
                            "id": "ask-1",
                            "name": "ask_user",
                            "args": '{"questions":[{"question":"Which role?"}]}',
                            "index": 0,
                        }
                    ],
                ),
                {"langgraph_node": "requirements"},
            ),
        )
        yield (
            "updates",
            {
                "requirements": {
                    "phase": "requirements",
                    "status": "requires_user_input",
                    "clarification": {
                        "status": "requires_user_input",
                        "questions": [{"id": "role", "question": "Which role?"}],
                    },
                    "timeline": ["requirements"],
                }
            },
        )

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "requirements",
                "status": "requires_user_input",
                "clarification": {
                    "status": "requires_user_input",
                    "questions": [{"id": "role", "question": "Which role?"}],
                },
                "timeline": ["requirements"],
            }
        )


class FakeBlockingGraph:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def astream(self, initial_state, *, config, stream_mode):
        self.started.set()
        await asyncio.Event().wait()
        yield "updates", {}

    def get_state(self, config):
        return SimpleNamespace(values={})


class FakeUiConfirmationProgressGraph:
    """模拟换一换 run：先发 ui_confirmation.progress 自定义事件，再完成节点。"""

    async def astream(self, initial_state, *, config, stream_mode):
        # 单页换一换生成期间推送进度（pages 为当前已就绪快照）。
        yield "custom", {
            "type": "ui_confirmation.progress",
            "node_name": "ui_confirmation",
            "message": "正在重新生成设计稿：首页",
            "detail": {
                "ready": 1,
                "total": 2,
                "pageId": "home_page",
                "pages": [
                    {
                        "pageId": "home_page",
                        "name": "首页",
                        "code": "export default function HomePage() { return null }",
                        "status": "pending",
                    },
                    {
                        "pageId": "list_page",
                        "name": "列表页",
                        "code": "",
                        "status": "pending",
                    },
                ],
            },
        }
        # 节点完成：返回 requires_user_input + ui_design_confirmation。
        yield "updates", {
            "ui_confirmation": {
                "phase": "ui_confirmation",
                "status": "requires_user_input",
                "ui_designs": {
                    "confirmation_status": "pending_user_confirmation",
                    "pages": [
                        {
                            "pageId": "home_page",
                            "name": "首页",
                            "code": "export default function HomePage() { return null }",
                            "status": "confirmed",
                        },
                        {
                            "pageId": "list_page",
                            "name": "列表页",
                            "code": "",
                            "status": "pending",
                        },
                    ],
                },
                "clarification": {
                    "mode": "ui_design_confirmation",
                    "status": "requires_user_input",
                    "questions": [],
                    "pages": [
                        {
                            "pageId": "home_page",
                            "name": "首页",
                            "code": "export default function HomePage() { return null }",
                            "status": "confirmed",
                        },
                        {
                            "pageId": "list_page",
                            "name": "列表页",
                            "code": "",
                            "status": "pending",
                        },
                    ],
                },
                "timeline": ["ui_confirmation"],
            }
        }

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "ui_confirmation",
                "status": "requires_user_input",
                "clarification": {
                    "mode": "ui_design_confirmation",
                    "status": "requires_user_input",
                    "pages": [
                        {"pageId": "home_page", "name": "首页", "code": "x", "status": "confirmed"},
                        {"pageId": "list_page", "name": "列表页", "code": "", "status": "pending"},
                    ],
                },
                "timeline": ["ui_confirmation"],
            }
        )

    async def aget_state(self, config):
        return self.get_state(config)


def _fake_code_change_set() -> dict:
    return {
        "id": "code-change-set:test",
        "status": "applied",
        "workspaceRoot": "/tmp/workspace",
        "summary": {"files": 1, "additions": 1, "deletions": 0},
        "files": [
            {
                "id": "file.write:data.json:test",
                "path": "data.json",
                "changeType": "added",
                "additions": 1,
                "deletions": 0,
                "diff": "--- data.json\n+++ data.json\n@@ -0,0 +1 @@\n+{\"sbw\":123}",
                "truncated": False,
                "binary": False,
                "tool": "file.write",
                "executed": True,
            }
        ],
    }


class WorkflowAgUiStreamTests(unittest.TestCase):
    def test_workspace_inspection_projects_safe_structured_summary(self) -> None:
        """工作区检查详情应包含识别结果，但不能暴露绝对缓存路径或哈希。"""

        detail = _workflow_node_detail(
            "inspect_workspace",
            {
                "workspace_revision": "revision-1234567890",
                "workspace_snapshot_path": "/private/workspace/cache/snapshot.json",
                "workspace_snapshot_hash": "secret-hash",
                "workspace_snapshot_summary": {
                    "schema_version": "1.0.0",
                    "tech_stack": ["FastAPI", "React"],
                    "project_roots": [
                        {"path": "Backend/app", "kind": "backend"},
                        {"path": "/private/workspace", "kind": "unsafe"},
                    ],
                    "entrypoints": [
                        {"path": "Backend/app/main.py", "kind": "backend_api"}
                    ],
                    "file_manifest": {
                        "total_files_indexed": 128,
                        "source_files_indexed": 96,
                        "truncated": False,
                    },
                    "code_graph": {
                        "provider": "code-review-graph",
                        "providerVersion": "2.3.7",
                        "status": "ready",
                        "available": True,
                        "buildType": "full",
                        "filesIndexed": 41,
                        "symbolsIndexed": 122,
                        "relationsIndexed": 495,
                        "languages": ["java", "typescript"],
                        "nodesByKind": [{"kind": "Function", "count": 78}],
                        "relationsByKind": [{"kind": "CALLS", "count": 332}],
                        "sampleSymbols": [
                            {
                                "name": "login",
                                "kind": "function",
                                "language": "typescript",
                                "path": "frontend/src/api/auth.ts",
                                "lineStart": 12,
                                "lineEnd": 24,
                            }
                        ],
                        "warningCount": 0,
                        "warnings": [],
                        "durationMs": 4700,
                    },
                },
                "timeline": ["inspect_workspace:cache_hit"],
            },
        )

        snapshot = detail["data"]["workspaceInspection"]
        self.assertEqual(snapshot["fileManifest"]["totalFiles"], 128)
        self.assertEqual(snapshot["techStack"], ["FastAPI", "React"])
        self.assertEqual(snapshot["projectRoots"], [{"path": "Backend/app", "kind": "backend"}])
        self.assertTrue(snapshot["cacheHit"])
        self.assertEqual(snapshot["codeGraph"]["filesIndexed"], 41)
        self.assertEqual(snapshot["codeGraph"]["nodesByKind"][0]["kind"], "Function")
        self.assertNotIn("codeNavigation", snapshot)
        self.assertIn("已索引 128 个文件", detail["message"])
        self.assertNotIn("/private/workspace", str(snapshot))
        self.assertNotIn("secret-hash", str(snapshot))

    def test_progress_summary_prefers_newly_started_node_over_previous_result(self) -> None:
        """节点切换后应立即展示新阶段，不能继续沿用上一节点的 phase。"""

        summary = _workflow_progress_summary(
            {
                "phase": "requirements",
                "status": "completed",
                "clarification": {
                    "mode": "requirement_document_confirmation",
                    "status": "clear",
                },
            },
            [
                {
                    "type": "workflow.node.completed",
                    "node": {"id": "requirements", "label": "需求确认"},
                    "status": "completed",
                },
                {
                    "type": "workflow.node.started",
                    "node": {"id": "project_planning", "label": "项目规划"},
                    "status": "running",
                },
            ],
        )

        self.assertEqual(summary["phase"], "project_planning")
        self.assertEqual(summary["status"], "running")

    def test_progress_summary_does_not_treat_missing_requirement_confirmation_as_false(
        self,
    ) -> None:
        """节点切换增量未携带需求确认字段时不得把已确认文档误报为草稿。"""

        summary = _workflow_progress_summary(
            {"phase": "ui_confirmation", "status": "completed"},
            [
                {
                    "type": "workflow.node.started",
                    "node": {"id": "technical_planning", "label": "技术规划"},
                    "status": "running",
                }
            ],
        )

        self.assertEqual(summary["phase"], "technical_planning")
        self.assertNotIn("requirementsConfirmed", summary)

    def setUp(self) -> None:
        self.cleanup_patcher = patch(
            "app.protocols.workflow.runtime.cleanup_workflow_checkpoints",
            new=AsyncMock(return_value=None),
        )
        self.cleanup_patcher.start()

    def tearDown(self) -> None:
        self.cleanup_patcher.stop()

    def test_launch_project_is_current_run_terminal_in_visual_timeline(self) -> None:
        """验证启动成功或失败后都不会伪造尚未执行的验收节点事件。"""

        self.assertEqual(
            _workflow_next_nodes("launch_project", {"status": "requires_user_input"}),
            [],
        )
        self.assertEqual(
            _workflow_next_nodes("launch_project", {"status": "failed"}),
            [],
        )

    def test_invalid_selected_skills_emits_structured_error(self) -> None:
        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeWorkflowGraph(),
                payload={
                    "threadId": "thread-skills-error",
                    "runId": "run-skills-error",
                    "messages": [{"role": "user", "content": "use a skill"}],
                    "forwardedProps": {"selectedSkillNames": "invalid"},
                },
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn("invalid_selected_skills", payload)
        self.assertIn("RUN_ERROR", payload)
        self.assertNotIn("RUN_FINISHED", payload)
        self.assertNotIn("TEXT_MESSAGE_CONTENT", payload)

    def test_selected_skills_are_forwarded_to_graph_state_and_metadata(self) -> None:
        graph = FakeWorkflowGraph()
        validation = SimpleNamespace(names=("alpha",), revision="skills-revision")

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "thread-skills",
                    "runId": "run-skills",
                    "messages": [{"role": "user", "content": "use alpha"}],
                    "forwardedProps": {"selectedSkillNames": ["alpha"]},
                },
            )
            return [frame async for frame in stream]

        with patch(
            "app.protocols.workflow.runtime.validate_selected_user_skills",
            return_value=validation,
        ):
            frames = asyncio.run(collect())

        self.assertEqual(graph.initial_states[0]["selected_skill_names"], ["alpha"])
        payload = "\n".join(frames)
        self.assertIn('"selectedSkillNames":["alpha"]', payload)
        self.assertIn("skills-revision", payload)

    def test_application_planning_does_not_enter_workbench_lifecycle(self) -> None:
        """独立需求规划不能登记、推进或失败收尾工作台计划执行。"""

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeWorkflowGraph(),
                payload={
                    "threadId": "thread-application-planning",
                    "runId": "run-application-planning",
                    "messages": [{"role": "user", "content": "plan application"}],
                    "forwardedProps": {
                        "workflowScope": "application_planning",
                        "workspaceRoot": "/tmp/application-planning",
                    },
                },
            )
            return [frame async for frame in stream]

        with (
            patch("app.protocols.workflow.runtime.begin_workflow_lifecycle") as begin,
            patch(
                "app.protocols.workflow.runtime.project_workflow_lifecycle_boundary"
            ) as project,
            patch("app.protocols.workflow.runtime.stop_workflow_lifecycle") as stop,
            patch("app.protocols.workflow.runtime.fail_workflow_lifecycle") as fail,
        ):
            frames = asyncio.run(collect())

        self.assertTrue(frames)
        begin.assert_not_called()
        project.assert_not_called()
        stop.assert_not_called()
        fail.assert_not_called()

    def test_workbench_lifecycle_is_projected_before_first_node_snapshot(self) -> None:
        """资源锁写入成功后应立即广播，不能等待首个 Graph 节点完成。"""

        initial_lifecycle = {
            "schemaVersion": "1.4.0",
            "application": {"id": "app-1", "name": "测试应用"},
            "updatedAt": "2026-07-23T00:00:00Z",
            "revision": 2,
            "initialization": {
                "stage": "ready_for_workbench",
                "status": "completed",
            },
            "activeExecutions": {},
            "resourceLocks": {
                "application": None,
                "pages": {},
                "apiContracts": {},
                "dataSources": {},
            },
            "extensions": {},
        }

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeWorkflowGraph(),
                payload={
                    "threadId": "thread-lifecycle",
                    "runId": "run-lifecycle",
                    "messages": [{"role": "user", "content": "build orders"}],
                    "forwardedProps": {"workspaceRoot": "/tmp/lifecycle-projection"},
                },
            )
            return [frame async for frame in stream]

        with (
            patch(
                "app.protocols.workflow.runtime.begin_workflow_lifecycle",
                return_value=initial_lifecycle,
            ) as begin,
            patch(
                "app.protocols.workflow.runtime.project_workflow_lifecycle_boundary",
                return_value=initial_lifecycle,
            ),
        ):
            frames = asyncio.run(collect())

        begin.assert_called_once()
        custom_events = _decode_custom_frames(frames)
        event_names = [event.get("name") for event in custom_events]
        self.assertEqual(event_names[0], "application-lifecycle", event_names)
        self.assertIn("workflow-run", event_names)
        self.assertLess(
            event_names.index("application-lifecycle"),
            event_names.index("workflow-run"),
        )

    def test_cancel_run_request_cancels_the_active_workflow_task(self) -> None:
        graph = FakeBlockingGraph()

        async def collect(stream) -> list[str]:
            return [frame async for frame in stream]

        async def run() -> tuple[list[str], bool]:
            workflow_task = asyncio.create_task(
                collect(
                    build_workflow_ag_ui_stream(
                        graph=graph,
                        payload={
                            "threadId": "thread-cancel",
                            "runId": "run-active",
                            "messages": [{"role": "user", "content": "keep working"}],
                        },
                    )
                )
            )
            await graph.started.wait()
            cancellation_frames = await collect(
                build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={
                        "threadId": "thread-cancel",
                        "runId": "run-cancel-request",
                        "forwardedProps": {"cancelRunId": "run-active"},
                    },
                )
            )
            with self.assertRaises(asyncio.CancelledError):
                await workflow_task
            return cancellation_frames, workflow_task.cancelled()

        frames, cancelled = asyncio.run(run())
        payload = "\n".join(frames)

        self.assertTrue(cancelled)
        self.assertIn("RUN_STARTED", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn("cancel_requested", payload)

    def test_visual_payload_state_preserves_requirement_spec_for_resume(self) -> None:
        result = {
            "phase": "requirements",
            "status": "requires_user_input",
            "requirement_spec": {
                "confirmation_status": "pending_user_input",
                "clarification_status": "requires_user_input",
            },
            "requirement_spec_path": "var/specs/requirement-spec.md",
            "requirement_spec_json_path": "var/specs/requirement-spec.json",
            "clarification": {
                "status": "requires_user_input",
                "questions": [{"id": "role", "question": "需要哪些角色？"}],
            },
            "timeline": ["requirements"],
        }
        summary = _workflow_summary(result, [])
        payload = _workflow_visual_payload(
            run_id="run-resume",
            thread_id="thread-resume",
            summary=summary,
            events=[],
            result=result,
        )

        self.assertEqual(
            payload["state"]["requirement_spec"]["confirmation_status"],
            "pending_user_input",
        )
        self.assertEqual(
            payload["state"]["requirement_spec_path"],
            "var/specs/requirement-spec.md",
        )

    def test_ask_user_tool_ends_before_run_finishes_without_tool_message(self) -> None:
        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeAskUserToolGraph(),
                payload={
                    "threadId": "thread-ask",
                    "runId": "run-ask",
                    "messages": [{"role": "user", "content": "make an app"}],
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn("TOOL_CALL_RESULT", payload)
        self.assertLess(payload.index("TOOL_CALL_END"), payload.index("RUN_FINISHED"))
        self.assertLess(payload.index("TOOL_CALL_RESULT"), payload.index("RUN_FINISHED"))

    def test_stream_emits_incremental_standard_tool_call_events(self) -> None:
        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeStreamingToolGraph(),
                payload={
                    "threadId": "thread-tools",
                    "runId": "run-tools",
                    "messages": [{"role": "user", "content": "read the readme"}],
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn("TOOL_CALL_START", payload)
        self.assertEqual(payload.count("TOOL_CALL_ARGS"), 2)
        self.assertIn("TOOL_CALL_END", payload)
        self.assertIn("TOOL_CALL_RESULT", payload)
        self.assertIn('\\"README.md\\"', payload)
        self.assertIn("read result", payload)

    def test_stream_emits_incremental_integration_check_snapshots(self) -> None:
        """验证 custom stream 会更新同一集成测试步骤的检查快照。"""

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeIntegrationProgressGraph(),
                payload={
                    "threadId": "thread-integration-progress",
                    "runId": "run-integration-progress",
                    "messages": [{"role": "user", "content": "run integration tests"}],
                },
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn('"id":"workflow:integration_test"', payload)
        self.assertIn('"name":"前端构建检查"', payload)
        self.assertIn('"status":"running"', payload)
        self.assertIn('"status":"passed"', payload)

    def test_stream_emits_ordered_dag_generation_snapshots(self) -> None:
        """DAG 子阶段应在节点完成前更新同一个稳定 ProcessStep。"""

        async def collect() -> list[str]:
            """收集模拟 DAG 生成节点的全部 SSE 帧。"""

            stream = build_workflow_ag_ui_stream(
                graph=FakeDagGenerationProgressGraph(),
                payload={
                    "threadId": "thread-dag-progress",
                    "runId": "run-dag-progress",
                    "messages": [{"role": "user", "content": "生成任务 DAG"}],
                    "forwardedProps": {"resumeFrom": "prepare_build_tasks"},
                },
            )
            return [frame async for frame in stream]

        frames = _decode_agent_process_frames(asyncio.run(collect()))
        dag_frames = [
            frame
            for frame in frames
            if frame.get("id") == "workflow:prepare_build_tasks"
            and isinstance(frame.get("dagGeneration"), dict)
        ]

        self.assertEqual([frame["status"] for frame in dag_frames], ["running", "running", "completed"])
        self.assertEqual(dag_frames[0]["dagGeneration"]["stages"][0]["status"], "running")
        self.assertEqual(dag_frames[1]["dagGeneration"]["stages"][1]["status"], "running")
        self.assertEqual(dag_frames[-1]["dagGeneration"]["tasks"][0]["id"], "page")
        self.assertLess(dag_frames[-2]["sequence"], dag_frames[-1]["sequence"])

    def test_stream_attaches_workspace_inspection_to_completed_step(self) -> None:
        """工作区完成帧应复用节点事件中的安全结构化摘要。"""

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeWorkspaceInspectionGraph(),
                payload={
                    "threadId": "thread-workspace-scan",
                    "runId": "run-workspace-scan",
                    "messages": [{"role": "user", "content": "检查工作区"}],
                    "forwardedProps": {"resumeFrom": "inspect_workspace"},
                },
            )
            return [frame async for frame in stream]

        frames = _decode_agent_process_frames(asyncio.run(collect()))
        completed = [
            frame
            for frame in frames
            if frame.get("id") == "workflow:inspect_workspace"
            and frame.get("status") == "completed"
        ]

        self.assertEqual(len(completed), 1)
        snapshot = completed[0]["workspaceInspection"]
        self.assertEqual(snapshot["fileManifest"]["sourceFiles"], 96)
        self.assertEqual(snapshot["entrypoints"][0]["path"], "Backend/app/main.py")
        self.assertTrue(snapshot["cacheHit"])
        self.assertNotIn("/private/workspace", str(snapshot))

    def test_workflow_stream_forwards_integration_test_checks_progressively(self) -> None:
        """回归保护:runtime 必须把 LangGraph custom 流中的 ``integration_test.checks`` 事件
        转换成 ``agent-process`` 帧实时推给前端,且每一帧的 ``checks`` 字段反映当时的快照。

        在修复之前,``runtime.py`` 的 ``astream`` 消费循环里有两段并列的
        ``if stream_mode == "custom":`` 分支:第一段会丢弃非 ``workflow.build.progress``
        的 custom 事件,第二段处理 ``integration_test.checks`` 但被前一段遮蔽而成为
        死代码,导致节点执行过程中的检查进度从未推到前端,只在节点完成后才出现一次。

        这个测试显式按时间顺序断言:
        1. 至少有两帧 ``id=workflow:integration_test`` 且 ``status=running`` 出现在
           ``updates`` 事件触发的完成帧之前。
        2. 第一帧 ``running`` 的首个检查状态必须是 ``running``;第二帧必须把同一项
           检查的快照更新为 ``passed``(验证 ``_check_progress_snapshot_writer``
           的按 check_id 累积合并语义被正确转发)。
        3. 节点完成帧 ``status=completed`` 且 ``checks`` 与最终 ``test_results`` 一致,
           且整个步骤的 sequence 单调递增。
        """

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeIntegrationProgressGraph(),
                payload={
                    "threadId": "thread-integration-progress",
                    "runId": "run-integration-progress",
                    "messages": [{"role": "user", "content": "run integration tests"}],
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        frames = _decode_agent_process_frames(asyncio.run(collect()))

        integration_frames = [
            frame for frame in frames if frame.get("id") == "workflow:integration_test"
        ]
        running_frames = [
            frame for frame in integration_frames if frame.get("status") == "running"
        ]
        completed_frames = [
            frame for frame in integration_frames if frame.get("status") == "completed"
        ]

        # 实时帧必须先于完成帧出现,这是"实时进度"的核心契约。
        self.assertGreaterEqual(
            len(running_frames),
            2,
            f"期望至少 2 帧实时 running 帧,实际只有 {len(running_frames)} 帧,"
            f"全部帧为: {integration_frames}",
        )
        self.assertEqual(
            len(completed_frames),
            1,
            f"期望恰好 1 帧 completed 完成帧,实际有 {len(completed_frames)} 帧",
        )
        self.assertLess(
            running_frames[-1]["sequence"],
            completed_frames[0]["sequence"],
            "实时 running 帧的 sequence 必须小于完成帧,保证前端先看到进度再看到完成态",
        )

        # 第一帧应该报告检查项为 running 状态,第二帧把同一项更新为 passed。
        first_checks = running_frames[0].get("checks") or []
        second_checks = running_frames[1].get("checks") or []
        self.assertGreaterEqual(len(first_checks), 1)
        self.assertGreaterEqual(len(second_checks), 1)
        self.assertEqual(first_checks[0]["id"], "frontend_build")
        self.assertEqual(second_checks[0]["id"], "frontend_build")
        self.assertEqual(first_checks[0]["status"], "running")
        self.assertEqual(second_checks[0]["status"], "passed")

        # 完成帧的 checks 必须反映最终的 test_results,且 sequence 单调递增。
        completed_checks = completed_frames[0].get("checks") or []
        self.assertEqual(len(completed_checks), 1)
        self.assertEqual(completed_checks[0]["id"], "frontend_build")
        self.assertEqual(completed_checks[0]["status"], "passed")
        sequences = [frame["sequence"] for frame in integration_frames]
        self.assertEqual(
            sequences,
            sorted(sequences),
            f"integration_test 步骤的 sequence 必须单调递增,实际: {sequences}",
        )

    def test_integration_check_detail_lists_each_check_name(self) -> None:
        """验证兼容详情逐项展示检查名称和状态，而不是只返回数量。"""

        detail = integration_test_check_summary(
            [
                {
                    "id": "frontend_build",
                    "name": "前端构建检查",
                    "status": "passed",
                    "required": True,
                    "evidence": "命令执行通过。",
                },
                {
                    "id": "frontend_lint",
                    "name": "前端 lint 通过",
                    "status": "skipped",
                    "required": False,
                    "evidence": "未声明 lint script。",
                },
            ]
        )
        self.assertIn("前端构建检查：已通过", detail)
        self.assertIn("前端 lint 通过：已跳过", detail)
        self.assertNotIn("2/2", detail)

    def test_failed_gate_projects_repair_step_before_integration_node_finishes(self) -> None:
        """修复 running 帧必须早于集成测试完成帧，避免 RepairPlanner 分析期间界面空白。"""

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeImmediateRepairProgressGraph(),
                payload={
                    "threadId": "thread-immediate-repair",
                    "runId": "run-immediate-repair",
                    "messages": [{"role": "user", "content": "run integration tests"}],
                    "forwardedProps": {"resumeFrom": "integration_test"},
                },
            )
            return [frame async for frame in stream]

        frames = _decode_agent_process_frames(asyncio.run(collect()))
        repair_frames = [
            frame
            for frame in frames
            if frame.get("id") == "workflow:small_task_repair"
            and frame.get("status") == "running"
        ]
        integration_terminal = next(
            frame
            for frame in frames
            if frame.get("id") == "workflow:integration_test"
            and frame.get("status") == "failed"
        )

        self.assertGreaterEqual(len(repair_frames), 1)
        self.assertLess(repair_frames[0]["sequence"], integration_terminal["sequence"])
        self.assertIn("局部修复任务", repair_frames[0]["title"])

    def test_integration_check_projection_preserves_unit_test_counts(self) -> None:
        """单元测试数量字段必须穿过 AG-UI 进度裁剪层到达前端。"""

        checks = integration_test_checks(
            [
                {
                    "id": "frontend_unit_tests",
                    "name": "前端单元测试",
                    "status": "passed",
                    "required": True,
                    "passed_tests": 3,
                    "total_tests": 3,
                }
            ]
        )

        self.assertEqual(checks[0]["passed_tests"], 3)
        self.assertEqual(checks[0]["total_tests"], 3)

    def test_integration_check_projection_preserves_performance_report_fields(self) -> None:
        """性能得分、核心指标和报告路径必须安全穿过 AG-UI 裁剪层。"""

        checks = integration_test_checks(
            [
                {
                    "id": "frontend_performance",
                    "name": "前端性能测试",
                    "status": "passed",
                    "required": False,
                    "advisory": True,
                    "performance_scores": {
                        "performance": 92,
                        "accessibility": 95,
                        "best_practices": 100,
                        "seo": 88,
                    },
                    "performance_metrics": {
                        "fcp": 900,
                        "lcp": 1800,
                        "tbt": 120,
                        "cls": 0.02,
                        "si": 1500,
                    },
                    "report_path": "/tmp/.xcodeagent/runtime/tests/frontend_performance/report.html",
                }
            ]
        )

        self.assertEqual(checks[0]["advisory"], True)
        self.assertEqual(checks[0]["performanceScores"]["performance"], 92)
        self.assertEqual(checks[0]["performanceScores"]["best_practices"], 100)
        self.assertEqual(checks[0]["performanceMetrics"]["lcp"], 1800)
        self.assertEqual(checks[0]["performanceMetrics"]["cls"], 0.02)
        self.assertTrue(checks[0]["reportPath"].endswith("report.html"))

    def test_repair_loop_emits_unique_attempt_steps_and_semantic_statuses(self) -> None:
        """多轮构建测试必须保留唯一步骤 ID、attempt 与测试失败终态。"""

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeRepairLoopGraph(),
                payload={
                    "threadId": "thread-repair-loop",
                    "runId": "run-repair-loop",
                    "messages": [{"role": "user", "content": "repair and retest"}],
                    "forwardedProps": {"resumeFrom": "build"},
                },
            )
            return [frame async for frame in stream]

        frames = _decode_agent_process_frames(asyncio.run(collect()))
        terminal_frames = [
            frame
            for frame in frames
            if frame.get("nodeName") in {"build", "integration_test", "small_task_repair"}
            and frame.get("status") != "running"
        ]

        self.assertEqual(
            [frame["id"] for frame in terminal_frames],
            [
                "workflow:build",
                "workflow:integration_test",
                "workflow:small_task_repair",
                "workflow:integration_test:2",
            ],
        )
        self.assertEqual([frame["attempt"] for frame in terminal_frames], [1, 1, 1, 2])
        self.assertEqual(
            [frame["iterationKind"] for frame in terminal_frames],
            ["initial_build", "initial_test", "initial", "retest"],
        )
        self.assertEqual(terminal_frames[1]["status"], "failed")
        self.assertNotIn("buildExecutionSlice", terminal_frames[2])

    def test_ephemeral_build_activity_updates_step_without_entering_workflow_history(self) -> None:
        """工具活动应实时更新构建卡，但不能写入 workflow-run 事件历史。"""

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeEphemeralBuildActivityGraph(),
                payload={
                    "threadId": "thread-tool-activity",
                    "runId": "run-tool-activity",
                    "messages": [{"role": "user", "content": "build page"}],
                    "forwardedProps": {"resumeFrom": "build"},
                },
            )
            return [frame async for frame in stream]

        frames = asyncio.run(collect())
        process_frames = _decode_agent_process_frames(frames)
        workflow_frames = _decode_workflow_run_frames(frames)
        activity_frame = next(
            frame
            for frame in process_frames
            if frame.get("buildExecutionSlice", {}).get("tasks", [{}])[0].get(
                "activeToolActivity"
            )
        )

        self.assertEqual(
            activity_frame["buildExecutionSlice"]["tasks"][0]["activeToolActivity"]["callId"],
            "read-page",
        )
        self.assertNotIn("read-page", json.dumps(workflow_frames, ensure_ascii=False))

    def test_stream_emits_ag_ui_frames_for_openai_backed_workflow(self) -> None:
        graph = FakeWorkflowGraph()

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "thread-1",
                    "runId": "run-1",
                    "messages": [{"role": "user", "content": "make a tiny app"}],
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        frames = asyncio.run(collect())
        payload = "\n".join(frames)

        self.assertIn("RUN_STARTED", payload)
        self.assertIn("TEXT_MESSAGE_START", payload)
        self.assertIn("TEXT_MESSAGE_CONTENT", payload)
        self.assertIn("CUSTOM", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn("workflow-run", payload)
        self.assertIn("workflow.run.finished", payload)
        self.assertIn("qualityGatePassed", payload)
        self.assertIn("requiresUserInput", payload)
        self.assertIn("requires_user_input", payload)
        self.assertIn("需要哪些角色", payload)

    def test_stream_exposes_project_planning_confirmation(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            project_plan_path = Path(workspace) / "project-plan.md"
            project_plan_path.write_text(
                "# 库存系统项目计划\n\n仅用于项目规划确认。",
                encoding="utf-8",
            )
            graph = FakeProjectPlanningWaitGraph(str(project_plan_path))

            async def collect() -> list[str]:
                stream = build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={
                        "threadId": "thread-1",
                        "runId": "run-1",
                        "messages": [{"role": "user", "content": "make inventory app"}],
                        "resumeFrom": "project_planning",
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

            frames = asyncio.run(collect())
            payload = "\n".join(frames)

        self.assertIn("project_plan_confirmation", payload)
        self.assertIn("confirmationArtifact", payload)
        self.assertIn("库存系统项目计划", payload)
        self.assertIn("project_plan", payload)
        self.assertIn("请确认项目规划书是否正确", payload)
        self.assertIn("project_planning", payload)
        self.assertIn("nodeName", payload)
        self.assertIn("project-plan.md", payload)
        self.assertNotIn("project-plan.json", payload)

    def test_confirmation_artifact_is_limited_to_the_active_gate(self) -> None:
        with tempfile.TemporaryDirectory() as workspace:
            requirement_path = Path(workspace) / "requirement-spec.md"
            project_plan_path = Path(workspace) / "project-plan.md"
            requirement_path.write_text("# 需求文档\n\n需求确认正文。", encoding="utf-8")
            project_plan_path.write_text("# 项目计划\n\n计划确认正文。", encoding="utf-8")

            requirement_artifact = _workflow_confirmation_artifact(
                {
                    "phase": "requirements",
                    "status": "requires_user_input",
                    "requirements_confirmed": True,
                    "requirement_spec_path": str(requirement_path),
                    "project_plan_path": str(project_plan_path),
                    "clarification": {
                        "mode": "requirement_document_confirmation",
                        "status": "requires_user_input",
                    },
                }
            )
            project_plan_artifact = _workflow_confirmation_artifact(
                {
                    "phase": "project_planning",
                    "status": "requires_user_input",
                    "requirement_spec_path": str(requirement_path),
                    "project_plan_path": str(project_plan_path),
                    "clarification": {
                        "mode": "project_plan_confirmation",
                        "status": "requires_user_input",
                    },
                }
            )

        self.assertIsNotNone(requirement_artifact)
        self.assertIsNotNone(project_plan_artifact)
        assert requirement_artifact is not None
        assert project_plan_artifact is not None
        self.assertEqual(requirement_artifact["id"], "requirement_spec")
        self.assertIn("需求确认正文", requirement_artifact["content"])
        self.assertNotIn("计划确认正文", requirement_artifact["content"])
        self.assertEqual(project_plan_artifact["id"], "project_plan")
        self.assertIn("计划确认正文", project_plan_artifact["content"])
        self.assertNotIn("需求确认正文", project_plan_artifact["content"])

        self.assertIsNone(
            _workflow_confirmation_artifact(
                {
                    "phase": "entity_source_binding",
                    "status": "requires_user_input",
                    "project_plan_path": "project-plan.md",
                    "clarification": {
                        "mode": "entity_source_binding",
                        "status": "requires_user_input",
                    },
                }
            )
        )
        self.assertIsNone(
            _workflow_confirmation_artifact(
                {
                    "phase": "requirements",
                    "status": "completed",
                    "requirement_spec_path": "requirement-spec.md",
                    "clarification": {
                        "mode": "requirement_document_confirmation",
                        "status": "clear",
                    },
                }
            )
        )

    def test_unconfirmed_requirement_projects_draft_markdown_artifact(self) -> None:
        """需求分析草稿未确认时应投射为右侧可见的草稿工件。"""

        with tempfile.TemporaryDirectory() as workspace:
            requirement_path = (
                Path(workspace)
                / ".xcodeagent"
                / "drafts"
                / "specs"
                / "requirement-spec.md"
            )
            requirement_path.parent.mkdir(parents=True, exist_ok=True)
            requirement_path.write_text("# 当前需求草稿\n", encoding="utf-8")
            artifact = _workflow_confirmation_artifact(
                {
                    "phase": "requirements",
                    "status": "requires_user_input",
                    "requirements_confirmed": False,
                    "requirement_spec_path": str(requirement_path),
                    "clarification": {
                        "mode": "requirement_document_confirmation",
                        "status": "requires_user_input",
                    },
                }
            )

        self.assertIsNotNone(artifact)
        assert artifact is not None
        self.assertEqual(artifact["id"], "requirement_spec")
        self.assertIn("当前需求草稿", artifact["content"])

    def test_stream_passes_forwarded_workspace_and_editor_mode_to_graph_state(self) -> None:
        graph = FakeWorkflowGraph()
        with tempfile.TemporaryDirectory() as workspace:
            async def collect() -> list[str]:
                stream = build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={
                        "threadId": "thread-1",
                        "runId": "run-1",
                        "messages": [{"role": "user", "content": "make a tiny app"}],
                        "forwardedProps": {
                            "workspaceRoot": workspace,
                            "editorMode": "frontend",
                        },
                    },
                    accept="text/event-stream",
                )
                return [frame async for frame in stream]

            asyncio.run(collect())

            self.assertEqual(graph.initial_states[0]["workspace"], workspace)
        self.assertEqual(graph.initial_states[0]["editor_mode"], "frontend")

    def test_project_state_schema_preserves_workspace(self) -> None:
        seen_workspaces: list[str | None] = []
        seen_editor_modes: list[str | None] = []

        def capture_workspace(state: ProjectState) -> dict:
            seen_workspaces.append(state.get("workspace"))
            seen_editor_modes.append(state.get("editor_mode"))
            return {"phase": "capture_workspace", "timeline": ["capture_workspace"]}

        builder = StateGraph(ProjectState)
        builder.add_node("capture_workspace", capture_workspace)
        builder.add_edge(START, "capture_workspace")
        builder.add_edge("capture_workspace", END)

        graph = builder.compile()
        result = graph.invoke(
            {
                "request": "make a tiny app",
                "workspace": "/Users/sbw/Documents/example-workspace",
                "editor_mode": "backend",
                "timeline": [],
            }
        )

        self.assertEqual(
            seen_workspaces,
            ["/Users/sbw/Documents/example-workspace"],
        )
        self.assertEqual(
            result["workspace"],
            "/Users/sbw/Documents/example-workspace",
        )
        self.assertEqual(seen_editor_modes, ["backend"])
        self.assertEqual(result["editor_mode"], "backend")

    def test_stream_exposes_code_changes_payload(self) -> None:
        graph = FakeCodeChangesGraph()

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "thread-1",
                    "runId": "run-1",
                    "messages": [{"role": "user", "content": "add data.json"}],
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        payload = "\n".join(asyncio.run(collect()))

        self.assertIn("codeChanges", payload)
        self.assertIn("codeChangesSummary", payload)
        self.assertIn("data.json", payload)
        self.assertIn("file.write", payload)

    def test_ui_confirmation_progress_preserves_checkpoint_clarification(self) -> None:
        """换一换 run 期间的 ui_confirmation.progress 帧必须保留 checkpoint 的
        clarification（mode=ui_design_confirmation + pages），不能清空为 {}，
        否则前端 ApplicationPlanningQuestionPanel 走默认空表单分支白屏几十秒。"""

        checkpoint_clarification = {
            "mode": "ui_design_confirmation",
            "status": "requires_user_input",
            "questions": [],
            "pages": [
                {"pageId": "home_page", "name": "首页", "code": "old code", "status": "confirmed"},
                {"pageId": "list_page", "name": "列表页", "code": "", "status": "pending"},
            ],
        }
        graph = FakeUiConfirmationProgressGraph()

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "thread-ui-progress",
                    "runId": "run-ui-progress",
                    "messages": [{"role": "user", "content": "换一换首页设计稿"}],
                    "forwardedProps": {
                        "workflowScope": "application_planning",
                        "resumeFrom": "ui_confirmation",
                        "resumeState": {
                            "runId": "run-prev",
                            "threadId": "thread-ui-progress",
                            "summary": {
                                "status": "requires_user_input",
                                "phase": "ui_confirmation",
                                "clarification": checkpoint_clarification,
                            },
                            "state": {
                                "status": "requires_user_input",
                                "phase": "ui_confirmation",
                                "clarification": checkpoint_clarification,
                            },
                            "events": [
                                {
                                    "type": "workflow.node.completed",
                                    "nodeName": "ui_confirmation",
                                    "status": "requires_user_input",
                                }
                            ],
                        },
                    },
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        frames = asyncio.run(collect())
        workflow_frames = _decode_workflow_run_frames(frames)
        # 找到 progress 帧（status=running 且 clarification.mode 仍为 ui_design_confirmation）。
        progress_frames = [
            frame
            for frame in workflow_frames
            if frame.get("summary", {}).get("status") == "running"
            and frame.get("summary", {}).get("phase") == "ui_confirmation"
        ]
        self.assertTrue(progress_frames, "应至少有一个 ui_confirmation 进度帧")
        progress = progress_frames[0]
        clarification = progress.get("summary", {}).get("clarification", {})
        self.assertEqual(
            clarification.get("mode"),
            "ui_design_confirmation",
            "进度帧必须保留 checkpoint 的 clarification.mode，不能清空",
        )
        self.assertTrue(
            isinstance(clarification.get("pages"), list)
            and len(clarification.get("pages", [])) > 0,
            "进度帧必须保留 pages，前端据此渲染左侧页面列表",
        )

    def test_requirements_node_started_does_not_carry_checkpoint_clarification(self) -> None:
        """需求阶段提交后 node.started 起始帧不能带上 checkpoint 的 clarification。

        需求阶段 checkpoint 的 clarification.status=requires_user_input，若 node.started
        帧带上它，前端 awaitingUserInput=true → showingProgress=false，会卡在按钮禁用的
        确认面板不动（大模型在后台输出但页面无反应）。只有 UI 确认阶段（resume_from=
        ui_confirmation）才允许带上。
        """

        checkpoint_clarification = {
            "mode": "requirement_document_confirmation",
            "status": "requires_user_input",
            "questions": [{"id": "user_roles", "question": "需要哪些角色？"}],
        }
        graph = FakeWorkflowGraph()

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "thread-req-start",
                    "runId": "run-req-start",
                    "messages": [{"role": "user", "content": "补充角色信息后继续"}],
                    "forwardedProps": {
                        "workflowScope": "application_planning",
                        "resumeFrom": "requirements",
                        "resumeState": {
                            "runId": "run-prev",
                            "threadId": "thread-req-start",
                            "summary": {
                                "status": "requires_user_input",
                                "phase": "requirements",
                                "clarification": checkpoint_clarification,
                            },
                            "state": {
                                "status": "requires_user_input",
                                "phase": "requirements",
                                "clarification": checkpoint_clarification,
                            },
                            "events": [
                                {
                                    "type": "workflow.node.completed",
                                    "nodeName": "requirements",
                                    "status": "requires_user_input",
                                }
                            ],
                        },
                    },
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        frames = asyncio.run(collect())
        workflow_frames = _decode_workflow_run_frames(frames)
        # node.started 帧：status=running 且 phase=requirements（FakeWorkflowGraph 首节点）。
        started_frames = [
            frame
            for frame in workflow_frames
            if frame.get("summary", {}).get("status") == "running"
        ]
        self.assertTrue(started_frames, "应至少有一个 node.started 起始帧")
        for started in started_frames:
            clarification = started.get("summary", {}).get("clarification", {})
            self.assertNotEqual(
                clarification.get("status"),
                "requires_user_input",
                "需求阶段 node.started 帧不能带 checkpoint 的 requires_user_input clarification，"
                "否则前端卡在确认面板不切进度页",
            )


if __name__ == "__main__":
    unittest.main()
