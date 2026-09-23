"""Build 执行范围不改变应用级初次开发完成门禁。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    start_workbench_execution,
    write_application_lifecycle,
)
from app.services.development_artifacts import (
    complete_initial_development,
    development_artifact_key,
    in_scope_unit_ids,
    out_of_scope_keys,
    refresh_development_artifacts,
)
# 以 test_ 开头的导入名会被 pytest 当成测试函数收集，这里显式改名。
from app.services.development_artifacts import test_entry_gate as evaluate_test_entry_gate
from app.domain.development_artifacts import DevelopmentArtifactTarget

_PLAN_RELATIVE = Path(".devagentstudio/plans/build-task-plan.json")


def _page_unit(page_id: str, *, in_scope: bool) -> dict[str, object]:
    """构造构建计划里的页面 Unit；只有范围内的 Unit 带 input_fingerprint。"""

    unit: dict[str, object] = {"status": "prepared" if in_scope else "not_prepared", "task_ids": []}
    if in_scope:
        unit["input_fingerprint"] = f"fingerprint-{page_id}"
    return unit


class IncrementalScopeGateTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        plans = self.workspace / ".devagentstudio/plans"
        plans.mkdir(parents=True)
        (plans / "product-plan.json").write_text(
            json.dumps(
                {
                    "confirmation_status": "confirmed",
                    "pages": [{"pageId": "home_welcome"}, {"pageId": "hello_agent"}],
                }
            ),
            encoding="utf-8",
        )
        (plans / "technical-plan.json").write_text(
            json.dumps({"confirmation_status": "confirmed", "entities": [], "api_contracts": []}),
            encoding="utf-8",
        )
        state = create_application_lifecycle(application_id="test", application_name="测试")
        state.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
        state.initialization.status = ApplicationLifecycleStatus.COMPLETED
        write_application_lifecycle(self.workspace, state)

    def write_plan(self, units: dict[str, object]) -> None:
        (self.workspace / _PLAN_RELATIVE).write_text(
            json.dumps({"build_units": units}), encoding="utf-8"
        )

    def test_scope_reads_units_with_input_fingerprint(self) -> None:
        self.write_plan(
            {
                "page:home_welcome": _page_unit("home_welcome", in_scope=False),
                "page:hello_agent": _page_unit("hello_agent", in_scope=True),
            }
        )
        self.assertEqual(in_scope_unit_ids(self.workspace), {"page:hello_agent"})

    def test_missing_plan_reports_unknown_scope(self) -> None:
        """计划不可用时返回 None，调用方退回"全部产物都算"的保守口径。"""

        self.assertIsNone(in_scope_unit_ids(self.workspace))

    def test_out_of_scope_keys_marks_unchanged_page(self) -> None:
        self.write_plan(
            {
                "page:home_welcome": _page_unit("home_welcome", in_scope=False),
                "page:hello_agent": _page_unit("hello_agent", in_scope=True),
            }
        )
        targets = [
            DevelopmentArtifactTarget(type="page", pageId="home_welcome"),
            DevelopmentArtifactTarget(type="page", pageId="hello_agent"),
        ]
        self.assertEqual(out_of_scope_keys(self.workspace, targets), ["page:home_welcome"])
        self.assertEqual(development_artifact_key(targets[0]), "page:home_welcome")

    def test_gate_keeps_unfinished_page_outside_current_build_scope(self) -> None:
        """当前 Build 仅包含一个页面时，另一个未完成页面仍阻止进入测试。"""

        self.write_plan(
            {
                "page:home_welcome": _page_unit("home_welcome", in_scope=False),
                "page:hello_agent": _page_unit("hello_agent", in_scope=True),
            }
        )
        state = refresh_development_artifacts(self.workspace)
        self.assertEqual(state.development_artifacts.out_of_scope, ["page:home_welcome"])

        # 本轮只完成了范围内的那个产物。
        start_workbench_execution(
            self.workspace,
            scope="page",
            target_id="hello_agent",
            page_id="hello_agent",
            thread_id="thread-hello",
            run_id="run-hello",
            phase="api_design_readiness_gate",
            initial_development_entry=True,
        )
        complete_initial_development(self.workspace, run_id="run-hello")

        gate = evaluate_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertFalse(gate.allowed)
        self.assertEqual((gate.total, gate.completed), (2, 1))
        self.assertEqual([b.page_id for b in gate.blockers], ["home_welcome"])

    def test_gate_still_blocks_when_in_scope_work_unfinished(self) -> None:
        """当前目标与范围外目标都没完成时，两个目标都必须阻塞。"""

        self.write_plan(
            {
                "page:home_welcome": _page_unit("home_welcome", in_scope=False),
                "page:hello_agent": _page_unit("hello_agent", in_scope=True),
            }
        )
        gate = evaluate_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.total, 2)
        self.assertEqual([b.page_id for b in gate.blockers], ["home_welcome", "hello_agent"])

    def test_without_plan_all_targets_still_count(self) -> None:
        """没有构建计划时不能放宽：全部产物都要完成（首次构建即如此）。"""

        gate = evaluate_test_entry_gate(refresh_development_artifacts(self.workspace))
        self.assertFalse(gate.allowed)
        self.assertEqual(gate.total, 2)


if __name__ == "__main__":
    unittest.main()
