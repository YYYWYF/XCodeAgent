from __future__ import annotations

import asyncio
import unittest

from app.protocols.ag_ui_action_stream import (
    AgUiActionProgress,
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

    def test_progress_operation_streams_incremental_state_before_result(self) -> None:
        """长耗时操作应在最终结果前发送结构化阶段与文本增量。"""

        async def operation(report) -> AgUiActionResult:
            await report(
                AgUiActionProgress(
                    stage="designing",
                    message="正在设计页面…",
                    percent=40,
                    data={"action": "plan"},
                )
            )
            await asyncio.sleep(0)
            return AgUiActionResult(data={"action": "plan"}, message="完成。")

        async def collect() -> str:
            stream = build_ag_ui_action_stream(
                payload={"threadId": "thread-3", "runId": "run-3"},
                event_name="example-action",
                state_key="example",
                run_id_prefix="example",
                progress_operation=operation,
                error_message_prefix="操作失败",
            )
            return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn('"status":"in_progress"', payload)
        self.assertIn('"stage":"designing"', payload)
        self.assertIn('"percent":40', payload)
        self.assertLess(payload.index('"status":"in_progress"'), payload.index('"status":"completed"'))

    def test_streaming_operation_forwards_model_text_before_result(self) -> None:
        """模型文本增量应在最终结构化结果前按原顺序发送。"""

        async def operation(report, report_text) -> AgUiActionResult:
            await report(
                AgUiActionProgress(
                    stage="generating",
                    message="正在生成…",
                    percent=50,
                )
            )
            await report_text('{"pages":')
            await report_text("[]}")
            return AgUiActionResult(data={"value": 42}, message="完成。")

        async def collect() -> str:
            stream = build_ag_ui_action_stream(
                payload={"threadId": "thread-4", "runId": "run-4"},
                event_name="example-action",
                state_key="example",
                run_id_prefix="example",
                streaming_operation=operation,
                error_message_prefix="操作失败",
            )
            return "\n".join([frame async for frame in stream])

        payload = asyncio.run(collect())

        self.assertIn('"delta":"{\\"pages\\":"', payload)
        self.assertIn('"delta":"[]}"', payload)
        self.assertLess(payload.index('{\\"pages\\":'), payload.index('"status":"completed"'))
        self.assertNotIn('"delta":"正在生成', payload)


if __name__ == "__main__":
    unittest.main()
