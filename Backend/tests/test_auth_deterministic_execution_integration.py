"""auth-guard Planning、精确 capability 依赖与确定性 Build execution 集成回归。"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.graph.subgraphs.build import _execute_ready_tasks
from app.services.authorization_frontend_projection import _render_resources
from app.services.authorization_resource_catalog import (
    compile_frontend_resource_catalog,
    resource_catalog_fingerprint,
)
from app.services.authorization_resource_inspection import (
    inspect_authorization_resource_catalog,
)
from app.services.build_result_coordinator import apply_agent_results_with_scheduler
from app.services.build_scheduler import select_ready_build_batch
from app.services.build_task_planner import tasks_from_build_task_plan
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.deterministic_unit_candidates import (
    AUTH_GUARD_UNIT_ID,
    AUTH_RESOURCES_PATH,
    build_auth_guard_candidate,
)
from app.services.planning_frozen import plain_json
from app.services.scope_assembly import assemble_scope_build_task_plan
from app.services.unit_generation_contracts import (
    AttemptIdentity,
    CandidateAttempt,
)
from app.services.unit_generation_requirements import resolve_generation_requirements
from tests.dag_planning_baseline_fixtures import (
    build_context,
    confirmed_baseline,
    execution_scope,
    project_plan,
    workspace_snapshot,
)


PAGE_TASK_ID = "customers:page"
ROUTES_PATH = "frontend/src/constants/routes.tsx"


class AuthDeterministicExecutionIntegrationTests(unittest.TestCase):
    """证明当前 fingerprint provider 从 Planning 到 Build 的累计行为。"""

    def test_first_r1_plans_assembles_and_executes_single_writer(self) -> None:
        """首次 R1 生成唯一 auth Task，经真实 dispatch 只写 resources.ts。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            routes = self._write(workspace / ROUTES_PATH, "// routes-r1\n")
            cycle = self._planning_cycle(self._r1(), workspace)

            self.assertEqual(cycle["requirements"].planning_unit_ids, (AUTH_GUARD_UNIT_ID,))
            auth_task = self._auth_candidate_task(cycle)
            assembled = self._assemble(cycle, auth_task=auth_task)
            applied, results, change_sets = self._execute_auth(
                assembled,
                cycle["plan"],
                workspace,
                auth_task["id"],
            )

            self.assertEqual(results[0]["status"], "completed")
            self.assertEqual(results[0]["changed_files"], [AUTH_RESOURCES_PATH])
            result_task = next(item for item in applied["tasks"] if item["id"] == auth_task["id"])
            self.assertEqual(result_task["status"], "completed")
            self.assertEqual(
                {item["path"] for change_set in change_sets for item in change_set["files"]},
                {AUTH_RESOURCES_PATH},
            )
            self.assertEqual(routes.read_text(encoding="utf-8"), "// routes-r1\n")

    def test_same_r1_reuses_current_provider_without_new_task(self) -> None:
        """相同 R1 的 confirmed provider 和已满足 workspace 都不会制造重复 Task。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            first = self._planning_cycle(self._r1(), workspace)
            auth_task = self._auth_candidate_task(first)
            assembled = self._assemble(first, auth_task=auth_task)
            applied, _, _ = self._execute_auth(assembled, first["plan"], workspace, auth_task["id"])
            confirmed = self._confirm(applied["build_task_plan"])

            same = self._planning_cycle(self._r1(), workspace, confirmed_plan=confirmed)

            self.assertEqual(same["requirements"].planning_unit_ids, ())
            self.assertIsNone(self._auth_candidate_task(same))
            self.assertEqual(
                same["reuse_facts"].retained_task_ids_by_unit[AUTH_GUARD_UNIT_ID],
                (auth_task["id"],),
            )

    def test_r1_to_r2_appends_and_executes_new_provider(self) -> None:
        """R2 追加新 fingerprint provider，保留 R1 并用新 Task 覆盖真实输出。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            r1_cycle = self._planning_cycle(self._r1(), workspace)
            r1_task = self._auth_candidate_task(r1_cycle)
            r1_assembled = self._assemble(r1_cycle, auth_task=r1_task)
            r1_applied, _, _ = self._execute_auth(
                r1_assembled, r1_cycle["plan"], workspace, r1_task["id"]
            )
            r1_confirmed = self._confirm(r1_applied["build_task_plan"])

            r2_cycle = self._planning_cycle(self._r2(), workspace, confirmed_plan=r1_confirmed)
            r2_task = self._auth_candidate_task(r2_cycle)
            r2_assembled = self._assemble(r2_cycle, auth_task=r2_task)
            registry = r2_assembled["task_registry"]

            self.assertNotEqual(r1_task["id"], r2_task["id"])
            self.assertIn(r1_task["id"], registry)
            self.assertIn(r2_task["id"], registry)
            self._execute_auth(r2_assembled, r2_cycle["plan"], workspace, r2_task["id"])
            inspection = inspect_authorization_resource_catalog(
                workspace,
                r2_cycle["catalog"],
                workspace_revision=workspace_snapshot()["workspace_revision"],
            )
            self.assertEqual(inspection["status"], "satisfied")

    def test_failed_r1_is_retained_but_r2_page_waits_only_for_r2(self) -> None:
        """历史 R1 failed 仍保留，但 R2 Page 只依赖并等待当前 R2 provider。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            r1_cycle = self._planning_cycle(self._r1(), workspace)
            r1_task = self._auth_candidate_task(r1_cycle)
            r1_confirmed = self._confirm(
                self._assemble(r1_cycle, auth_task=r1_task),
                statuses={r1_task["id"]: "failed"},
            )
            r2_cycle = self._planning_cycle(self._r2(), workspace, confirmed_plan=r1_confirmed)
            r2_task = self._auth_candidate_task(r2_cycle)
            assembled = self._assemble(r2_cycle, auth_task=r2_task)
            page = assembled["task_registry"][PAGE_TASK_ID]

            self.assertEqual(assembled["task_registry"][r1_task["id"]]["status"], "failed")
            self.assertIn(r1_task["id"], assembled["task_registry"])
            self.assertEqual(page["dependencies"].count(r2_task["id"]), 1)
            self.assertNotIn(r1_task["id"], page["dependencies"])

            applied, _, _ = self._execute_auth(
                assembled, r2_cycle["plan"], workspace, r2_task["id"]
            )
            next_batch = select_ready_build_batch(applied["tasks"])
            self.assertIn(PAGE_TASK_ID, next_batch["ready_task_ids"])

    def test_workspace_presatisfied_r2_creates_no_fake_auth_task(self) -> None:
        """workspace 已有完整 R2 时 Planning 使用外部能力，Assembly 不创建 auth Task。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            plan = self._r2()
            catalog = compile_frontend_resource_catalog(plan["authorization_manifest"])
            self._write(
                workspace / AUTH_RESOURCES_PATH,
                _render_resources(catalog.frontend_resources()),
            )
            cycle = self._planning_cycle(plan, workspace)
            assembled = self._assemble(cycle)

            self.assertEqual(cycle["requirements"].planning_unit_ids, ())
            self.assertIsNone(self._auth_candidate_task(cycle))
            self.assertTrue(cycle["reuse_facts"].external_capabilities)
            self.assertFalse(
                any(
                    item["unit_id"] == AUTH_GUARD_UNIT_ID
                    for item in assembled["task_registry"].values()
                )
            )
            page_dependencies = assembled["task_registry"][PAGE_TASK_ID]["dependencies"]
            self.assertFalse(
                any(
                    assembled["task_registry"][task_id]["unit_id"] == AUTH_GUARD_UNIT_ID
                    for task_id in page_dependencies
                )
            )

    def test_page_depends_on_current_confirmed_provider_until_execution(self) -> None:
        """当前 confirmed provider 未执行时阻塞 Page，完成后 Page 才进入 ready batch。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            initial = self._planning_cycle(self._r2(), workspace)
            current_task = self._auth_candidate_task(initial)
            confirmed = self._confirm(self._assemble(initial, auth_task=current_task))
            current = self._planning_cycle(self._r2(), workspace, confirmed_plan=confirmed)
            assembled = self._assemble(current)
            page = assembled["task_registry"][PAGE_TASK_ID]

            self.assertEqual(current["requirements"].planning_unit_ids, ())
            self.assertEqual(page["dependencies"].count(current_task["id"]), 1)
            first_batch = select_ready_build_batch(tasks_from_build_task_plan(assembled))
            self.assertIn(current_task["id"], first_batch["ready_task_ids"])
            self.assertNotIn(PAGE_TASK_ID, first_batch["ready_task_ids"])

            applied, _, _ = self._execute_auth(
                assembled,
                current["plan"],
                workspace,
                current_task["id"],
            )
            self.assertIn(
                PAGE_TASK_ID,
                select_ready_build_batch(applied["tasks"])["ready_task_ids"],
            )

    def _planning_cycle(
        self,
        plan: dict,
        workspace: Path,
        *,
        confirmed_plan: dict | None = None,
    ) -> dict:
        """运行当前资源检查、ReuseFacts、职责判定和 deterministic Candidate planning。"""

        snapshot = workspace_snapshot()
        scope = execution_scope(name="customers")
        context = build_context(plan, scope)
        baseline = confirmed_plan or self._business_baseline(plan)
        skeleton = ensure_build_unit_skeleton(plan, snapshot, baseline)
        catalog = compile_frontend_resource_catalog(plan["authorization_manifest"])
        inspection = inspect_authorization_resource_catalog(
            workspace,
            catalog,
            workspace_revision=snapshot["workspace_revision"],
        )
        reuse_facts = resolve_reuse_facts(
            confirmed_plan=baseline,
            unit_skeleton=skeleton,
            build_context=context,
            workspace_snapshot=snapshot,
            formal_plan=plan,
            auth_resource_inspection=inspection,
        )
        requirements = resolve_generation_requirements(
            required_unit_ids=[AUTH_GUARD_UNIT_ID],
            build_execution_scope=scope,
            unit_skeleton=skeleton,
            reuse_facts=reuse_facts,
            formal_target=plan,
        )
        fingerprint = resource_catalog_fingerprint(catalog)
        candidate = build_auth_guard_candidate(
            unit_id=AUTH_GUARD_UNIT_ID,
            resource_catalog=catalog,
            fingerprint=fingerprint,
            generation_requirements=requirements,
        )
        return {
            "plan": plan,
            "scope": scope,
            "context": context,
            "skeleton": skeleton,
            "catalog": catalog,
            "fingerprint": fingerprint,
            "reuse_facts": reuse_facts,
            "requirements": requirements,
            "candidate": candidate,
            "confirmed_plan": baseline,
        }

    def _assemble(
        self,
        cycle: dict,
        *,
        auth_task: dict | None = None,
    ) -> dict:
        """把真实 auth Candidate 与完整 retained business history 组装为 DAG。"""

        requirements_by_unit = dict(cycle["requirements"].generation_requirements_by_unit)
        candidates_by_unit = {}
        if auth_task is not None:
            candidates_by_unit[AUTH_GUARD_UNIT_ID] = self._candidate(
                AUTH_GUARD_UNIT_ID,
                [auth_task],
                "a",
            )
        result = assemble_scope_build_task_plan(
            base_confirmed_plan=cycle["confirmed_plan"],
            skeleton_plan=cycle["skeleton"],
            project_plan=cycle["plan"],
            build_context=cycle["context"],
            reuse_facts=cycle["reuse_facts"],
            generation_requirements_by_unit=requirements_by_unit,
            candidates_by_unit=candidates_by_unit,
        )
        return plain_json(result.assembled_plan)

    def _auth_candidate_task(self, cycle: dict) -> dict | None:
        """从 deterministic planning 输出读取唯一 auth Task，无缺项时返回 None。"""

        candidate = cycle["candidate"]
        if candidate is None:
            return None
        task_items = candidate.get("tasks")
        if not isinstance(task_items, list) or len(task_items) != 1:
            raise AssertionError("auth planning 必须返回唯一 Task。")
        return task_items[0]

    def _execute_auth(
        self,
        assembled: dict,
        plan: dict,
        workspace: Path,
        expected_task_id: str,
    ) -> tuple[dict, list[dict], list[dict]]:
        """经真实 ready selection、registry dispatch 和结果协调执行当前 auth provider。"""

        tasks = tasks_from_build_task_plan(assembled)
        batch = select_ready_build_batch(tasks)
        auth_tasks = [task for task in batch["ready_tasks"] if task["id"] == expected_task_id]
        self.assertEqual([task["id"] for task in auth_tasks], [expected_task_id])
        results, change_sets = _execute_ready_tasks(
            {
                "workspace": str(workspace),
                "project_plan": plan,
                "build_task_plan": assembled,
            },
            auth_tasks,
        )
        applied = apply_agent_results_with_scheduler(
            project_plan=plan,
            build_task_plan=assembled,
            tasks=tasks,
            existing_results=[],
            new_results=results,
            stage="build",
        )
        return applied, results, change_sets

    def _confirm(
        self,
        plan: dict,
        *,
        statuses: dict[str, str] | None = None,
    ) -> dict:
        """把已组装计划转为下一轮只读 confirmed baseline，并保留指定执行状态。"""

        confirmed = deepcopy(plain_json(plan))
        confirmed["status"] = "ready"
        confirmed["confirmation_status"] = "confirmed"
        confirmed["confirmed_at"] = "2026-09-07T00:00:00+08:00"
        for task_id, status in (statuses or {}).items():
            confirmed["task_registry"][task_id]["status"] = status
        return confirmed

    def _candidate(
        self,
        unit_id: str,
        tasks: list[dict],
        marker: str,
    ) -> CandidateAttempt:
        """为已由对应生成器产生的 Task 包装有效 CandidateAttempt 身份。"""

        return CandidateAttempt(
            candidate_id=f"candidate-{marker * 32}",
            identity=AttemptIdentity(
                planning_run_id="planning-run-auth-integration",
                unit_id=unit_id,
                generation_round=1,
                attempt_in_round=1,
                attempt_id=f"attempt-{marker * 32}",
            ),
            input_fingerprint=f"input-{unit_id}",
            status="valid",
            tasks=tasks,
            validation_issues=(),
            generation_metadata={"source": "integration-test"},
        )

    def _r1(self) -> dict:
        """构造首版已确认 authorization manifest。"""

        return project_plan(authorization=True)

    def _r2(self) -> dict:
        """在 R1 全量目录中追加一个操作资源，形成新的完整 fingerprint。"""

        plan = self._r1()
        plan["authorization_manifest"]["resources"].append(
            {
                "resourceKey": "customers_export",
                "type": "operation",
                "targetResourceRef": "action:customers:export",
            }
        )
        return plan

    def _business_baseline(self, plan: dict) -> dict:
        """构造无 auth provider 的有效 ConfirmedPlan，并仅保留当前 Page 待执行。"""

        baseline = confirmed_baseline(plan, execution_scope(name="customers"))
        for task_id, retained_task in baseline["task_registry"].items():
            retained_task["status"] = "pending" if task_id == PAGE_TASK_ID else "completed"
        return baseline

    def _write(self, path: Path, content: str) -> Path:
        """写入隔离 workspace fixture 文件。"""

        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return path


if __name__ == "__main__":
    unittest.main()
