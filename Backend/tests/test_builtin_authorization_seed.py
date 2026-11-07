"""以真实请求解析、持久化和路由验证 Builtin Seed 的不可重放边界。"""

import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import httpx

from app.config import Settings
from app.graph.workflow import route_workflow_start
from app.protocols.builtin_authorization import build_builtin_authorization_stream
from app.services.authorization_manifest import _with_fingerprint
from app.services.authorization_table_projection import compile_authorization_tables
from app.services.authorization_workspace_projector import read_record
from app.services.builtin_authorization_bootstrap import run_builtin_authorization_bootstrap
from app.services.builtin_authorization_seed import PURPOSE, compile_builtin_authorization_seed, seed_request_fingerprint
from app.services.builtin_authorization_seed_client import ManagedAuthorizationSeedClient
from app.services.builtin_authorization_seed_runtime import refresh_builtin_authorization_seed
from app.services.builtin_authorization_state import save_state, seed_ready
from app.services.project_launcher import _require_preview_authorization_ready
from tests.authorization_seed_fixture import KEY, SUBJECT, SYSTEM, save_plan, seed_plan, write_schema


class BuiltinAuthorizationSeedTests(unittest.TestCase):
    """用严格 SaaS 替身验证独立受理、单次查询和当前输入隔离。"""

    def setUp(self) -> None:
        """写入正式规划和已核验 Schema，替换配置和外部 HTTP。"""

        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.ddl = write_schema(self.root)
        self.plan = seed_plan()
        save_plan(self.root, self.plan)
        self.application = {
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "authorization": {"enabled": True, "initialAdministratorSubjects": [SUBJECT, "admin-z", SUBJECT]},
            "managedDatabase": {"status": "REGISTERED", "datasourceKey": KEY, "datasourceId": 2},
        }
        (self.root / ".devagentstudio/application.json").write_text("{}")
        ddl = compile_authorization_tables(self.root, KEY)
        save_state(self.root, {"schemaVersion": 1, "datasourceMode": "builtin", "datasourceKey": KEY,
                               "datasourceId": 2, "ddlFingerprint": ddl["ddlFingerprint"], "operationId": "123",
                               "status": "schema_ready", "schemaReady": True, "verifiedAt": "2026-11-05T00:00:00Z"})
        for module in ("app.services.builtin_authorization_bootstrap", "app.services.builtin_authorization_seed_runtime",
                       "app.services.application_config", "app.graph.workflow"):
            self.start_patch(module + ".read_application_config", return_value=self.application)
        self.start_patch("app.services.builtin_authorization_bootstrap.ensure_projection")
        self.dag = self.start_patch("app.services.builtin_authorization_seed_runtime.load_confirmed_build_task_plan",
                                    return_value={"status": "ready"})
        self.settings = Settings(model_base_url="", model_api_key="", model_name="",
                                 managed_database_base_url="https://saas.example", managed_database_service_key="fixture-secret")
        self.calls = []
        self.accepted = False
        self.drop = False
        self.post_error = None
        self.receipt_status = "RUNNING"
        self.detail_overrides = {}
        self.before_response = None

    def start_patch(self, target: str, **options):
        """在测试结束时恢复所替换的依赖。"""

        replacement = patch(target, **options)
        self.addCleanup(replacement.stop)
        return replacement.start()

    def compile(self) -> dict:
        """通过生产编译器生成当前虚构数据。"""

        return compile_builtin_authorization_seed(self.application, self.plan, datasource_key=KEY)

    def client(self) -> ManagedAuthorizationSeedClient:
        """每次创建独立客户端，恢复仅依赖持久文件。"""

        return ManagedAuthorizationSeedClient(self.settings, transport=httpx.MockTransport(self.respond))

    def envelope(self, status: int, body: dict, code: str = "SUC0000") -> httpx.Response:
        """按 Java 真实包装返回响应，错误文本包含虚构敏感内容以检查脱敏。"""

        return httpx.Response(status, json={"returnCode": code, "errorMsg": None if code == "SUC0000" else "fixture-secret",
                                           "body": body})

    def respond(self, request: httpx.Request) -> httpx.Response:
        """检查真实路径、POST 前落盘和结果查询身份。"""

        self.calls.append((request.method, request.url.path))
        self.assertEqual(request.headers["authorization"], "Bearer fixture-secret")
        compiled = self.compile()
        path = f"/managed-database/datasources/{KEY}/data-operations/"
        if request.method == "POST":
            self.assertEqual(request.url.path, path + "batch")
            self.assertEqual(json.loads(request.content), compiled["request"])
            marker = read_record(self.root, "state.json")["seed"]
            self.assertEqual(marker["status"], "submitting")
            self.assertEqual(marker["idempotencyKey"], compiled["idempotencyKey"])
            if self.post_error:
                return self.envelope(self.post_error, {}, "RESOURCE_BUSY")
            self.accepted = True
            if self.drop:
                raise httpx.ReadTimeout("fixture-secret", request=request)
            return self.envelope(202, {"operationId": "456", "status": self.receipt_status, "purpose": PURPOSE})
        self.assertIn(request.url.path, {path + "by-key/" + compiled["idempotencyKey"], path + "456"})
        if not self.accepted:
            return self.envelope(404, {}, "RESOURCE_NOT_FOUND")
        tables = compiled["request"]["tables"]
        body = {"operationId": "456", "status": "SUCCEEDED", "purpose": PURPOSE, "datasourceKey": KEY,
                "requestFingerprint": seed_request_fingerprint(KEY, compiled["request"]),
                "tableNames": [table["tableName"] for table in tables],
                "createdCount": sum(len(table["rows"]) for table in tables), "existingCount": 0,
                "verified": True, "errorCode": None, **self.detail_overrides}
        if self.before_response:
            self.before_response()
        return self.envelope(200, body)

    def run_node(self) -> dict:
        """走既有 Bootstrap 的真实 Schema 阶段分派。"""

        return run_builtin_authorization_bootstrap(str(self.root), self.plan, seed_client=self.client())

    def refresh(self, operation_id: str = "456") -> dict:
        """通过公开服务入口只读查询原 Seed。"""

        return refresh_builtin_authorization_seed(str(self.root), operation_id, client=self.client())

    def assert_calls(self, *methods: str) -> None:
        """核对实际网络访问顺序及次数。"""

        self.assertEqual([method for method, _ in self.calls], list(methods))

    def test_compile_four_tables_and_stable_fingerprints(self) -> None:
        """排序、去重、白名单及业务默认授权不进入实际行。"""

        compiled = self.compile()
        self.assertEqual(compiled["seedFingerprint"], "sha256:d1e0993dbdaaf114ea63a4924f142e34ecdfaaab1e2975d7ad62e45c3f958236")
        self.assertEqual(compiled["idempotencyKey"], "42e1a9ef9753b9f0ab410fe705b10b5627082e6c814784f830d5c6e25fa1f833")
        tables = compiled["request"]["tables"]
        self.assertEqual([table["tableName"] for table in tables],
                         [KEY + "_" + name for name in ("role", "resource", "role_resource", "role_member")])
        self.assertEqual([len(table["rows"]) for table in tables], [1, 2, 1, 2])
        self.assertEqual(tables[0]["rows"][0]["role_id"], "SYSTEM_ADMIN")
        self.assertEqual(tables[2]["rows"][0]["resource_key"], SYSTEM)
        self.assertEqual(tables[1]["rows"][0]["key"], "home")
        self.assertEqual(set(tables[1]["rows"][0]), {"key", "name", "description", "origin", "type", "target_resource_ref"})
        self.assertEqual([row["member_name"] for row in tables[3]["rows"]], ["admin-z", SUBJECT])
        self.application["authorization"]["initialAdministratorSubjects"].reverse()
        self.plan["authorization_manifest"]["resources"].reverse()
        self.assertEqual(self.compile(), compiled)

    def test_saas_java_request_fingerprint_vector(self) -> None:
        """复现 SaaS AuthorizationSeedFixture 生产 Java 解析器的固定向量。"""

        request = {"purpose": PURPOSE, "idempotencyKey": "a" * 64, "tables": [
            {"tableName": KEY + "_role", "rows": [{"role_id": "SYSTEM_ADMIN", "role_name": "管理员",
                "description": "系统角色", "enable": 1, "is_system": 1, "is_deleted": 0, "created_by": "SYSTEM"}]},
            {"tableName": KEY + "_resource", "rows": [
                {"key": SYSTEM, "name": "权限管理", "description": "系统资源", "origin": "system",
                 "type": "system", "target_resource_ref": "system:authorization_management"},
                {"key": "home", "name": "首页", "description": "含 SQL 字符: '); DROP TABLE test; --",
                 "origin": "business", "type": "page", "target_resource_ref": "page:home"}]},
            {"tableName": KEY + "_role_resource", "rows": [{"role_id": "SYSTEM_ADMIN", "resource_key": SYSTEM, "created_by": "SYSTEM"}]},
            {"tableName": KEY + "_role_member", "rows": [{"role_id": "SYSTEM_ADMIN", "member_id": "fixture-subject",
                                                         "member_name": "Fixture Admin", "created_by": "SYSTEM"}]},
        ]}
        expected = "sha256:326c7141c54f4f5fbd7663f97eb78476f5ece2db23ceeedf219b2591d6454078"
        self.assertEqual(seed_request_fingerprint(KEY, request), expected)
        request["idempotencyKey"] = "b" * 64
        self.assertEqual(seed_request_fingerprint(KEY, request), expected)
        request["tables"][1]["rows"].reverse()
        self.assertNotEqual(seed_request_fingerprint(KEY, request), expected)

    def test_employee_names_project_to_distinct_columns(self) -> None:
        """新格式按工号排序分列；旧 subject 保持原列值且冲突不能落入载荷。"""

        self.application["authorization"]["initialAdministratorSubjects"] = ["00123456/张三", "00000001/欧阳娜娜", SUBJECT]
        rows = self.compile()["request"]["tables"][3]["rows"]
        self.assertEqual([(row["member_id"], row["member_name"]) for row in rows],
                         [("00000001", "欧阳娜娜"), ("00123456", "张三"), (SUBJECT, SUBJECT)])
        for values in (["00123456/张三", "00123456/李四"], ["00123456", "00123456/张三"]):
            self.application["authorization"]["initialAdministratorSubjects"] = values
            with self.assertRaisesRegex(ValueError, "冲突"):
                self.compile()

    def test_requires_schema_and_confirmed_dag(self) -> None:
        """Schema 或 DAG 未就绪时零 Seed 请求。"""

        self.dag.return_value = None
        self.assertFalse(self.run_node()["seed_ready"])
        record = read_record(self.root, "state.json")
        save_state(self.root, {**record, "schemaReady": False})
        with patch("app.services.builtin_authorization_bootstrap._run_remote", return_value={"seed_ready": False}):
            self.assertFalse(self.run_node()["seed_ready"])
        self.assert_calls()

    def test_receipt_never_ready_then_get_and_reuse(self) -> None:
        """202 即使报告 SUCCEEDED 也需要原 GET，之后 Build 和预览复用同一事实。"""

        self.receipt_status = "SUCCEEDED"
        pending = self.run_node()
        self.assertTrue(pending["schema_ready"])
        self.assertFalse(pending["seed_ready"])
        self.assertEqual(pending["operation_id"], "123")
        self.assertEqual(pending["seed_operation_id"], "456")
        self.assert_calls("GET", "POST")
        self.assertFalse(seed_ready(self.root, self.application, self.plan))
        with self.assertRaisesRegex(ValueError, "正在初始化"):
            _require_preview_authorization_ready(self.root)
        self.assertTrue(self.refresh()["seed_ready"])
        self.assertTrue(seed_ready(self.root, self.application, self.plan))
        _require_preview_authorization_ready(self.root)
        self.assertEqual(self.run_node()["status"], "reused")
        for resume in ("build", "authorization_bootstrap", "builtin_authorization_bootstrap"):
            state = {"workspace": str(self.root), "technical_plan": self.plan, "resume_from": resume}
            self.assertEqual(route_workflow_start(state), "build")
        self.assert_calls("GET", "POST", "GET")
        public = json.dumps([pending, self.refresh(), read_record(self.root, "state.json")], ensure_ascii=False)
        for private in (SUBJECT, "admin-z", "fixture-secret", "member_id", "sourceRuleIds"):
            self.assertNotIn(private, public)

    def test_accepted_without_receipt_only_queries_by_key(self) -> None:
        """响应丢失后重启只查原键，暂查不到也不能再次 POST。"""

        self.drop = True
        result = self.run_node()
        self.assertEqual(result["failure_category"], "seed_timeout")
        self.assertEqual(result["next_action"], "lookup_seed")
        self.accepted = False
        self.assertEqual(self.run_node()["failure_category"], "seed_operation_not_found")
        self.accepted = True
        self.assertTrue(self.run_node()["seed_ready"])
        self.assert_calls("GET", "POST", "GET", "GET")
        self.assertTrue(self.calls[-1][1].endswith("/by-key/" + self.compile()["idempotencyKey"]))

    def test_existing_remote_operation_without_local_seed(self) -> None:
        """仅有 Schema 状态时按键复用同输入的远端操作。"""

        self.accepted = True
        self.assertTrue(self.run_node()["seed_ready"])
        self.assert_calls("GET")

    def test_nonterminal_failed_and_unknown_never_replay(self) -> None:
        """执行中、明确失败和未知均只能读原 ID。"""

        self.run_node()
        for status in ("RUNNING", "FAILED", "UNKNOWN"):
            self.detail_overrides = {"status": status, "verified": False, "createdCount": 0}
            result = self.run_node()
            self.assertFalse(result["seed_ready"])
            self.assertEqual(result["seed_operation_status"], status)
            self.assertTrue(self.calls[-1][1].endswith("/456"))
        self.assert_calls("GET", "POST", "GET", "GET", "GET")

    def test_invalid_remote_identity_or_verification(self) -> None:
        """回执错库、错表、错指纹、缺字段或验证不充分均不覆盖本地事实。"""

        self.run_node()
        baseline = read_record(self.root, "state.json")
        for change in ({"datasourceKey": "112233445566"}, {"operationId": "789"}, {"verified": False},
                       {"tableNames": ["fake"] * 4}, {"requestFingerprint": "sha256:" + "0" * 64},
                       {"createdCount": 1}, {"existingCount": None}):
            with self.subTest(change=change):
                self.detail_overrides = change
                self.assertFalse(self.refresh()["seed_ready"])
                self.assertEqual(read_record(self.root, "state.json"), baseline)
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_input_change_after_seed_requires_review(self) -> None:
        """管理员或资源实际行改变后拒绝旧成功，也不发新的 Seed。"""

        self.run_node()
        self.refresh()
        self.application["authorization"]["initialAdministratorSubjects"] = ["other"]
        self.assertEqual(self.run_node()["failure_category"], "seed_input_changed")
        with self.assertRaises(ValueError):
            self.refresh()
        self.application["authorization"]["initialAdministratorSubjects"] = [SUBJECT, "admin-z"]
        self.plan["authorization_manifest"]["resources"][0]["name"] = "新首页"
        self.plan["authorization_manifest"] = _with_fingerprint(self.plan["authorization_manifest"])
        save_plan(self.root, self.plan)
        self.assertEqual(self.run_node()["failure_category"], "seed_input_changed")
        self.assertFalse(seed_ready(self.root, self.application, self.plan))
        self.assert_calls("GET", "POST", "GET")

    def test_stale_refresh_id_and_current_binding_are_rejected(self) -> None:
        """Schema ID、换库、权限关闭和 DDL 漂移均在网络前拒绝。"""

        self.run_node()
        with self.assertRaises(ValueError):
            self.refresh("123")
        self.application["managedDatabase"]["datasourceId"] = 3
        with self.assertRaises(ValueError):
            self.refresh()
        self.application["managedDatabase"]["datasourceId"] = 2
        self.application["authorization"]["enabled"] = False
        with self.assertRaises(ValueError):
            self.refresh()
        self.application["authorization"]["enabled"] = True
        with self.ddl.open("a") as stream:
            stream.write("\n-- changed DDL")
        with self.assertRaises(ValueError):
            self.refresh()
        self.assert_calls("GET", "POST")

    def test_binding_changed_during_get_cannot_commit_ready(self) -> None:
        """迟到回执不能写入已换库的应用状态。"""

        self.run_node()

        def change_binding() -> None:
            """在请求与结果落盘之间切换本地挂载。"""

            self.application["managedDatabase"]["datasourceId"] = 3

        self.before_response = change_binding
        with self.assertRaisesRegex(ValueError, "与当前应用不符"):
            self.refresh()
        self.assertFalse(read_record(self.root, "state.json")["seed"]["ready"])

    def test_failed_marker_write_does_not_post(self) -> None:
        """保护标记写失败时停止于首次按键 GET。"""

        with patch("app.services.builtin_authorization_seed_runtime.save_state", side_effect=OSError):
            result = self.run_node()
        self.assertEqual(result["failure_category"], "seed_state_write_failed")
        self.assertNotIn("seed", read_record(self.root, "state.json"))
        self.assert_calls("GET")

    def test_definite_http_errors_do_not_replay(self) -> None:
        """HTTP 失败保留安全分类并继续保护可能已受理的操作。"""

        baseline = read_record(self.root, "state.json")
        for status in (400, 409, 500):
            with self.subTest(status=status):
                save_state(self.root, baseline)
                self.calls.clear()
                self.post_error = status
                self.assertEqual(self.run_node()["failure_category"], f"seed_http_{status}")
                self.assertEqual(self.run_node()["failure_category"], "seed_operation_not_found")
                self.assert_calls("GET", "POST", "GET")

    def test_current_saas_contract_rejects_action_refs_and_empty_descriptions(self) -> None:
        """Direct 合法但 SaaS 尚未支持的实际行保留原值，受理前停止。"""

        manifest = deepcopy(self.plan["authorization_manifest"])
        manifest["resources"][0]["description"] = ""
        self.plan["authorization_manifest"] = _with_fingerprint(manifest)
        save_plan(self.root, self.plan)
        self.assertEqual(self.run_node()["failure_category"], "seed_saas_contract_incompatible")
        manifest["resources"][0].update(type="operation", targetResourceRef="action:home:edit", description="编辑首页")
        self.plan["authorization_manifest"] = _with_fingerprint(manifest)
        save_plan(self.root, self.plan)
        self.assertEqual(self.compile()["request"]["tables"][1]["rows"][0]["target_resource_ref"], "action:home:edit")
        self.assertEqual(self.run_node()["failure_category"], "seed_saas_contract_incompatible")
        self.assert_calls()

    def test_invalid_subjects_manifest_and_size_fail_closed(self) -> None:
        """校验占位管理员、指纹漂移、重复资源和 SaaS 字节边界。"""

        for subjects in ([], ["current-user"], [" "], [42], ["长" * 33], ["a" + str(i) for i in range(101)]):
            self.application["authorization"]["initialAdministratorSubjects"] = subjects
            with self.assertRaises(ValueError):
                self.compile()
        self.application["authorization"]["initialAdministratorSubjects"] = [SUBJECT]
        manifest = self.plan["authorization_manifest"]
        manifest["resources"][0]["description"] = "变动"
        with self.assertRaisesRegex(ValueError, "指纹"):
            self.compile()
        manifest["resources"].append(manifest["resources"][0])
        with self.assertRaisesRegex(ValueError, "重复"):
            self.compile()
        self.plan = seed_plan()
        manifest = self.plan["authorization_manifest"]
        manifest["resources"] += [{"resourceKey": f"page_{i}", "name": "页" * 32, "description": "说" * 128,
                                   "origin": "business", "type": "page", "targetResourceRef": f"page:page_{i}"}
                                  for i in range(498)]
        self.plan["authorization_manifest"] = _with_fingerprint(manifest)
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.compile()

    def test_seed_ag_ui_refresh_projects_safe_identity(self) -> None:
        """独立动作只查询一次 Seed，并返回标准生命周期及两个阶段编号。"""

        self.run_node()
        self.calls.clear()

        async def collect() -> str:
            """收集独立 AG-UI 流完整帧。"""

            payload = {"threadId": "seed-thread", "runId": "seed-run", "forwardedProps": {"builtinAuthorization": {
                "action": "refresh", "operationKind": "seed", "workspaceRoot": str(self.root), "operationId": "456"}}}
            return "".join([frame async for frame in build_builtin_authorization_stream(payload)])

        with patch("app.services.builtin_authorization_seed_runtime.ManagedAuthorizationSeedClient", return_value=self.client()):
            frames = asyncio.run(collect()).replace(" ", "")
        for value in ("RUN_STARTED", "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED",
                      '"schemaReady":true', '"seedReady":true', '"operationId":"123"', '"seedOperationId":"456"'):
            self.assertIn(value, frames)
        self.assertNotIn(SUBJECT, frames)
        self.assert_calls("GET")
