"""真实原子服务和假 SaaS 驱动编排，不使用真实轮询等待。"""

import asyncio
import json
import threading
import unittest
import multiprocessing
from concurrent.futures import Future
from unittest.mock import patch
from uuid import uuid4

import httpx

from app.services import builtin_authorization_orchestrator as orchestration
from app.services.authorization_workspace_projector import RUNTIME, read_record
from app.services.builtin_authorization_bootstrap import run_builtin_authorization_bootstrap
from app.services.builtin_authorization_state import save_state
from app.services.builtin_authorization_task_store import AuthorizationTask, read_task, save_task
from app.protocols.builtin_authorization import build_builtin_authorization_stream
from app.services.managed_database_client import ManagedDatabaseClient
from tests import test_builtin_authorization_seed as seed_fixture
from tests.authorization_orchestrator_safety_cases import AuthorizationOrchestratorSafetyCases


class BuiltinAuthorizationOrchestratorTests(AuthorizationOrchestratorSafetyCases, seed_fixture.BuiltinAuthorizationSeedTests):
    """复用既有协议回归基线，额外覆盖调度、恢复及身份失效。"""

    def setUp(self) -> None:
        """安装真实编译输入和两阶段传输替身。"""

        super().setUp()
        self.start_patch("app.services.builtin_authorization_orchestrator.read_application_config", return_value=self.application)
        self.schema_status = "RUNNING"
        self.schema_synced = False
        self.schema_drop = False
        self.schema_posts = 0
        self.schema_gets = 0
        self.generation = uuid4().hex

    def begin(self, *, fresh=False) -> None:
        """建立独立编排记录，模拟首次调度或 Backend 接管。"""

        if fresh:
            (self.root / RUNTIME / "state.json").unlink()
        save_task(self.root, AuthorizationTask(generation=self.generation,
                  inputFingerprint=orchestration._inputs(self.root)[0], status="pending", updatedAt="fixture"))

    def schema_respond(self, request: httpx.Request) -> httpx.Response:
        """严格模拟四表检查、历史、CREATE 受理和原 ID 查询。"""

        if request.url.path.endswith("/tables/check"):
            return self.envelope(200, {"datasourceId": 2, "ready": self.schema_synced,
                "tables": [{"tableName": table["tableName"], "status": "SYNCED" if self.schema_synced else "NOT_CREATED",
                            "differences": []} for table in self.compile()["request"]["tables"]]})
        if request.url.path.endswith("/batch"):
            self.schema_posts += 1
            if self.schema_drop:
                raise httpx.ReadTimeout("fixture", request=request)
            return self.envelope(200, {"operationId": "123", "status": "RUNNING"})
        if request.url.query:
            return self.envelope(200, [])
        self.assertTrue(request.url.path.endswith("/123"))
        self.schema_gets += 1
        return self.envelope(200, {"operationId": "123", "mode": "CREATE_BATCH", "status": self.schema_status,
            "tableNames": [t["tableName"] for t in self.compile()["request"]["tables"]], "executionResult": {"steps": []}})

    def execute(self, workspace: str, plan: dict, *, schema_only: bool) -> dict:
        """保留真实 Schema 和 Seed 的幂等及成功核验代码。"""

        client = ManagedDatabaseClient(self.settings, transport=httpx.MockTransport(self.schema_respond))
        return run_builtin_authorization_bootstrap(workspace, plan, client=client, seed_client=self.client(), schema_only=schema_only)

    def tick(self) -> bool:
        """推进一个安全转换，所有等待均由测试显式驱动。"""

        return orchestration.tick(self.root, self.generation, execute=self.execute)

    def test_full_automatic_journey_without_build_plan(self) -> None:
        """无 Build DAG 时 CREATE 和 Seed 各一次，多次 RUNNING 仅 GET，最终均就绪。"""

        self.assertFalse((self.root / ".devagentstudio/plans/build-task-plan.json").exists())
        self.begin(fresh=True)
        self.assertTrue(self.tick())
        for _ in range(3):
            self.assertTrue(self.tick())
        self.assertEqual((self.schema_posts, self.schema_gets), (1, 3))
        self.assertEqual(self.calls, [])
        self.schema_status, self.schema_synced = "SUCCEEDED", True
        self.assertTrue(self.tick())
        self.assertEqual(read_task(self.root).stage, "seed")
        self.assertTrue(self.tick())
        self.detail_overrides = {"status": "RUNNING", "verified": False, "createdCount": 0}
        for _ in range(3):
            self.assertTrue(self.tick())
        self.detail_overrides = {}
        self.assertFalse(self.tick())
        self.assertEqual(self.schema_posts, 1)
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        self.assertEqual(sum(method == "GET" for method, _ in self.calls), 5)
        self.assertEqual(orchestration.get_builtin_authorization_task(str(self.root))["status"], "ready")

    def test_schema_success_without_structure_never_seeds(self) -> None:
        """操作成功不能替代四表核验。"""

        self.begin(fresh=True)
        self.tick()
        self.schema_status = "SUCCEEDED"
        for _ in range(3):
            self.assertTrue(self.tick())
        self.assertEqual(self.schema_posts, 1)
        self.assertEqual(self.calls, [])

    def test_schema_failed_unknown_and_lost_receipt(self) -> None:
        """明确失败、UNKNOWN 和无编号保护均禁止第二次 CREATE。"""

        for status in ("FAILED", "UNKNOWN", "lost"):
            with self.subTest(status=status):
                self.begin(fresh=True)
                self.schema_drop = status == "lost"
                self.tick()
                if status != "lost":
                    self.schema_status = status
                    self.assertFalse(self.tick())
                self.assertEqual(read_task(self.root).status, "needs_attention")
                self.assertFalse(self.tick())
        self.assertEqual(self.schema_posts, 3)
        self.assertEqual(self.calls, [])

    def test_seed_response_lost_resumes_by_key_without_post(self) -> None:
        """受理回执丢失后重新读取落盘身份，只有 by-key GET。"""

        self.begin()
        self.drop = True
        self.assertTrue(self.tick())
        self.assertNotIn("operationId", read_record(self.root, "state.json")["seed"])
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST", "GET")
        self.assertEqual(self.schema_posts, 0)

    def test_existing_seed_key_404_resumes_same_request(self) -> None:
        """本地未确认记录不阻断原键提交，最终仍须 GET 核验。"""

        self.begin()
        record = read_record(self.root, "state.json")
        compiled = self.compile()
        save_state(self.root, {**record, "seed": {"seedFingerprint": compiled["seedFingerprint"],
                   "idempotencyKey": compiled["idempotencyKey"], "status": "unknown", "ready": False}})
        self.assertTrue(self.tick())
        self.assertEqual(read_task(self.root).status, "waiting")
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST", "GET")
        self.assertEqual(read_task(self.root).status, "ready")


    def test_stale_generation_and_read_only_get(self) -> None:
        """旧 worker 和本地查询均不能提交或更新任务。"""

        self.begin()
        before = (self.root / RUNTIME / "orchestration.json").read_bytes()
        self.assertFalse(orchestration.tick(self.root, uuid4().hex, execute=self.execute))
        for _ in range(3):
            orchestration.get_builtin_authorization_task(str(self.root))
        self.assertEqual((self.root / RUNTIME / "orchestration.json").read_bytes(), before)
        self.assertEqual(self.calls, [])

    def test_worker_timeout_without_sleep(self) -> None:
        """注入时钟直接进入上限，保留原操作和可恢复标记。"""

        self.begin()
        times = iter([0, 601])
        orchestration._worker(self.root, self.generation, threading.Event(), clock=lambda: next(times))
        self.assertEqual(read_task(self.root).failureCategory, "timeout")
        self.assertEqual(self.calls, [])

    def test_first_seed_lookup_transient_error_only_retries_get(self) -> None:
        """首次 by-key 临时失败可自动查回，未确认不存在前不提交。"""

        self.begin()
        original = self.respond

        def respond(request):
            """首个 GET 超时，后续传输仍使用真实协议替身。"""

            if not self.calls:
                self.calls.append((request.method, request.url.path))
                raise httpx.ReadTimeout("fixture", request=request)
            return original(request)

        with patch.object(self, "respond", side_effect=respond):
            self.assertTrue(self.tick())
            self.assert_calls("GET")
            self.assertTrue(self.tick())
            self.assertFalse(self.tick())
        self.assert_calls("GET", "GET", "POST", "GET")

    @unittest.skipUnless("fork" in multiprocessing.get_all_start_methods(), "跨进程 Transport 替身需要 fork")
    def test_two_processes_share_one_create(self) -> None:
        """两进程分别竞争 Schema 和 Seed，每阶段一次 POST 后只查原 ID。"""

        schema_record = read_record(self.root, "state.json")
        for stage in ("schema", "seed"):
            with self.subTest(stage=stage):
                save_state(self.root, schema_record)
                self.begin(fresh=stage == "schema")
                log = self.root / f"{stage}.jsonl"
                accepted = self.root / "seed-accepted"
                method = "schema_respond" if stage == "schema" else "respond"
                original = getattr(self, method)

                def respond(request):
                    """用共享文件模拟 SaaS 的受理事实并收集跨进程传输次数。"""

                    with log.open("a") as handle:
                        handle.write(json.dumps([request.method, request.url.path]) + "\n")
                    self.accepted = accepted.exists()
                    response = original(request)
                    if stage == "seed" and request.method == "POST":
                        accepted.write_text("accepted")
                    return response

                context = multiprocessing.get_context("fork")
                with patch.object(self, method, side_effect=respond):
                    processes = [context.Process(target=self.tick) for _ in range(2)]
                    for process in processes:
                        process.start()
                    for process in processes:
                        process.join(10)
                        self.assertEqual(process.exitcode, 0)
                calls = [json.loads(line) for line in log.read_text().splitlines()]
                self.assertEqual(sum(path.endswith("/batch") for _, path in calls), 1)
                operation_id = "123" if stage == "schema" else "456"
                self.assertEqual(sum(path.endswith("/" + operation_id) for _, path in calls), 1)
                if stage == "seed":
                    self.assertEqual(read_task(self.root).status, "ready")

    def test_explicit_retry_recovers_lookup_failure_via_ag_ui(self) -> None:
        """显式重试恢复首次查询错误，attach 不重启，最终仅提交一次数据。"""

        self.begin()
        task = read_task(self.root)
        task.status, task.stage, task.failureCategory = "needs_attention", "seed", "seed_invalid_response"
        save_task(self.root, task)
        before = read_record(self.root, "state.json")
        self.assertEqual(orchestration.get_builtin_authorization_task(str(self.root))["nextAction"], "retry")

        async def collect() -> str:
            """通过实际 AG-UI retry 动作收集完整生命周期。"""

            stream = build_builtin_authorization_stream({"threadId": "retry-thread", "runId": "retry-run",
                "forwardedProps": {"builtinAuthorization": {"action": "retry", "workspaceRoot": str(self.root)}}})
            return "".join([frame async for frame in stream])

        with patch.object(orchestration, "_executor") as executor:
            executor.submit.return_value = Future()
            self.addCleanup(orchestration._workers.pop, str(self.root), None)
            orchestration.resume_builtin_authorization_task(str(self.root))
            executor.submit.assert_not_called()
            frames = asyncio.run(collect())
            self.assertIn("RUN_STARTED", frames)
            self.assertIn("STATE_SNAPSHOT", frames)
            self.assertIn("RUN_FINISHED", frames)
            self.assertNotIn('"status":"failed"', frames.replace(" ", ""))
            orchestration.resume_builtin_authorization_task(str(self.root), retry=True)
            executor.submit.assert_called_once()
        self.assertEqual(read_record(self.root, "state.json"), before)
        self.assertTrue(self.tick())
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST", "GET")

    def test_explicit_retry_recovers_previous_http_404_failure(self) -> None:
        """修复查无记录响应后，原 404 失败可显式恢复且只提交一次 Seed。"""

        self.begin()
        task = read_task(self.root)
        task.status, task.stage, task.failureCategory = "needs_attention", "seed", "seed_http_404"
        save_task(self.root, task)
        before = read_record(self.root, "state.json")
        self.assertEqual(orchestration.get_builtin_authorization_task(str(self.root))["nextAction"], "retry")
        with patch.object(orchestration, "_executor") as executor:
            executor.submit.return_value = Future()
            self.addCleanup(orchestration._workers.pop, str(self.root), None)
            orchestration.resume_builtin_authorization_task(str(self.root))
            executor.submit.assert_not_called()
            orchestration.resume_builtin_authorization_task(str(self.root), retry=True)
            executor.submit.assert_called_once()
        self.assertEqual(read_record(self.root, "state.json"), before)
        self.assertTrue(self.tick())
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST", "GET")

    def test_explicit_retry_after_receipt_loss_never_reposts(self) -> None:
        """回执丢失后即使因 404 显式重试，也沿原幂等键查询，不重复 POST。"""

        self.begin()
        self.drop = True
        self.assertTrue(self.tick())
        task = read_task(self.root)
        task.status, task.failureCategory = "needs_attention", "seed_http_404"
        save_task(self.root, task)
        with patch.object(orchestration, "_executor") as executor:
            executor.submit.return_value = Future()
            self.addCleanup(orchestration._workers.pop, str(self.root), None)
            orchestration.resume_builtin_authorization_task(str(self.root), retry=True)
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST", "GET")
        self.assertEqual(read_task(self.root).status, "ready")

    def test_explicit_retry_rejects_unsafe_failures_and_changed_inputs(self) -> None:
        """输入变化、损坏和远端失败未知均不能通过重试绕过原提交保护。"""

        self.begin()
        for failure in ("inputs_changed", "state_or_inputs_invalid", "seed_operation_failed", "seed_operation_unknown"):
            task = read_task(self.root)
            task.status, task.failureCategory = "needs_attention", failure
            save_task(self.root, task)
            with self.assertRaises(ValueError):
                orchestration.resume_builtin_authorization_task(str(self.root), retry=True)
            self.assertEqual(read_task(self.root).status, "needs_attention")
        task.failureCategory = "seed_invalid_response"
        save_task(self.root, task)
        self.application["authorization"]["initialAdministratorSubjects"] = ["changed"]
        with self.assertRaises(ValueError):
            orchestration.resume_builtin_authorization_task(str(self.root), retry=True)
        self.assert_calls()

    def test_restart_resumes_original_id_and_worker_deduplication(self) -> None:
        """attach 与重复 Build 合用一个 worker，重启接管只查原 Seed ID。"""

        self.begin()
        self.tick()
        pending = Future()
        with patch.object(orchestration, "_executor") as executor:
            executor.submit.return_value = pending
            self.addCleanup(orchestration._workers.pop, str(self.root), None)
            first = orchestration.resume_builtin_authorization_task(str(self.root))
            second = orchestration.ensure_builtin_authorization_task(str(self.root), source="build")
            self.assertEqual(first["status"], second["status"])
            executor.submit.assert_called_once()
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST", "GET")
        self.assertTrue(self.calls[-1][1].endswith("/456"))


    def test_stop_and_nonbuiltin_attach_do_not_submit(self) -> None:
        """删除等待本地 worker 停止，空白及非 Builtin attach 不启动任务。"""

        self.begin()
        before = read_record(self.root, "state.json")
        orchestration.stop_builtin_authorization_task(str(self.root))
        self.addCleanup(orchestration._blocked.discard, str(self.root))
        self.assertEqual(read_task(self.root).status, "cancelled")
        self.assertEqual(read_record(self.root, "state.json"), before)
        self.assertFalse(self.tick())
        self.assertEqual(self.calls, [])

