from __future__ import annotations

import asyncio
import unittest

from app.protocols.ag_ui_action_stream import (
    AgUiActionResult,
    build_ag_ui_action_stream,
)


class AgUiActionStreamTests(unittest.TestCase):
    def test_success_uses_the_standard_lifecycle_and_result_key(self) -> None:
        async def operation() -> AgUiActionResult:
            return AgUiActionResult(data={"value": 42}, message="完成。")

        async def collect() -> str:
            stream = build_ag_ui_action_stream(
                payload={"threadId": "thread-1", "runId": "run-1"},
                event_name="example-action",
                state_key="example",
                run_id_prefix="example",
                operation=operation,
                error_message_prefix="操作失败",
            )
            return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        for event_type in (
            "RUN_STARTED",
            "TEXT_MESSAGE_START",
            "CUSTOM",
            "STATE_SNAPSHOT",
            "TEXT_MESSAGE_CONTENT",
            "TEXT_MESSAGE_END",
            "RUN_FINISHED",
        ):
            self.assertIn(event_type, payload)
        self.assertIn("example-action", payload)
        self.assertIn("\"example\"", payload)
        self.assertIn("\"value\":42", payload)

    def test_failure_is_returned_as_a_finished_structured_run(self) -> None:
        async def operation() -> AgUiActionResult:
            raise ValueError("invalid input")

        async def collect() -> str:
            stream = build_ag_ui_action_stream(
                payload={"threadId": "thread-2", "runId": "run-2"},
                event_name="example-action",
                state_key="example",
                run_id_prefix="example",
                operation=operation,
                error_message_prefix="操作失败",
                error_data=lambda _exc: {"action": "save"},
            )
            return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn("RUN_FINISHED", payload)
        self.assertIn("\"status\":\"failed\"", payload)
        self.assertIn("ValueError", payload)
        self.assertIn("invalid input", payload)
        self.assertIn("\"action\":\"save\"", payload)


if __name__ == "__main__":
    unittest.main()
