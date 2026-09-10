from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.graph.nodes.tasks import _existing_build_task_plan
from app.workspace.task_documents import (
    build_task_plan_json_path,
    load_confirmed_build_task_plan,
    write_build_task_plan_json,
)


def _confirmed_plan(task_id: str = "formal-task") -> dict:
    """构造内容可区分的已确认 v3 DAG，验证来源而非只验证确认标记。"""

    return {
        "version": "1.0.0",
        "schema_version": "build-dag.v3",
        "status": "ready",
        "confirmation_status": "confirmed",
        "confirmed_at": "2026-09-01T00:00:00+00:00",
        "task_registry": {
            task_id: {
                "id": task_id,
                "unit_id": "page:home",
                "owner": "frontend",
                "status": "completed",
                "dependencies": [],
            }
        },
        "task_graph": {
            "schema_version": "build-task-graph.v3",
            "nodes": [task_id],
            "edges": [],
            "topological_order": [task_id],
            "validation": {"is_valid": True, "errors": []},
        },
    }


class ConfirmedBuildTaskPlanLoaderTests(unittest.TestCase):
    def setUp(self) -> None:
        """为每个测试创建独立工作区，避免读取真实应用计划。"""

        temporary_workspace = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_workspace.cleanup)
        self.workspace = Path(temporary_workspace.name)
        self.state = {"workspace": str(self.workspace)}
        self.formal_path = build_task_plan_json_path(self.state)

    def test_loads_formal_confirmed_plan_exactly(self) -> None:
        """正式 confirmed 文件应原样返回，保留完整任务内容。"""

        formal = _confirmed_plan()
        write_build_task_plan_json(self.state, formal)

        self.assertEqual(load_confirmed_build_task_plan(self.workspace), formal)
        self.assertEqual(load_confirmed_build_task_plan(str(self.workspace)), formal)
        self.assertEqual(_existing_build_task_plan(self.state), formal)

    def test_no_formal_returns_empty_even_with_confirmed_checkpoint(self) -> None:
        """没有正式文件时不使用 checkpoint，也不创建任何产物目录。"""

        self.assertIsNone(load_confirmed_build_task_plan(self.workspace))
        self.assertEqual(
            _existing_build_task_plan({**self.state, "build_task_plan": _confirmed_plan()}),
            {},
        )
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_pending_file_is_never_a_baseline(self) -> None:
        """即使 pending 文件错误标记为 confirmed，也不能代替正式路径。"""

        pending_path = self.formal_path.with_name("build-task-plan.pending.json")
        pending_path.parent.mkdir(parents=True)
        for status in ("pending", "confirmed"):
            with self.subTest(confirmation_status=status):
                pending = {**_confirmed_plan("pending-task"), "confirmation_status": status}
                pending_path.write_text(json.dumps(pending), encoding="utf-8")

                self.assertIsNone(load_confirmed_build_task_plan(self.workspace))
                self.assertEqual(
                    _existing_build_task_plan({
                        **self.state,
                        "build_task_plan": pending,
                        "build_task_plan_path": str(pending_path),
                    }),
                    {},
                )

    def test_newer_pending_checkpoint_cannot_override_formal(self) -> None:
        """更新且有效的 pending checkpoint 与 sidecar 都不能覆盖 confirmed 文件。"""

        formal = _confirmed_plan()
        write_build_task_plan_json(self.state, formal)
        pending = {
            **_confirmed_plan("newer-pending-task"),
            "version": "99.0.0",
            "confirmation_status": "pending",
            "confirmed_at": None,
        }
        pending_path = self.formal_path.with_name("build-task-plan.pending.json")
        pending_path.write_text(json.dumps(pending), encoding="utf-8")
        state = {
            **self.state,
            "build_task_plan": pending,
            "build_task_plan_path": str(pending_path),
        }
        original_state = deepcopy(state)

        self.assertEqual(_existing_build_task_plan(state), formal)
        self.assertEqual(load_confirmed_build_task_plan(self.workspace), formal)
        self.assertEqual(state, original_state)

    def test_different_confirmed_checkpoint_cannot_override_formal(self) -> None:
        """即便 checkpoint 也已确认且版本更新，仍只返回正式文件内容。"""

        formal = _confirmed_plan()
        write_build_task_plan_json(self.state, formal)
        checkpoint = {**_confirmed_plan("checkpoint-task"), "version": "99.0.0"}

        self.assertEqual(
            _existing_build_task_plan({**self.state, "build_task_plan": checkpoint}),
            formal,
        )

    def test_nonconfirmed_formal_never_falls_back_to_checkpoint(self) -> None:
        """正式文件必须精确标记 confirmed，其他值或缺失都不授予基线资格。"""

        for status in ("pending", "abandoned", "failed", "CONFIRMED", None):
            with self.subTest(confirmation_status=status):
                formal = _confirmed_plan()
                if status is None:
                    formal.pop("confirmation_status")
                else:
                    formal["confirmation_status"] = status
                write_build_task_plan_json(self.state, formal)

                self.assertIsNone(load_confirmed_build_task_plan(self.workspace))
                with self.assertRaisesRegex(ValueError, "正式文件存在"):
                    _existing_build_task_plan({
                        **self.state,
                        "build_task_plan": _confirmed_plan("checkpoint-task"),
                    })

    def test_failed_or_invalid_formal_is_not_a_baseline(self) -> None:
        """保留既有有效 DAG 约束，确认标记不能让失败或无效计划成为基线。"""

        invalid_plans = [
            [],
            {**_confirmed_plan(), "status": "failed"},
            {**_confirmed_plan(), "schema_version": "unsupported"},
            {**_confirmed_plan(), "task_graph": None},
            {**_confirmed_plan(), "task_graph": {"validation": {"is_valid": False}}},
            {**_confirmed_plan(), "task_graph": {"validation": {"is_valid": "true"}}},
        ]
        self.formal_path.parent.mkdir(parents=True)
        for formal in invalid_plans:
            with self.subTest(formal=formal):
                self.formal_path.write_text(json.dumps(formal), encoding="utf-8")
                self.assertIsNone(load_confirmed_build_task_plan(self.workspace))

    def test_loading_does_not_modify_or_normalize_files(self) -> None:
        """读取及修改返回对象都不改写文件内容、修改时间或目录清单。"""

        formal = _confirmed_plan()
        formal.pop("status")
        write_build_task_plan_json(self.state, formal)
        original_bytes = self.formal_path.read_bytes()
        original_mtime = self.formal_path.stat().st_mtime_ns
        original_paths = sorted(self.workspace.rglob("*"))

        loaded = load_confirmed_build_task_plan(self.workspace)
        self.assertEqual(loaded, formal)
        loaded["task_registry"].clear()

        self.assertEqual(load_confirmed_build_task_plan(self.workspace), formal)
        self.assertEqual(self.formal_path.read_bytes(), original_bytes)
        self.assertEqual(self.formal_path.stat().st_mtime_ns, original_mtime)
        self.assertEqual(sorted(self.workspace.rglob("*")), original_paths)

    def test_malformed_json_is_reported_without_checkpoint_fallback(self) -> None:
        """正式文件损坏必须暴露读取错误，不能悄悄使用 checkpoint。"""

        self.formal_path.parent.mkdir(parents=True)
        self.formal_path.write_text("{broken json", encoding="utf-8")

        with self.assertRaises(json.JSONDecodeError):
            _existing_build_task_plan({**self.state, "build_task_plan": _confirmed_plan()})
        self.assertEqual(self.formal_path.read_text(encoding="utf-8"), "{broken json")

    def test_read_errors_are_not_treated_as_missing_formal(self) -> None:
        """权限等读取错误必须上抛，不能被混同为不存在正式计划。"""

        write_build_task_plan_json(self.state, _confirmed_plan())
        with patch(
            "app.workspace.task_documents.load_build_task_plan_json",
            side_effect=PermissionError("formal plan is unreadable"),
        ), self.assertRaises(PermissionError):
            load_confirmed_build_task_plan(self.workspace)


if __name__ == "__main__":
    unittest.main()
