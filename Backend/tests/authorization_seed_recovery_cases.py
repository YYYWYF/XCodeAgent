"""复用真实 Seed 夹具，验证提交前退出、幂等重提及原操作身份保护。"""

import asyncio
import json
from unittest.mock import patch

import httpx

from app.protocols.builtin_authorization import build_builtin_authorization_stream
from app.services.authorization_workspace_projector import read_record
from app.services.builtin_authorization_patch import refresh_patch
from app.services.builtin_authorization_state import save_state


class AuthorizationSeedRecoveryCases:
    """由 Seed 与编排测试共享，所有 HTTP 均通过受控 SaaS 替身。"""

    def store_seed_attempt(self, status: str = "submitting") -> None:
        """模拟已持久化尝试但请求尚未受理，保留当前编译身份。"""

        compiled = self.compile()
        record = read_record(self.root, "state.json")
        save_state(self.root, {**record, "seed": {
            "seedFingerprint": compiled["seedFingerprint"], "idempotencyKey": compiled["idempotencyKey"],
            "status": status, "ready": False}})

    def test_pre_submit_exit_and_local_unknown_can_resume(self) -> None:
        """提交前退出或未受理的未知状态，重建客户端后均可原键提交。"""

        baseline = read_record(self.root, "state.json")
        for status in ("submitting", "unknown"):
            with self.subTest(status=status):
                save_state(self.root, baseline)
                self.calls.clear()
                self.accepted = False
                self.store_seed_attempt(status)
                identity = read_record(self.root, "state.json")["seed"]
                self.assertFalse(self.run_node()["seed_ready"])
                self.assertTrue(self.run_node()["seed_ready"])
                actual = read_record(self.root, "state.json")["seed"]
                for field in ("seedFingerprint", "idempotencyKey"):
                    self.assertEqual(actual[field], identity[field])
                self.assert_calls("GET", "POST", "GET")

    def test_stale_missing_lookup_reposts_same_payload_without_duplicate_effect(self) -> None:
        """回执丢失且查询暂不可见时允许同键重提，远端复用一次业务效果。"""

        original = self.respond
        submissions = []
        executions = 0
        stale = True

        def respond(request: httpx.Request) -> httpx.Response:
            """模拟持久幂等受理及一次陈旧查询，记录请求字节和实际执行次数。"""

            nonlocal executions, stale
            if request.method == "GET" and "/by-key/" in request.url.path and self.accepted and stale:
                stale = False
                self.calls.append((request.method, request.url.path))
                return self.envelope(404, None, "RESOURCE_NOT_FOUND")
            if request.method == "POST":
                submissions.append(request.content)
                executions += int(not self.accepted)
            return original(request)

        with patch.object(self, "respond", side_effect=respond):
            self.drop = True
            self.assertEqual(self.run_node()["failure_category"], "seed_timeout")
            self.drop = False
            self.assertFalse(self.run_node()["seed_ready"])
            self.assertTrue(self.run_node()["seed_ready"])
        self.assertEqual(len(submissions), 2)
        self.assertEqual(submissions[0], submissions[1])
        self.assertEqual(json.loads(submissions[0]), self.compile()["request"])
        self.assertEqual(executions, 1)
        self.assert_calls("GET", "POST", "GET", "POST", "GET")

    def test_missing_id_looks_up_key_before_reusing_operation(self) -> None:
        """原 ID 查无记录后按原键找到同一操作，仅核验而不提交。"""

        self.run_node()
        original = self.respond

        def respond(request: httpx.Request) -> httpx.Response:
            """只让 ID 查询返回明确未找到，原键查询仍返回原操作。"""

            if request.url.path.endswith("/456"):
                self.calls.append((request.method, request.url.path))
                return self.envelope(404, None, "RESOURCE_NOT_FOUND")
            return original(request)

        with patch.object(self, "respond", side_effect=respond):
            self.assertTrue(self.run_node()["seed_ready"])
        self.assert_calls("GET", "POST", "GET", "GET")
        self.assertIn("/by-key/", self.calls[-1][1])

    def test_both_lookups_missing_allow_same_key_submission(self) -> None:
        """原 ID 与原键均未找到才可重提，已有 ID 在提交标记中持续保留。"""

        self.run_node()
        self.accepted = False
        original = self.respond

        def respond(request: httpx.Request) -> httpx.Response:
            """检查重提前未丢弃原受理身份，远端仍返回原操作回执。"""

            if request.method == "POST":
                self.assertEqual(read_record(self.root, "state.json")["seed"]["operationId"], "456")
            return original(request)

        with patch.object(self, "respond", side_effect=respond):
            self.assertFalse(self.run_node()["seed_ready"])
            self.assertTrue(self.run_node()["seed_ready"])
        self.assert_calls("GET", "POST", "GET", "GET", "POST", "GET")
        self.assertTrue(self.calls[2][1].endswith("/456"))
        self.assertIn("/by-key/", self.calls[3][1])

    def test_key_lookup_cannot_replace_existing_operation_identity(self) -> None:
        """ID 未找到后原键返回另一操作时停止，不覆盖原受理证据。"""

        self.run_node()
        baseline = read_record(self.root, "state.json")
        original = self.respond
        self.detail_overrides = {"operationId": "789"}

        def respond(request: httpx.Request) -> httpx.Response:
            """模拟 ID 缺失但按键返回矛盾的操作身份。"""

            if request.url.path.endswith("/456"):
                self.calls.append((request.method, request.url.path))
                return self.envelope(404, {}, "RESOURCE_NOT_FOUND")
            return original(request)

        with patch.object(self, "respond", side_effect=respond):
            self.assertEqual(self.run_node()["failure_category"], "seed_operation_identity_mismatch")
        self.assertEqual(read_record(self.root, "state.json"), baseline)
        self.assert_calls("GET", "POST", "GET", "GET")

    def test_repeated_submission_receipt_cannot_replace_known_id(self) -> None:
        """同键重提的回执不能静默替换已知 ID，矛盾时保留原证据。"""

        self.run_node()
        self.accepted = False
        original = self.respond

        def respond(request: httpx.Request) -> httpx.Response:
            """重提时返回不同操作编号，模拟远端幂等合同异常。"""

            response = original(request)
            if request.method == "POST":
                envelope = response.json()
                envelope["body"]["operationId"] = "789"
                return httpx.Response(200, json=envelope)
            return response

        with patch.object(self, "respond", side_effect=respond):
            result = self.run_node()
        self.assertEqual(result["failure_category"], "seed_operation_identity_mismatch")
        seed = read_record(self.root, "state.json")["seed"]
        self.assertEqual(seed["operationId"], "456")
        self.assertEqual(seed["idempotencyKey"], self.compile()["idempotencyKey"])
        self.assertFalse(seed["ready"])

    def test_lookup_errors_never_authorize_submission(self) -> None:
        """网络、服务异常和非业务 404 均保持原状态，不因本地尝试而提交。"""

        self.store_seed_attempt()
        baseline = read_record(self.root, "state.json")
        for status, code, body in ((503, "INTERNAL_ERROR", None), (404, "OTHER_NOT_FOUND", None),
                                   (404, "RESOURCE_NOT_FOUND", {"unexpected": True})):
            with self.subTest(status=status, code=code):
                self.calls.clear()

                def respond(request: httpx.Request) -> httpx.Response:
                    """返回不可作为记录不存在依据的查询错误。"""

                    self.calls.append((request.method, request.url.path))
                    return self.envelope(status, body, code)

                with patch.object(self, "respond", side_effect=respond):
                    self.assertFalse(self.run_node()["seed_ready"])
                self.assertEqual(read_record(self.root, "state.json"), baseline)
                self.assert_calls("GET")

    def test_recovery_without_build_plan_reuses_seed_identity(self) -> None:
        """无 Build DAG 时，查无原操作仍按原键提交并核验。"""

        self.store_seed_attempt()
        self.assertFalse((self.root / ".devagentstudio/plans/build-task-plan.json").exists())
        original_seed = read_record(self.root, "state.json")["seed"]
        self.assertFalse(self.run_node()["seed_ready"])
        self.assertTrue(self.run_node()["seed_ready"])
        current_seed = read_record(self.root, "state.json")["seed"]
        for field in ("idempotencyKey", "seedFingerprint"):
            self.assertEqual(current_seed[field], original_seed[field])
        self.assert_calls("GET", "POST", "GET")

    def test_inputs_changed_after_missing_lookup_block_submission(self) -> None:
        """按键未找到后管理员变化，网络边界复核必须阻断 POST。"""

        self.store_seed_attempt()
        baseline = read_record(self.root, "state.json")
        original = self.respond

        def respond(request: httpx.Request) -> httpx.Response:
            """在未找到响应返回前改变权威管理员配置。"""

            response = original(request)
            self.application["authorization"]["initialAdministratorSubjects"] = ["changed"]
            return response

        with patch.object(self, "respond", side_effect=respond):
            self.assertEqual(self.run_node()["failure_category"], "seed_inputs_changed_during_operation")
        self.assertEqual(read_record(self.root, "state.json"), baseline)
        self.assert_calls("GET")

    def test_ag_ui_refresh_missing_remains_read_only(self) -> None:
        """纯刷新收到原 ID 404 时完整返回 AG-UI 生命周期，不补查或提交。"""

        self.run_node()
        self.calls.clear()
        self.accepted = False
        baseline = read_record(self.root, "state.json")

        async def collect() -> str:
            """通过原独立权限端点收集未找到操作的完整生命周期。"""

            payload = {"threadId": "missing-thread", "runId": "missing-run", "forwardedProps": {
                "builtinAuthorization": {"action": "refresh", "operationKind": "seed",
                    "workspaceRoot": str(self.root), "operationId": "456"}}}
            return "".join([frame async for frame in build_builtin_authorization_stream(payload)])

        with patch("app.services.builtin_authorization_seed_runtime.ManagedDatabaseClient", return_value=self.client()):
            frames = asyncio.run(collect()).replace(" ", "")
        for expected in ("RUN_STARTED", "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED",
                         '"failureCategory":"seed_operation_not_found"', '"seedReady":false'):
            self.assertIn(expected, frames)
        self.assertEqual(read_record(self.root, "state.json"), baseline)
        self.assert_calls("GET")

    def test_existing_rows_are_verified_before_ready(self) -> None:
        """预期行全部已存在时仍需 GET 核验，HTTP 200 回执不能直接宣告完成。"""

        total = sum(len(table["rows"]) for table in self.compile()["request"]["tables"])
        self.detail_overrides = {"createdCount": 0, "existingCount": total}
        self.assertFalse(self.run_node()["seed_ready"])
        self.assertTrue(self.run_node()["seed_ready"])
        seed = read_record(self.root, "state.json")["seed"]
        self.assertEqual((seed["createdCount"], seed["existingCount"]), (0, total))
        self.assert_calls("GET", "POST", "GET")

    def test_patch_refresh_without_id_never_submits(self) -> None:
        """调试面板只有原键时，刷新未找到也不提交，且不要求写入准入。"""

        self.store_seed_attempt()
        baseline = read_record(self.root, "state.json")
        with patch("app.services.builtin_authorization_patch.read_application_config", return_value=self.application), \
                patch("app.services.builtin_authorization_seed_runtime.ManagedDatabaseClient", return_value=self.client()):
            result = refresh_patch(str(self.root))
        self.assertEqual(result["failure_category"], "seed_operation_not_found")
        self.assertEqual(read_record(self.root, "state.json"), baseline)
        self.assert_calls("GET")
