"""发起新迭代继承上一版本已完成的产物进度。

新迭代保留已有工程代码，上一版本已开发的产物仍然存在；而本轮构建范围是**增量**的
（只覆盖改动的产物），不会再去开发其余已完成的产物。若继承丢失，这些产物会回到
"未开发"，"全部产物完成"的测试门禁就永远满足不了——表现为开发阶段走完却无入口。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    completed_development_artifacts,
    ensure_application_lifecycle,
    load_application_lifecycle,
)


def _completed() -> dict[str, str]:
    return {
        "initialDevelopmentStatus": "completed",
        "completedAt": "2026-09-19T00:00:00Z",
        "completedRunId": "run-1",
        "completedThreadId": "thread-1",
    }


class CompletedArtifactsInheritanceTests(unittest.TestCase):
    def test_keeps_only_completed_entries(self) -> None:
        """只继承 completed：pending/in_progress 描述的是"本轮要做什么"，必须重新推导。"""

        inherited = completed_development_artifacts(
            {
                "pages": {
                    "page_done": _completed(),
                    "page_pending": {"initialDevelopmentStatus": "pending"},
                    "page_active": {"initialDevelopmentStatus": "in_progress"},
                },
                "entities": {
                    "entity_done": {"initialDevelopmentStatus": "completed"},
                    "entity_pending": {"initialDevelopmentStatus": "pending"},
                },
                "endpoints": {
                    "contract": {
                        "endpoint_done": _completed(),
                        "endpoint_pending": {"initialDevelopmentStatus": "pending"},
                    }
                },
            }
        )
        assert inherited is not None
        self.assertEqual(list(inherited.pages), ["page_done"])
        self.assertEqual(list(inherited.entities), ["entity_done"])
        self.assertEqual({k: list(v) for k, v in inherited.endpoints.items()}, {"contract": ["endpoint_done"]})
        # 目录尚未重新校准，不能带着"目录不可用"的假错误进入新迭代。
        self.assertIsNone(inherited.catalog_error)

    def test_returns_none_when_nothing_to_inherit(self) -> None:
        """没有任何已完成事实时返回 None，让新 lifecycle 保持全新状态。"""

        self.assertIsNone(
            completed_development_artifacts(
                {"pages": {"page": {"initialDevelopmentStatus": "pending"}}}
            )
        )

    def test_returns_none_for_missing_or_malformed_input(self) -> None:
        """入参缺失或不可解析时不继承，绝不写入错误的完成事实。"""

        self.assertIsNone(completed_development_artifacts(None))
        self.assertIsNone(completed_development_artifacts("bogus"))
        # completed 缺少时间/runId/threadId 证据：schema 会拒绝，整体不继承。
        self.assertIsNone(
            completed_development_artifacts(
                {"pages": {"page": {"initialDevelopmentStatus": "completed"}}}
            )
        )

    def test_created_lifecycle_carries_inherited_artifacts(self) -> None:
        """经 ensure_application_lifecycle 落盘的新迭代 lifecycle 必须带着继承结果。"""

        inherited = completed_development_artifacts({"pages": {"page_done": _completed()}})
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            state = ensure_application_lifecycle(
                workspace,
                application_id="app-1",
                application_name="测试应用",
                inherited_artifacts=inherited,
            )
            self.assertEqual(
                state.development_artifacts.pages["page_done"].initial_development_status,
                "completed",
            )
            # 落盘后重读仍保留（新迭代后续读的就是这份权威状态）。
            reloaded = load_application_lifecycle(workspace)
            assert reloaded is not None
            self.assertEqual(
                reloaded.development_artifacts.pages["page_done"].initial_development_status,
                "completed",
            )
            # 初始化阶段仍从收集需求开始，不因继承产物而跳过规划。
            self.assertEqual(
                reloaded.initialization.stage, ApplicationLifecycleStage.COLLECTING_REQUIREMENT
            )
            self.assertEqual(reloaded.initialization.status, ApplicationLifecycleStatus.PENDING)

    def test_created_lifecycle_without_inheritance_starts_empty(self) -> None:
        """不传继承时不改变既有行为：新应用仍从零开始。"""

        with tempfile.TemporaryDirectory() as raw:
            state = ensure_application_lifecycle(
                Path(raw), application_id="app-2", application_name="新应用"
            )
            self.assertEqual(state.development_artifacts.pages, {})


if __name__ == "__main__":
    unittest.main()
