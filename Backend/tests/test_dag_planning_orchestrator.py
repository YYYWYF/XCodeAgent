"""T6.4/T9.2 全新有限并发链路集成 Gate，模型传输替身之外均使用真实服务。"""

import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.services.dag_planning_orchestrator import DagPlanningError, ValidatedAssembledPlan, plan_dag_sequential
from app.services.planning_frozen import plain_json
from app.services.unit_generation import generate_unit_candidate_once
from app.services.unit_generation_contracts import UnitGenerationPolicy
from app.workspace.planning_run_documents import load_planning_run
from tests.dag_planning_baseline_fixtures import build_context, confirmed_baseline, execution_scope, project_plan
from tests.dag_planning_orchestrator_fixtures import model_tasks, planning_inputs, shared_inputs
from tests.planning_run_fixtures import AT
from tests.test_unit_generation_contracts import _policy_payload
from tests.test_unit_generation_orchestrator import _settings


class ConcurrentPlanningIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        """使用临时工作区记录模型调用、冻结 Context 与已发布阶段。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = {"workspace": directory.name}
        self.calls = []
        self.phases = []
        self.invalid_first = None
        self.collision_id = None
        self.offline = False
        self.wrong_page_entry = False
        self.policy = UnitGenerationPolicy(**_policy_payload())

    async def _generate(self, job, **kwargs):
        """模型层返回可控 JSON，真实 Prompt/session/parser/Local 均继续执行。"""

        self.calls.append((job, kwargs))
        tasks = model_tasks(job)
        if self.wrong_page_entry and job.identity.unit_id.startswith("page:"):
            path = "frontend/src/pages/Incorrect/index.tsx"
            tasks[0]["target_files"] = [path]
            tasks[0]["allowed_paths"] = [path]
            tasks[0]["change_scope"][0]["path"] = path
            tasks[0]["deliverables"][0]["paths"] = [path]
        if job.identity.unit_id == self.invalid_first and job.identity.attempt_in_round == 1:
            tasks[0]["owner"] = "backend"
        if job.identity.unit_id == "page:b" and job.identity.generation_round == 1 and self.collision_id:
            tasks[0]["id"] = self.collision_id
        response = SimpleNamespace(content=json.dumps({"tasks": tasks}), response_metadata={"finish_reason": "stop"})
        model = SimpleNamespace(ainvoke=AsyncMock(side_effect=httpx.ReadError("offline") if self.offline else None, return_value=response))
        with patch("app.services.unit_generation.create_chat_model", return_value=model):
            result = await generate_unit_candidate_once(job, **kwargs)
        model.ainvoke.assert_awaited_once()
        return result

    async def _plan(self, inputs, **kwargs):
        """调用正式有限并发 API；唯一替身是单次模型工厂返回的固定传输响应。"""

        async def publish(projection):
            """只记录轻量 Controller 投影，便于核对阶段与预算。"""

            self.phases.append((projection["phase"], projection["global_repair_round"]))

        return await plan_dag_sequential(
            inputs, workspace_state=self.workspace, planning_run_id="sequential-run", workflow_run_id="workflow",
            thread_id="thread", policy=self.policy, settings=_settings(), generate_once=self._generate,
            publish=publish, now=lambda: AT, **kwargs,
        )

    def _assert_retained_contract(self, current, original):
        """保留历史合同并验证依赖不丢失；仅累计图派生的三项字段允许重算。"""

        current = plain_json(current)
        derived = {"dependencies", "dependency_rewrites", "parallel_with"}
        self.assertEqual({key: value for key, value in current.items() if key not in derived},
                         {key: value for key, value in original.items() if key not in derived})
        self.assertTrue(set(original.get("dependencies", ())) <= set(current.get("dependencies", ())))
        old_rewrites = original.get("dependency_rewrites", [])
        self.assertEqual(current.get("dependency_rewrites", [])[:len(old_rewrites)], old_rewrites)

    def _assert_formal_untouched(self):
        """写入可辨识的 Formal/Pending 证据，返回断言闭包以检查原始字节。"""

        plans = Path(self.workspace["workspace"]) / ".xcodeagent" / "plans"
        plans.mkdir(parents=True, exist_ok=True)
        files = {}
        for name in ("build-task-plan.json", "technical-plan.json", "build-task-plan.pending.json"):
            path = plans / name
            payload = json.dumps({"sentinel": name}).encode()
            path.write_bytes(payload)
            files[path] = payload

        def verify():
            """逐字节检查所有正式和 Pending 文件，避免只比较路径或摘要。"""

            for path, payload in files.items():
                self.assertEqual(path.read_bytes(), payload)
        return verify

    async def test_a_once_b_twice_c_once_with_retry_at_queue_tail(self):
        """A/C 首次成功，B 的 Local retry 回到首批 A/B/C 后面的队尾。"""

        inputs = planning_inputs()
        before = inputs.model_dump_json()
        verify = self._assert_formal_untouched()
        self.invalid_first = "page:b"
        result = await self._plan(inputs)
        self.assertIsInstance(result, ValidatedAssembledPlan)
        self.assertEqual(Counter(job.identity.unit_id for job, _ in self.calls), {"page:a": 1, "page:b": 2, "page:c": 1})
        self.assertEqual([job.identity.unit_id for job, _ in self.calls], ["page:a", "page:b", "page:c", "page:b"])
        self.assertEqual(result.planning_run.global_repair_round, 0)
        self.assertEqual(result.planning_run.phase, "validating")
        self.assertIn(("global_check", 0), self.phases)
        self.assertIn(("assembling", 0), self.phases)
        self.assertTrue(result.assembly.assembled_plan["task_graph"]["validation"]["is_valid"])
        self.assertNotIn("confirmation_status", result.assembly.assembled_plan)
        self.assertEqual(before, inputs.model_dump_json())
        # B 两个 Attempt 复用同一冻结输入；Context 不携带 A/C 的当前候选正文。
        self.assertEqual(self.calls[1][0].context, self.calls[3][0].context)
        for job, _ in self.calls:
            for other in ("page:a", "page:b", "page:c"):
                if other != job.identity.unit_id:
                    self.assertNotIn(f"{other}-r1", job.context.model_dump_json())
        verify()

    async def test_global_real_collision_repairs_only_b(self):
        """真实 retained/Candidate ID 冲突经过 Assembly/T4.1 后只重生 B。"""

        historical = await self._plan(planning_inputs(required=["page:history"]))
        baseline = plain_json(historical.assembly.assembled_plan)
        baseline.update(confirmation_status="confirmed", confirmed_at=AT)
        self.calls.clear()
        self.phases.clear()
        self.collision_id = next(iter(baseline["task_registry"]))
        verify = self._assert_formal_untouched()
        result = await self._plan(planning_inputs(baseline=baseline))
        self.assertEqual(Counter(job.identity.unit_id for job, _ in self.calls), {"page:a": 1, "page:b": 2, "page:c": 1})
        self.assertEqual(result.planning_run.global_repair_round, 1)
        self.assertEqual(result.planning_run.unit_states["page:a"].generation_round, 1)
        self.assertEqual(result.planning_run.unit_states["page:b"].generation_round, 2)
        self.assertEqual(result.planning_run.unit_states["page:c"].generation_round, 1)
        repaired = self.calls[-1]
        self.assertEqual(repaired[1]["global_feedback"][0].code, "GLOBAL_TASK_ID_COLLISION")
        statuses = Counter(candidate.status for candidate in result.planning_run.candidates.values())
        self.assertEqual(statuses, {"valid": 3, "superseded": 1})
        self._assert_retained_contract(result.assembly.assembled_plan["task_registry"][self.collision_id], baseline["task_registry"][self.collision_id])
        verify()

    async def test_shared_unit_retains_history_and_appends_missing_api_only(self):
        """共享 Unit 精确复用 adapter/旧接口，只追加 customers 缺失职责并保留所有历史合同。"""

        baseline = confirmed_baseline(project_plan(), execution_scope())
        baseline["task_registry"]["api:adapter"]["provides_capabilities"] = ["frontend.response-entity-adapter"]
        inputs = shared_inputs(baseline)
        result = await self._plan(inputs)
        self.assertEqual(len(self.calls), 1)
        context = self.calls[0][0].context
        self.assertEqual([item.requirement_id for item in context.generation_requirements], ["frontend.api_module:customers-api:customers.list"])
        self.assertEqual({item["id"] for item in context.dependency_context["retained_task_summaries"]}, {"api:adapter", "orders:api"})
        assembled = result.assembly.assembled_plan["task_registry"]
        self.assertEqual(len(assembled), len(baseline["task_registry"]) + 1)
        for key, task in baseline["task_registry"].items():
            self._assert_retained_contract(assembled[key], task)

    async def test_no_planning_units_still_assembles_and_validates(self):
        """全部职责已复用时模型零调用，仍通过 Barrier、真实 Assembly 与 Global。"""

        previous = await self._plan(planning_inputs())
        baseline = plain_json(previous.assembly.assembled_plan)
        baseline.update(confirmation_status="confirmed", confirmed_at=AT)
        self.calls.clear()
        result = await self._plan(planning_inputs(baseline=baseline))
        self.assertEqual(result.generation_requirements.planning_unit_ids, ())
        self.assertEqual(self.calls, [])
        self.assertEqual(result.assembly.candidate_task_ids, ())
        self.assertEqual(plain_json(result.assembly.assembled_plan["task_registry"]), baseline["task_registry"])
        self.assertEqual(result.planning_run.global_repair_round, 0)

    async def test_infrastructure_failure_has_no_content_retry_or_formal_writes(self):
        """首批 sibling 可已启动，但基础设施失败不进入内容重试或正式写入。"""

        self.offline = True
        verify = self._assert_formal_untouched()
        with self.assertRaises(DagPlanningError) as caught:
            await self._plan(planning_inputs())
        state = caught.exception.snapshot
        self.assertEqual((state.status, state.global_repair_round), ("failed", 0))
        self.assertEqual(state.failure.category, "infrastructure")
        self.assertCountEqual(
            [(job.identity.unit_id, job.identity.attempt_in_round) for job, _ in self.calls],
            [("page:a", 1), ("page:b", 1), ("page:c", 1)],
        )
        self.assertEqual(load_planning_run(self.workspace)["status"], "failed")
        self.assertFalse(any(phase == "assembling" for phase, _ in self.phases))
        verify()

    async def test_database_scope_runs_full_requirements_and_compilation(self):
        """真实数据库页面 Scope 的公共层、后端职责、前端接口和页面都经过新链路。"""

        plan = project_plan()
        scope = execution_scope()
        context = build_context(plan, scope)
        inputs = planning_inputs(plan=plan, scope=scope, context=context, required=context["required_unit_ids"])
        result = await self._plan(inputs)
        self.assertEqual(Counter(job.identity.unit_id for job, _ in self.calls), {
            "backend:bootstrap": 1, "backend:endpoint:orders-api:orders.list": 1,
            "frontend:api-client": 1, "page:orders": 1,
        })
        self.assertTrue(result.assembly.assembled_plan["task_graph"]["validation"]["is_valid"])
        self.assertEqual(result.planning_run.phase, "validating")

    async def test_pending_baseline_rejected_before_model_or_persistence(self):
        """Pending 不能进入新链路充当历史基线，前置失败不写 Run。"""

        baseline = confirmed_baseline(project_plan(), execution_scope())
        baseline["confirmation_status"] = "pending"
        with self.assertRaises(DagPlanningError) as caught:
            await self._plan(shared_inputs(baseline))
        self.assertIsNone(caught.exception.snapshot)
        self.assertEqual(caught.exception.issues[0].code, "CONFIRMED_BASELINE_INVALID")
        self.assertEqual(self.calls, [])
        self.assertIsNone(load_planning_run(self.workspace))

    async def test_empty_required_units_has_no_fake_candidate(self):
        """明确空 required 集合仍能完成空图检查，不构造占位任务或模型调用。"""

        result = await self._plan(planning_inputs(required=[]))
        self.assertEqual(self.calls, [])
        self.assertEqual(result.assembly.assembled_plan["task_registry"], {})
        self.assertEqual(result.planning_run.planning_unit_ids, ())

    async def test_compiler_error_without_attribution_is_fatal(self):
        """真实编译器报告页面入口错误时阻断，不解析字符串并猜测 Global 修复目标。"""

        self.wrong_page_entry = True
        verify = self._assert_formal_untouched()
        with self.assertRaises(DagPlanningError) as caught:
            await self._plan(planning_inputs())
        self.assertEqual(caught.exception.issues[0].code, "GLOBAL_COMPILED_PLAN_INVALID")
        self.assertTrue(caught.exception.issues[0].details["validation"]["errors"])
        self.assertEqual(caught.exception.snapshot.global_repair_round, 0)
        self.assertEqual(len(self.calls), 3)
        verify()

    async def test_supplied_requirements_must_match_current_inputs(self):
        """调用方提供的旧职责不能越过当前正式输入重新计算的门禁。"""

        inputs = planning_inputs()
        stale = planning_inputs(required=["page:a"]).requirements()
        with self.assertRaises(DagPlanningError) as caught:
            await self._plan(inputs, generation_requirements=stale)
        self.assertEqual(caught.exception.issues[0].code, "PLANNING_REQUIREMENTS_MISMATCH")
        self.assertEqual(self.calls, [])
        self.assertIsNone(load_planning_run(self.workspace))

    async def test_authorization_uses_deterministic_candidate_without_model_budget(self):
        """auth-guard 经过确定性 builder、Controller、Assembly，模型预算始终为零。"""

        plan = project_plan(authorization=True)
        scope = execution_scope()
        context = build_context(plan, scope)
        result = await self._plan(planning_inputs(plan=plan, scope=scope, context=context,
                                                 required=context["required_unit_ids"]))
        auth = result.planning_run.unit_states["frontend:auth-guard"]
        self.assertEqual((auth.generation_status, auth.attempt_in_round, auth.total_attempts), ("candidate_ready", 0, 0))
        self.assertNotIn("frontend:auth-guard", [job.identity.unit_id for job, _ in self.calls])
        candidate = result.planning_run.candidates[auth.latest_candidate_id]
        self.assertEqual(candidate.tasks[0]["platform_executor"], "authorization.frontend_resources")
        self.assertEqual(candidate.tasks[0]["target_files"], ("frontend/src/constants/resources.ts",))
