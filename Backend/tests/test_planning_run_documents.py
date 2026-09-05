from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

from app.services import planning_run as sm
from app.workspace.planning_run_documents import (
    delete_planning_run,
    load_planning_run,
    planning_run_json_path,
    write_planning_run_atomic,
)
from tests.planning_run_fixtures import AT, ready, run


class PlanningRunDocumentTests(unittest.TestCase):
    """验证 PlanningRun 轻量快照的独立持久化生命周期。"""

    def test_create_and_load_lightweight_snapshot(self) -> None:
        """首次写入应创建固定文件，并可读取 Run 与 Unit 轻量状态。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            planning_run = run()

            written_path = write_planning_run_atomic(state, planning_run)
            loaded = load_planning_run(state)

            self.assertEqual(Path(written_path), planning_run_json_path(state))
            self.assertEqual(loaded["planning_run_id"], planning_run.planning_run_id)
            self.assertEqual(loaded["revision"], 0)
            self.assertEqual(loaded["unit_states"]["page:orders"]["generation_status"], "pending")

    def test_update_persists_latest_revision(self) -> None:
        """覆盖写入应保存转换后的最新 revision 和 phase。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            original = run()
            updated = sm.begin_generation(original, at=AT)
            write_planning_run_atomic(state, original)

            write_planning_run_atomic(state, updated)

            loaded = load_planning_run(state)
            self.assertEqual(loaded["revision"], updated.revision)
            self.assertEqual(loaded["phase"], "generating_units")

    def test_delete_removes_only_planning_run(self) -> None:
        """删除临时 Run 快照不得影响 confirmed 或 pending plan。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            planning_path = Path(write_planning_run_atomic(state, run()))
            confirmed = planning_path.parent / "build-task-plan.json"
            pending = planning_path.parent / "build-task-plan.pending.json"
            confirmed.write_text('{"confirmation_status":"confirmed"}\n', encoding="utf-8")
            pending.write_text('{"confirmation_status":"pending"}\n', encoding="utf-8")

            self.assertTrue(delete_planning_run(state))

            self.assertFalse(planning_path.exists())
            self.assertTrue(confirmed.exists())
            self.assertTrue(pending.exists())

    def test_absent_load_and_delete_are_idempotent(self) -> None:
        """文件不存在时读取返回空，删除返回未删除且不创建目录。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}

            self.assertIsNone(load_planning_run(state))
            self.assertFalse(delete_planning_run(state))
            self.assertFalse(planning_run_json_path(state).parent.exists())

    def test_corrupt_file_is_reported_without_deletion(self) -> None:
        """损坏 JSON 应向调用方报告，不能静默视为不存在或自动删除。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            path = planning_run_json_path(state)
            path.parent.mkdir(parents=True)
            path.write_text("{corrupt", encoding="utf-8")

            with self.assertRaises(json.JSONDecodeError):
                load_planning_run(state)

            self.assertEqual(path.read_text(encoding="utf-8"), "{corrupt")

    def test_candidate_body_is_not_serialized(self) -> None:
        """有效 Candidate 的任务正文和 metadata 只能留在内存快照。"""

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            planning_run = ready(sm.begin_generation(run(), at=AT))
            candidate_id = planning_run.unit_states["page:orders"].latest_candidate_id
            candidate = planning_run.candidates[candidate_id]
            candidate = candidate.model_copy(
                update={
                    "tasks": ({"id": "candidate-body-marker", "unit_id": "page:orders"},),
                    "generation_metadata": {"raw_marker": "candidate-metadata-marker"},
                }
            )
            planning_run = planning_run.model_copy(
                update={"candidates": {candidate_id: candidate}}
            )

            write_planning_run_atomic(state, planning_run)

            raw = planning_run_json_path(state).read_text(encoding="utf-8")
            loaded = load_planning_run(state)
            self.assertNotIn("candidates", loaded)
            self.assertNotIn("candidate-body-marker", raw)
            self.assertNotIn("candidate-metadata-marker", raw)
            self.assertEqual(
                loaded["unit_states"]["page:orders"]["latest_candidate_id"],
                candidate_id,
            )


if __name__ == "__main__":
    unittest.main()
