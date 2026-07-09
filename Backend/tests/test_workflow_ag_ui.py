from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from langgraph.graph import END, START, StateGraph

from app.graph.state import ProjectState
from app.protocols.workflow_visualization import build_workflow_ag_ui_stream


class FakeWorkflowGraph:
    def __init__(self) -> None:
        self.initial_states: list[dict] = []

    async def astream(self, initial_state, *, config, stream_mode):
        self.initial_states.append(initial_state)
        yield {
            "classify_request_complexity": {
                "phase": "classify_request_complexity",
                "status": "completed",
                "request_complexity": "complex",
                "message": "classified",
                "timeline": ["classified"],
            }
        }
        yield {
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


class WorkflowAgUiStreamTests(unittest.TestCase):
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
        self.assertIn("TOOL_CALL_START", payload)
        self.assertIn("TOOL_CALL_ARGS", payload)
        self.assertIn("TOOL_CALL_END", payload)
        self.assertIn("TOOL_CALL_RESULT", payload)
        self.assertIn("ask_user", payload)
        self.assertIn("CUSTOM", payload)
        self.assertIn("STATE_SNAPSHOT", payload)
        self.assertIn("RUN_FINISHED", payload)
        self.assertIn("workflow-run", payload)
        self.assertIn("workflow.run.finished", payload)
        self.assertIn("qualityGatePassed", payload)
        self.assertIn("requiresUserInput", payload)
        self.assertIn("requires_user_input", payload)
        self.assertIn("需要哪些角色", payload)

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


if __name__ == "__main__":
    unittest.main()
