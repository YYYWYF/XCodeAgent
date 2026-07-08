from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace

from app.protocols.workflow_visualization import build_workflow_ag_ui_stream


class FakeWorkflowGraph:
    async def astream(self, initial_state, *, config, stream_mode):
        yield {
            "classify_request_complexity": {
                "phase": "classify_request_complexity",
                "status": "completed",
                "complexity": "simple",
                "message": "classified",
                "timeline": ["classified"],
            }
        }

    def get_state(self, config):
        return SimpleNamespace(
            values={
                "phase": "finalize_project",
                "status": "completed",
                "summary": "done",
                "timeline": ["classified", "done"],
                "quality_gate_passed": True,
            }
        )


class WorkflowAgUiStreamTests(unittest.TestCase):
    def test_stream_emits_ag_ui_frames_for_openai_backed_workflow(self) -> None:
        async def collect() -> list[str]:
            stream = build_workflow_ag_ui_stream(
                graph=FakeWorkflowGraph(),
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


if __name__ == "__main__":
    unittest.main()
