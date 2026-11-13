"""共享真实编排夹具的安全边界用例，按输入与持久保护集中维护。"""

from copy import deepcopy
from concurrent.futures import Future
from unittest.mock import patch

import httpx

from app.services import builtin_authorization_orchestrator as orchestration
from app.services.authorization_workspace_projector import RUNTIME, read_record
from app.services.builtin_authorization_seed import SeedContractError
from app.services.builtin_authorization_state import save_state
from app.services.builtin_authorization_task_store import read_task
from app.services.authorization_manifest import _with_fingerprint
from tests.authorization_seed_fixture import save_plan


class AuthorizationOrchestratorSafetyCases:
    """由生产传输夹具类混入执行，不单独生成第二套业务替身。"""

    def _assert_employee_name_change_never_reposts_seed(self, status: str) -> None:
        """以真实编排推进状态，再验证改姓名不触发第二次提交或改写操作证据。"""

        self.application["authorization"]["initialAdministratorSubjects"] = ["00123456/张三"]
        self.begin()
        self.tick()
        if status != "RUNNING":
            self.detail_overrides = {"status": status, "verified": status == "SUCCEEDED"}
            if status == "UNKNOWN":
                self.detail_overrides["createdCount"] = 0
            self.tick()
        before = deepcopy(read_record(self.root, "state.json"))
        self.assertEqual(before["seed"]["status"], {"RUNNING": "running", "UNKNOWN": "unknown", "SUCCEEDED": "seed_ready"}.get(status, status))
        self.application["authorization"]["initialAdministratorSubjects"] = ["00123456/李四"]
        orchestration.ensure_builtin_authorization_task(str(self.root), source="test")
        self.tick()
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        self.assertEqual(read_record(self.root, "state.json"), before)

    def test_running_seed_name_change_never_reposts(self) -> None:
        """执行中改姓名不能产生新的 Seed。"""
        self._assert_employee_name_change_never_reposts_seed("RUNNING")

    def test_unknown_seed_name_change_never_reposts(self) -> None:
        """结果未知时改姓名不能重放或丢失原操作。"""
        self._assert_employee_name_change_never_reposts_seed("UNKNOWN")

    def test_ready_seed_name_change_never_reposts(self) -> None:
        """已就绪时改姓名不能重新播种或修改已初始化成员。"""
        self._assert_employee_name_change_never_reposts_seed("SUCCEEDED")

    def test_seed_child_loss_never_becomes_first_submission(self) -> None:
        """Seed 子对象被清空也不能通过 by-key 404 重发 POST。"""

        self.begin()
        self.assertTrue(self.tick())
        self.assertTrue(read_task(self.root).seedStateExpected)
        record = read_record(self.root, "state.json")
        record.pop("seed")
        save_state(self.root, record)
        self.accepted = False
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST")
        self.assertEqual(read_task(self.root).status, "needs_attention")

    def test_ready_snapshot_never_overrides_operation_facts(self) -> None:
        """编排成功快照不能覆盖已丢失的权威 Seed 核验事实。"""

        self.begin()
        self.tick()
        self.tick()
        record = read_record(self.root, "state.json")
        record["seed"]["ready"] = False
        save_state(self.root, record)
        view = orchestration.get_builtin_authorization_task(str(self.root))
        self.assertFalse(view["seedReady"])
        self.assertEqual(view["status"], "needs_attention")

    def test_inputs_changed_before_and_during_get(self) -> None:
        """管理员变化阻断下一次副作用，迟到回执不能写入成功。"""

        self.begin()
        self.tick()
        before = deepcopy(read_record(self.root, "state.json"))
        self.before_response = lambda: self.application["authorization"].update(initialAdministratorSubjects=["changed"])
        self.assertFalse(self.tick())
        self.assertEqual(read_record(self.root, "state.json"), before)
        self.assertEqual(read_task(self.root).failureCategory, "inputs_changed")
        self.assertFalse(orchestration.get_builtin_authorization_task(str(self.root))["seedReady"])

    def test_corrupt_missing_and_symlink_states(self) -> None:
        """状态损坏、丢失及软链接不能退化为首次提交。"""

        self.begin()
        self.tick()
        path = self.root / RUNTIME / "state.json"
        path.unlink()
        self.assertFalse(self.tick())
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        task_path = self.root / RUNTIME / "orchestration.json"
        task_path.write_text("{}")
        self.assertEqual(orchestration.get_builtin_authorization_task(str(self.root))["status"], "needs_attention")
        task_path.unlink()
        target = self.root / "other.json"
        target.write_text("{}")
        task_path.symlink_to(target)
        self.assertEqual(orchestration.ensure_builtin_authorization_task(str(self.root), source="test")["status"], "needs_attention")

    def test_input_drift_variants_block_before_network(self) -> None:
        """开关、绑定、DDL、管理员及 Manifest 漂移不能使用旧任务提交。"""

        self.begin()
        with patch.object(orchestration, "_inputs", side_effect=ValueError("changed")):
            self.assertFalse(self.tick())
        self.assertEqual(self.calls, [])
        application, plan, ddl = deepcopy(self.application), deepcopy(self.plan), self.ddl.read_text()

        def change_manifest():
            """以有效的新正式 Manifest 测试输入漂移，而非仅破坏指纹。"""

            self.plan["authorization_manifest"]["resources"][0]["name"] = "新名称"
            self.plan["authorization_manifest"] = _with_fingerprint(self.plan["authorization_manifest"])
            save_plan(self.root, self.plan)

        for change in (lambda: self.application["authorization"].update(enabled=False),
                       lambda: self.application["managedDatabase"].update(datasourceId=3),
                       lambda: self.ddl.write_text(self.ddl.read_text().replace("BIGINT", "INT")),
                       lambda: self.application["authorization"].update(initialAdministratorSubjects=["other"]),
                       change_manifest):
            with self.subTest(change=change):
                self.begin()
                change()
                self.assertFalse(self.tick())
                self.assertEqual(self.calls, [])
                # 恢复测试输入不恢复执行身份，下一轮重新建立测试任务。
                self.application.clear()
                self.application.update(deepcopy(application))
                self.plan.clear()
                self.plan.update(deepcopy(plan))
                save_plan(self.root, self.plan)
                self.ddl.write_text(ddl)

    def test_post_inflight_input_change_preserves_submitting_guard(self) -> None:
        """POST 已发出后换库，迟到成功不能覆盖 submitting 原证据。"""

        self.begin(fresh=True)
        original = self.schema_respond

        def respond(request):
            """在受理边界后模拟权威配置切换。"""

            response = original(request)
            if request.url.path.endswith("/batch"):
                self.application["managedDatabase"]["datasourceId"] = 3
            return response

        self.schema_respond = respond
        self.assertFalse(self.tick())
        self.assertEqual(read_record(self.root, "state.json")["status"], "submitting")
        self.assertEqual(self.schema_posts, 1)
        self.assertFalse(self.tick())
        self.assertEqual(self.schema_posts, 1)

    def test_contract_mismatch_reports_attention_without_posts(self) -> None:
        """传输合同错误进入待处理且不会查询或提交远端操作。"""

        self.begin()
        with patch("app.services.builtin_authorization_seed_runtime.validate_seed_transport",
                   side_effect=SeedContractError("权限 Seed 字段集合无效。")):
            self.assertFalse(self.tick())
        self.assertEqual(read_task(self.root).failureCategory, "seed_saas_contract_incompatible")
        self.assertEqual(self.calls, [])

    def test_invalid_seed_success_never_becomes_ready(self) -> None:
        """指纹、verified、表名和数量任一错误均不能产生 ready。"""

        self.begin()
        self.tick()
        before = read_record(self.root, "state.json")
        for change in ({"verified": False}, {"requestFingerprint": "sha256:" + "0" * 64},
                       {"tableNames": ["wrong"] * 4}, {"createdCount": 0}):
            with self.subTest(change=change):
                save_state(self.root, before)
                self.begin()
                self.detail_overrides = change
                self.assertFalse(self.tick())
                self.assertNotEqual(read_task(self.root).status, "ready")
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_malformed_authority_never_replays(self) -> None:
        """对象格式正确但原状态字段损坏仍必须 fail closed。"""

        self.begin()
        record = read_record(self.root, "state.json")
        record["status"] = "unrecognized"
        save_state(self.root, record)
        self.assertFalse(self.tick())
        self.assertEqual(self.calls, [])

    def test_post_rejections_stop_automatic_attempts(self) -> None:
        """认证、参数、路由及幂等冲突拒绝不能误判为丢失回执而无限自动重提。"""

        baseline = read_record(self.root, "state.json")
        for status in (400, 401, 403, 404, 409, 422):
            with self.subTest(status=status):
                save_state(self.root, baseline)
                self.calls.clear()
                self.post_error = status
                self.post_error_code = "IDEMPOTENCY_CONFLICT" if status == 409 else "INVALID_ARGUMENT"
                self.begin()
                self.assertFalse(self.tick())
                category = "seed_idempotency_conflict" if status == 409 else f"seed_http_{status}"
                self.assertEqual(read_task(self.root).failureCategory, category)
                self.assertEqual(read_record(self.root, "state.json")["seed"]["status"], "failed")
                self.assertFalse(self.tick())
                self.assert_calls("GET", "POST")
                if status == 409:
                    with self.assertRaises(ValueError):
                        orchestration.resume_builtin_authorization_task(str(self.root), retry=True)

    def test_explicit_retry_after_auth_rejection_can_submit(self) -> None:
        """认证拒绝后修正服务，显式重试先查原键再恢复提交，不删除本地身份。"""

        self.begin()
        self.post_error = 401
        self.assertFalse(self.tick())
        identity = deepcopy(read_record(self.root, "state.json")["seed"])
        self.post_error = None
        with patch.object(orchestration, "_executor") as executor:
            executor.submit.return_value = Future()
            self.addCleanup(orchestration._workers.pop, str(self.root), None)
            orchestration.resume_builtin_authorization_task(str(self.root))
            executor.submit.assert_not_called()
            orchestration.resume_builtin_authorization_task(str(self.root), retry=True)
            executor.submit.assert_called_once()
        self.assertTrue(self.tick())
        self.assertFalse(self.tick())
        actual = read_record(self.root, "state.json")["seed"]
        self.assertEqual(actual["idempotencyKey"], identity["idempotencyKey"])
        self.assertEqual(read_task(self.root).status, "ready")
        self.assert_calls("GET", "POST", "GET", "POST", "GET")

    def test_unaccepted_post_network_error_recovers_automatically(self) -> None:
        """首次 POST 连接失败未受理，后续 tick 查无记录后按原键恢复。"""

        self.begin()
        original = self.respond
        unavailable = True

        def respond(request: httpx.Request) -> httpx.Response:
            """仅让第一次 POST 在受理前失败，不模拟远端已有操作。"""

            nonlocal unavailable
            if request.method == "POST" and unavailable:
                unavailable = False
                self.calls.append((request.method, request.url.path))
                raise httpx.ConnectError("fixture", request=request)
            return original(request)

        with patch.object(self, "respond", side_effect=respond):
            self.assertTrue(self.tick())
            self.assertFalse(self.accepted)
            self.assertEqual(read_task(self.root).status, "waiting")
            self.assertTrue(self.tick())
            self.assertFalse(self.tick())
        self.assertEqual(read_task(self.root).status, "ready")
        self.assert_calls("GET", "POST", "GET", "POST", "GET")

    def test_terminal_receipts_require_get_then_stop(self) -> None:
        """HTTP 200 的失败或未知回执仍须 GET 核实，远端终态确认后停止而不重新执行。"""

        baseline = read_record(self.root, "state.json")
        for status in ("FAILED", "UNKNOWN"):
            with self.subTest(status=status):
                save_state(self.root, baseline)
                self.calls.clear()
                self.accepted = False
                self.receipt_status = status
                self.detail_overrides = {"status": status, "verified": False, "createdCount": 0}
                self.begin()
                self.assertTrue(self.tick())
                self.assertFalse(self.tick())
                self.assertEqual(read_task(self.root).failureCategory, f"seed_operation_{status.lower()}")
                self.assertFalse(self.tick())
                self.assert_calls("GET", "POST", "GET")

    def test_resource_busy_recovers_after_rechecking_key(self) -> None:
        """SaaS 409 资源占用释放后可恢复；重试必须先查原键。"""

        self.begin()
        self.post_error = 409
        self.assertTrue(self.tick())
        self.assertEqual(read_task(self.root).status, "waiting")
        self.assertEqual(read_record(self.root, "state.json")["seed"]["failureCategory"], "seed_resource_busy")
        self.post_error = None
        self.assertTrue(self.tick())
        self.assertFalse(self.tick())
        self.assertEqual(read_task(self.root).status, "ready")
        self.assert_calls("GET", "POST", "GET", "POST", "GET")

    def test_invalid_accepted_receipt_recovers_by_key(self) -> None:
        """HTTP 200 回执无效但远端已受理时，自动按键查回原操作，不再次 POST。"""

        self.begin()
        original = self.respond

        def respond(request: httpx.Request) -> httpx.Response:
            """保留远端受理副作用，只损坏返回的操作编号。"""

            response = original(request)
            return self.envelope(200, {}) if request.method == "POST" else response

        with patch.object(self, "respond", side_effect=respond):
            self.assertTrue(self.tick())
            self.assertEqual(read_record(self.root, "state.json")["seed"]["status"], "unknown")
            self.assertFalse(self.tick())
        self.assertEqual(read_task(self.root).status, "ready")
        self.assert_calls("GET", "POST", "GET")
