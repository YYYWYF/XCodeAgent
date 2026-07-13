from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph

from app.graph.state import ProjectState
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import _workflow_confirmation_artifact


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
                    "requirement_spec_path": str(requirement_path),
                    "project_plan_path": str(project_plan_path),
                    "clarification": {
                        "mode": "requirement_spec_confirmation",
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
                    "phase": "detail_confirmation",
                    "status": "requires_user_input",
                    "project_plan_path": "project-plan.md",
                    "clarification": {
                        "mode": "detail_review",
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
                        "mode": "requirement_spec_confirmation",
                        "status": "clear",
                    },
                }
            )
        )

    def test_stream_passes_forwarded_workspace_to_graph_state(self) -> None:
        graph = FakeWorkflowGraph()

        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=graph,
                payload={
                    "threadId": "thread-1",
                    "runId": "run-1",
                    "messages": [{"role": "user", "content": "make a tiny app"}],
                    "forwardedProps": {
                        "workspaceRoot": "/Users/sbw/Documents/example-workspace"
                    },
                },
                accept="text/event-stream",
            )
            return [frame async for frame in stream]

        asyncio.run(collect())

        self.assertEqual(
            graph.initial_states[0]["workspace"],
            "/Users/sbw/Documents/example-workspace",
        )

    def test_project_state_schema_preserves_workspace(self) -> None:
        seen_workspaces: list[str | None] = []

        def capture_workspace(state: ProjectState) -> dict:
            seen_workspaces.append(state.get("workspace"))
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


if __name__ == "__main__":
    unittest.main()
