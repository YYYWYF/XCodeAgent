"""PlanningRun 轻量投影 schema：合法 JSON 仍须满足严格类型、状态和范围契约。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from app.services import planning_run as sm
from app.services.planning_run_contracts import PlanningRunProjection
from app.workspace.planning_run_documents import (
    load_planning_run, planning_run_json_path, project_planning_run, write_planning_run_atomic,
)
from tests.planning_run_fixtures import (
    AT, UNIT, exhausted, issue, phases, repair_decision, run, start,
)


class PlanningRunProjectionTests(unittest.TestCase):
    def setUp(self):
        """每项测试创建独立轻量投影路径，避免触碰真实工作区状态。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.path = planning_run_json_path(self.workspace)
        self.path.parent.mkdir(parents=True)
        self.payload = project_planning_run(run())

    def assert_rejected(self, payload):
        """确认语法合法但契约非法的数据被报告，并且原文件不被删除或重写。"""

        raw = json.dumps(payload, ensure_ascii=False)
        self.path.write_text(raw, encoding="utf-8")
        with self.assertRaises(ValidationError):
            load_planning_run(self.workspace)
        self.assertEqual(self.path.read_text(encoding="utf-8"), raw)

    def test_valid_json_with_invalid_top_level_schema_is_rejected(self):
        """覆盖用户反例、错误标量类型、枚举、范围和不允许的字段。"""

        self.assert_rejected({"status": "active", "revision": "abc"})
        for changes in (
            {"revision": "1"}, {"revision": True}, {"revision": -1},
            {"status": "succeeded"}, {"phase": "unknown"}, {"planning_run_id": ""},
            {"global_repair_round": 3}, {"global_repair_limit": 2.0},
            {"global_issues": {}}, {"unit_states": []}, {"required_unit_ids": "page:orders"},
            {"build_execution_scope": []}, {"candidates": {}}, {"unknown": "value"},
        ):
            with self.subTest(changes=changes):
                self.assert_rejected({**self.payload, **changes})

    def test_every_serialized_root_and_unit_field_is_required(self):
        """不能用领域初态默认值补造缺失的 revision、phase、轮次或 Unit 证据。"""

        for field in self.payload:
            with self.subTest(root_field=field):
                self.assert_rejected({key: value for key, value in self.payload.items() if key != field})
        for field in self.payload["unit_states"][UNIT]:
            with self.subTest(unit_field=field):
                payload = deepcopy(self.payload)
                del payload["unit_states"][UNIT][field]
                self.assert_rejected(payload)

    def test_scope_state_and_attempt_identity_must_be_consistent(self):
        """语法正确也不能包含未知/重复 Unit、终态在途任务或跨 Run Attempt。"""

        for changes in (
            {"required_unit_ids": (UNIT, UNIT)}, {"required_unit_ids": ("page:other",)},
            {"planning_unit_ids": ()}, {"status": "failed"}, {"status": "cancelled"},
            {"unit_states": {"page:other": self.payload["unit_states"][UNIT]}},
        ):
            with self.subTest(changes=changes):
                self.assert_rejected({**self.payload, **changes})
        for changes in (
            {"generation_status": "failed"}, {"attempt_in_round": "1"},
            {"generation_strategy": "deterministic", "attempt_in_round": 1, "total_attempts": 1},
            {"generation_round": 2}, {"kind": "unknown"}, {"latest_candidate_id": "orphan"},
        ):
            with self.subTest(unit_changes=changes):
                payload = deepcopy(self.payload)
                payload["unit_states"][UNIT].update(changes)
                self.assert_rejected(payload)
        generating, _ = start(sm.begin_generation(run(), at=AT))
        payload = project_planning_run(generating)
        payload["unit_states"][UNIT]["expected_identity"]["planning_run_id"] = "other-run"
        self.assert_rejected(payload)

    def test_nested_issues_are_strictly_validated(self):
        """轻量诊断也受 Issue 契约约束，禁止无目标的 retryable 或未知类别。"""

        for changes in ({"retry_unit_ids": []}, {"retryable": "true"}, {"category": "unknown"}):
            with self.subTest(changes=changes):
                payload = deepcopy(self.payload)
                payload["global_issues"] = [{**issue(level="global").model_dump(mode="json"), **changes}]
                self.assert_rejected(payload)

    def test_all_current_lifecycle_snapshots_round_trip_without_candidate_bodies(self):
        """正常、耗尽、重开、在途和终态投影均合法，不需伪造 Candidate 正文来读取。"""

        fixtures = list(phases().values())
        generated, _ = start(sm.begin_generation(run(), at=AT))
        closed = exhausted(sm.begin_generation(run(), at=AT))
        reopened = sm.begin_global_repair(phases()["global_check"], repair_decision(issue(level="global")), at=AT)
        fixtures.extend((generated, closed, reopened, sm.cancel(generated, at=AT),
                         sm.fail(generated, issue(retryable=False), at=AT)))
        for state in fixtures:
            with self.subTest(phase=state.phase, status=state.status, revision=state.revision):
                expected = state.model_dump(mode="json", exclude={"candidates"})
                self.assertEqual(project_planning_run(state), expected)
                self.assertEqual(Path(write_planning_run_atomic(self.workspace, state)), self.path)
                loaded = load_planning_run(self.workspace)
                self.assertEqual(loaded, expected)
                self.assertNotIn("candidates", loaded)
                model = PlanningRunProjection.model_validate(loaded)
                self.assertEqual(PlanningRunProjection.model_validate_json(model.model_dump_json()), model)

    def test_projection_model_is_frozen_and_has_no_execution_state(self):
        """投影冻结内部数据，导出独立 JSON 副本，不能假扮完整 Run。"""

        model = PlanningRunProjection.model_validate(self.payload)
        self.assertNotIsInstance(model, sm.PlanningRun)
        with self.assertRaises(ValidationError):
            model.revision = 99
        with self.assertRaises(TypeError):
            model.unit_states[UNIT] = None
        dumped = model.model_dump(mode="json")
        dumped["unit_states"].clear()
        self.assertIn(UNIT, model.unit_states)

    def test_non_object_json_is_rejected_without_changing_file(self):
        """顶层 list、null 和标量继续被拒绝，保留既有读取错误语义。"""

        for payload in ([], None, "active", 1):
            raw = json.dumps(payload)
            self.path.write_text(raw, encoding="utf-8")
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                load_planning_run(self.workspace)
            self.assertEqual(self.path.read_text(encoding="utf-8"), raw)
