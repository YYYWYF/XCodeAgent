"""T3.2 Candidate dependency allowlist 与 same-unit cycle 回归。"""

from __future__ import annotations

import unittest

from app.services.unit_candidate_validator import (
    validate_unit_candidate_dependencies,
)


UNIT_ID = "page:orders"


def _task(task_id: str, dependencies: list[str] | None = None) -> dict:
    """构造已通过 Raw Candidate Parser 的最小 Task。"""

    return {"id": task_id, "dependencies": dependencies or []}


def _context(*summaries: dict, dependency_unit_ids: tuple[str, ...] = ()) -> dict:
    """构造当前 Unit 的冻结依赖上下文 JSON 投影。"""

    return {
        "dependency_unit_ids": list(dependency_unit_ids),
        "retained_task_summaries": list(summaries),
    }


class UnitCandidateDependencyTests(unittest.TestCase):
    def _assert_retryable_current_unit(self, issue) -> None:
        """断言模型依赖错误只重试当前 Unit，并保持结构化归因。"""

        self.assertEqual(issue.level, "unit")
        self.assertEqual(issue.category, "generation")
        self.assertEqual(issue.retry_unit_ids, (UNIT_ID,))
        self.assertTrue(issue.retryable)

    def test_candidate_internal_dependency_is_allowed(self) -> None:
        """Candidate Task 可以依赖同一 Candidate 中的另一个 Task。"""

        tasks = [_task("task-adapter"), _task("task-page", ["task-adapter"])]

        self.assertEqual(
            validate_unit_candidate_dependencies(tasks, _context(), UNIT_ID),
            [],
        )

    def test_same_unit_retained_dependency_is_allowed(self) -> None:
        """Context 显式提供的同 Unit retained Task ID 可以被 Candidate 引用。"""

        tasks = [_task("task-page", ["task-retained"])]
        contexts = (
            _context({"id": "task-retained"}),
            _context({"id": "task-retained", "unit_id": UNIT_ID}),
        )
        for context in contexts:
            with self.subTest(context=context):
                self.assertEqual(
                    validate_unit_candidate_dependencies(tasks, context, UNIT_ID),
                    [],
                )

    def test_cross_unit_task_id_is_rejected(self) -> None:
        """显式属于其他 Unit 的 retained Task 不能进入当前 allowlist。"""

        tasks = [_task("task-page", ["task-api"])]
        context = _context(
            {"id": "task-api", "unit_id": "frontend:api-client"}
        )

        issues = validate_unit_candidate_dependencies(tasks, context, UNIT_ID)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "CANDIDATE_DEPENDENCY_CROSS_UNIT")
        self.assertEqual(issues[0].unit_ids, (UNIT_ID, "frontend:api-client"))
        self._assert_retryable_current_unit(issues[0])

    def test_unknown_task_id_is_rejected(self) -> None:
        """既不在 Candidate 也不在 retained slice 的 Task ID 必须被拒绝。"""

        issues = validate_unit_candidate_dependencies(
            [_task("task-page", ["task-unknown"])],
            _context(),
            UNIT_ID,
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "CANDIDATE_DEPENDENCY_UNKNOWN")
        self.assertEqual(issues[0].details["dependency_id"], "task-unknown")
        self._assert_retryable_current_unit(issues[0])

    def test_unit_id_cannot_be_used_as_task_dependency(self) -> None:
        """当前或依赖 Unit 的 ID 不能冒充 Task ID。"""

        tasks = [_task("task-page", ["frontend:api-client"])]
        context = _context(dependency_unit_ids=("frontend:api-client",))

        issues = validate_unit_candidate_dependencies(tasks, context, UNIT_ID)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "CANDIDATE_DEPENDENCY_UNIT_TARGET")
        self._assert_retryable_current_unit(issues[0])

    def test_direct_self_cycle_is_rejected(self) -> None:
        """Candidate Task 直接依赖自身时产生精确 self-cycle Issue。"""

        issues = validate_unit_candidate_dependencies(
            [_task("task-page", ["task-page"])],
            _context(),
            UNIT_ID,
        )

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "CANDIDATE_DEPENDENCY_SELF_CYCLE")
        self.assertEqual(issues[0].task_ids, ("task-page",))
        self._assert_retryable_current_unit(issues[0])

    def test_multi_task_same_unit_cycle_is_rejected(self) -> None:
        """只在当前 Candidate 子图内检测并归因多 Task 环。"""

        tasks = [
            _task("task-a", ["task-c"]),
            _task("task-b", ["task-a"]),
            _task("task-c", ["task-b"]),
        ]

        issues = validate_unit_candidate_dependencies(tasks, _context(), UNIT_ID)

        self.assertEqual(len(issues), 1)
        self.assertEqual(issues[0].code, "CANDIDATE_DEPENDENCY_CYCLE")
        self.assertEqual(issues[0].task_ids, ("task-a", "task-b", "task-c"))
        self._assert_retryable_current_unit(issues[0])


if __name__ == "__main__":
    unittest.main()
