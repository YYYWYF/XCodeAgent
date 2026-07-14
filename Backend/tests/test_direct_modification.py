from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.registry import AgentBundle
from app.graph.nodes.modification import direct_modification


class FakeAgent:
    def __init__(self, content: str) -> None:
        self.content = content
        self.payloads: list[dict[str, object]] = []

    def invoke(self, payload: dict[str, object]) -> dict[str, list[SimpleNamespace]]:
        self.payloads.append(payload)
        return {"messages": [SimpleNamespace(content=self.content)]}


class DirectModificationTests(unittest.TestCase):
    def test_frontend_direct_modification_uses_frontend_agent(self) -> None:
        frontend = FakeAgent("frontend completed")
        bundle = AgentBundle(
            frontend=frontend,
            data_source=FakeAgent("unused"),
            test=FakeAgent("unused"),
            repair_planner=FakeAgent("unused"),
        )
        capture_calls: list[dict[str, object]] = []

        def capture(**kwargs):
            capture_calls.append(kwargs)
            return SimpleNamespace(value=kwargs["action"](), code_change_set=None)

        with (
            patch("app.agents.create_agent_bundle", return_value=bundle),
            patch(
                "app.graph.nodes.modification.capture_agent_file_changes",
                side_effect=capture,
            ),
        ):
            result = direct_modification(
                {
                    "request": "把提交按钮文案改成保存",
                    "workspace": "/tmp/demo",
                    "editor_mode": "frontend",
                }
            )

        self.assertEqual(result["tasks"][0]["owner"], "frontend")
        self.assertEqual(result["build_results"][0]["owner"], "frontend")
        self.assertEqual(result["build_results"][0]["agent_note"], "frontend completed")
        self.assertEqual(capture_calls[0]["source_tool"], "frontend.direct_modification")
        self.assertEqual(len(frontend.payloads), 1)

    def test_data_source_direct_modification_uses_data_source_agent(self) -> None:
        data_source = FakeAgent("backend completed")
        bundle = AgentBundle(
            frontend=FakeAgent("unused"),
            data_source=data_source,
            test=FakeAgent("unused"),
            repair_planner=FakeAgent("unused"),
        )

        with (
            patch("app.agents.create_agent_bundle", return_value=bundle),
            patch(
                "app.graph.nodes.modification.capture_agent_file_changes",
                side_effect=lambda **kwargs: SimpleNamespace(
                    value=kwargs["action"](),
                    code_change_set=None,
                ),
            ),
        ):
            result = direct_modification(
                {
                    "request": "修复库存查询接口的空值处理",
                    "editor_mode": "backend",
                }
            )

        self.assertEqual(result["tasks"][0]["owner"], "data_source")
        self.assertEqual(result["build_results"][0]["agent_note"], "backend completed")
        self.assertEqual(len(data_source.payloads), 1)

    def test_direct_modification_rejects_missing_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "validated frontend or data_source owner"):
            direct_modification(
                {
                    "request": "做一个修改",
                }
            )


if __name__ == "__main__":
    unittest.main()
