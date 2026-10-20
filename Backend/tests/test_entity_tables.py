"""验证实体 SQL 确认、应用前缀和 SaaS 异步建表合同，不连接真实数据库。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.config import Settings
from app.domain.entity_tables import EntityTableRequest
from app.protocols.entity_tables import build_entity_tables_stream
from app.services.entity_tables import prepare_entity_table, run_entity_table_action
from app.services.managed_database_client import ManagedDatabaseRegistrationError
from app.services.managed_table_client import ManagedTableClient


class EntityTableTests(unittest.TestCase):
    """只用临时当前合同工件和 HTTP 替身验证副作用边界。"""

    def setUp(self) -> None:
        """准备已确认技术规划及已注册模拟数据库应用。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        root = self.workspace / ".devagentstudio"
        (root / "plans").mkdir(parents=True)
        self.application_path = root / "application.json"
        self.application = {
            "schemaVersion": 6, "configRevision": 3, "appName": "实体测试",
            "auth": {"enable": False},
            "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"datasourceKey": "12ab34cd56ef", "datasourceId": 2, "status": "REGISTERED"},
        }
        self.plan_path = root / "plans" / "technical-plan.json"
        self.plan = {
            "artifact_type": "technical-plan", "confirmation_status": "confirmed",
            "entities": [{"id": "Order", "name": "订单'表", "table_name": "12ab34cd56ef_order",
                          "fields": [{"name": "amount", "label": "金额", "type": "decimal", "required": True}]}],
        }
        self.write_inputs()
        self.calls: list[httpx.Request] = []

    def write_inputs(self) -> None:
        """写入本测试当前快照，不迁移或复用历史工件。"""

        self.application_path.write_text(json.dumps(self.application), encoding="utf-8")
        self.plan_path.write_text(json.dumps(self.plan), encoding="utf-8")

    def request(self, action: str = "prepare", **extra) -> EntityTableRequest:
        """组装正式产品动作输入。"""

        return EntityTableRequest.model_validate({"workspaceRoot": str(self.workspace),
                                                  "entityId": "Order", "action": action, **extra})

    def client(self, handler) -> ManagedTableClient:
        """配置固定假密钥和传输替身，不访问网络。"""

        return ManagedTableClient(Settings(model_base_url="http://model.invalid", model_api_key="test",
                                          model_name="test", managed_database_service_key="test-service-key"),
                                  transport=httpx.MockTransport(handler))

    def test_sql_uses_bound_name_and_fixed_table_options(self) -> None:
        """SQL 引用完整真实表名，包含主键、字段与固定建表约束。"""

        result = run_entity_table_action(self.request())
        sql = result["preview"]["sql"]
        self.assertIn("CREATE TABLE `12ab34cd56ef_order`", sql)
        self.assertIn("`amount` DECIMAL(12,2) NOT NULL", sql)
        self.assertIn("PRIMARY KEY (`id`)", sql)
        self.assertTrue(sql.endswith("ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='订单''表';"))
        self.assertNotIn("IF NOT EXISTS", sql)
        self.assertNotIn("test-service-key", json.dumps(result))

    def test_execute_requires_current_confirmation_and_only_submits_once(self) -> None:
        """当前确认通过后只发一次 CREATE，受理不伪装为建表成功。"""

        def handler(request: httpx.Request) -> httpx.Response:
            """记录唯一请求并返回 SaaS 真实受理结构。"""
            self.calls.append(request)
            return httpx.Response(202, json={"operationId": "123", "status": "RUNNING"})

        client = self.client(handler)
        preview = prepare_entity_table(self.request())
        with self.assertRaisesRegex(ValueError, "明确确认"):
            run_entity_table_action(self.request("execute", previewHash=preview["previewHash"]), client=client)
        self.plan["entities"][0]["fields"][0]["required"] = False
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "设计变化"):
            run_entity_table_action(self.request("execute", confirmed=True, previewHash=preview["previewHash"]), client=client)
        self.assertEqual(self.calls, [])
        preview = prepare_entity_table(self.request())
        result = run_entity_table_action(self.request("execute", confirmed=True, previewHash=preview["previewHash"]), client=client)
        self.assertEqual(result["receipt"], {"operationId": "123", "status": "RUNNING"})
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0].url.path, "/managed-database/datasources/12ab34cd56ef/table-operations")
        self.assertEqual(json.loads(self.calls[0].content), {"mode": "CREATE", "sql": preview["sql"]})
        self.assertNotIn("readiness", result)

    def test_rebuild_requires_data_loss_and_current_sql_confirmation(self) -> None:
        """重建缺任一确认或使用旧 SQL 指纹时不发送远端请求。"""

        preview = prepare_entity_table(self.request())
        with patch.object(ManagedTableClient, "request") as remote:
            for extra in [
                {"previewHash": preview["previewHash"], "allowDataLoss": True},
                {"previewHash": preview["previewHash"], "confirmed": True},
                {"previewHash": "0" * 64, "confirmed": True, "allowDataLoss": True},
            ]:
                with self.subTest(extra=extra), self.assertRaises(ValueError):
                    run_entity_table_action(self.request("rebuild", **extra))
            remote.assert_not_called()

    def test_rebuild_submits_once_with_data_loss_and_fixed_target(self) -> None:
        """只在已停止预览且明确确认后提交一次同表 REBUILD。"""

        def handler(request: httpx.Request) -> httpx.Response:
            """记录重建请求，不实际删除或创建任何表。"""
            self.calls.append(request)
            return httpx.Response(202, json={"operationId": "456", "status": "RUNNING"})

        preview = prepare_entity_table(self.request())
        request = self.request("rebuild", previewHash=preview["previewHash"], confirmed=True, allowDataLoss=True)
        with patch("app.services.entity_tables.inspect_project_preview", return_value={"running": False}) as inspect:
            result = run_entity_table_action(request, client=self.client(handler))
            inspect.assert_called_once_with(str(self.workspace))
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(json.loads(self.calls[0].content), {
            "mode": "REBUILD", "sql": preview["sql"], "allowDataLoss": True,
        })
        self.assertEqual(result["receipt"]["status"], "RUNNING")
        self.assertNotIn("readiness", result)

    def test_running_preview_blocks_rebuild_before_remote_submission(self) -> None:
        """应用仍运行时后端拒绝重建，不能只依靠前端确认提示。"""

        preview = prepare_entity_table(self.request())
        request = self.request("rebuild", previewHash=preview["previewHash"], confirmed=True, allowDataLoss=True)
        client = self.client(lambda _request: httpx.Response(202, json={}))
        with patch("app.services.entity_tables.inspect_project_preview", return_value={"running": True}), \
                patch.object(client, "request") as remote:
            with self.assertRaisesRegex(ValueError, "先停止预览"):
                run_entity_table_action(request, client=client)
            remote.assert_not_called()

    def test_lost_rebuild_response_does_not_resubmit_destructive_operation(self) -> None:
        """重建响应丢失后只提示核对，不再次发送破坏性操作。"""

        def handler(request: httpx.Request) -> httpx.Response:
            """模拟重建已受理但连接中断。"""
            self.calls.append(request)
            raise httpx.ReadTimeout("test timeout", request=request)

        preview = prepare_entity_table(self.request())
        request = self.request("rebuild", previewHash=preview["previewHash"], confirmed=True, allowDataLoss=True)
        with patch("app.services.entity_tables.inspect_project_preview", return_value={"running": False}):
            with self.assertRaisesRegex(ManagedDatabaseRegistrationError, "不要重复提交"):
                run_entity_table_action(request, client=self.client(handler))
        self.assertEqual(len(self.calls), 1)

    def test_explicit_id_uses_one_implicit_primary_key_without_renaming(self) -> None:
        """正常规划中的 id 复用隐式主键，不报重复或按展示标签新增列。"""

        for label in ["申请ID", "申请编号", "Application ID"]:
            with self.subTest(label=label):
                self.plan["entities"][0]["fields"] = [
                    {"name": "id", "label": label, "type": "text", "required": True},
                    {"name": "amount", "label": "金额", "type": "decimal", "required": True},
                ]
                self.write_inputs()
                before = self.plan_path.read_text()
                preview = prepare_entity_table(self.request())
                self.assertEqual([column["name"] for column in preview["columns"]], ["id", "amount"])
                self.assertEqual(preview["columns"][0]["type"], "BIGINT")
                self.assertTrue(preview["columns"][0]["auto_increment"])
                self.assertEqual(preview["sql"].count("`id` BIGINT"), 1)
                self.assertEqual(self.plan_path.read_text(), before)

    def test_real_duplicate_id_and_business_field_remain_rejected(self) -> None:
        """接纳一个 id 后仍拒绝两个 id 或真正同名的业务字段。"""

        for name in ["id", "amount"]:
            with self.subTest(name=name):
                self.plan["entities"][0]["fields"] = [
                    {"name": name, "label": "字段一", "type": "number"},
                    {"name": name, "label": "字段二", "type": "number"},
                ]
                self.write_inputs()
                with self.assertRaisesRegex(ValueError, f"无效或重复字段 `{name}`"):
                    prepare_entity_table(self.request())

    def test_draft_unbound_or_foreign_prefix_is_rejected(self) -> None:
        """未确认或未绑定的设计不能在执行入口静默修复。"""

        self.plan["confirmation_status"] = "pending_user_confirmation"
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "尚未确认"):
            prepare_entity_table(self.request())
        self.plan["confirmation_status"] = "confirmed"
        del self.plan["entities"][0]["table_name"]
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "固定真实表名"):
            prepare_entity_table(self.request())
        self.plan["entities"][0]["table_name"] = "ab1234cd56ef_order"
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "不一致"):
            prepare_entity_table(self.request())

    def test_refresh_only_queries_history_and_current_structure(self) -> None:
        """刷新读取 SaaS 历史和当前 SQL 结构，不提交表操作。"""

        def handler(request: httpx.Request) -> httpx.Response:
            """仅接受只读查询和单实体检查，其他调用立即失败。"""
            self.calls.append(request)
            if request.method == "GET":
                return httpx.Response(200, json=[{"operationId": "123", "tableName": "12ab34cd56ef_order",
                    "tableNames": ["12ab34cd56ef_order"], "createdAt": "2026-10-15T02:00:00Z",
                    "mode": "CREATE", "status": "SUCCEEDED", "executionResult": {"steps": [], "recoveryState": "NOT_REQUESTED"}}])
            self.assertTrue(request.url.path.endswith("/tables/check"))
            self.assertEqual(len(json.loads(request.content)["sqls"]), 1)
            return httpx.Response(200, json={"ready": False, "datasourceId": 2, "tables": [{
                "tableName": "12ab34cd56ef_order", "status": "STRUCTURE_MISMATCH", "differences": [{
                    "category": "COLUMN", "target": "amount", "expected": "DECIMAL(12,2)", "actual": "BIGINT"}]}]})

        result = run_entity_table_action(self.request("refresh"), client=self.client(handler))
        self.assertEqual(result["operation"]["status"], "SUCCEEDED")
        self.assertFalse(result["readiness"]["ready"])
        self.assertEqual(len(self.calls), 2)

    def test_refresh_accepts_cleanup_history_and_matches_exact_target(self) -> None:
        """删除及资源回收记录不会阻断实体刷新，也不能匹配相似表名前缀。"""

        table = "12ab34cd56ef_order"

        def entry(operation_id: str, mode: str, target: str, targets: list[str]) -> dict:
            """构造 SaaS 当前完整操作记录，附带不允许投射到产品的内部字段。"""

            return {"operationId": operation_id, "mode": mode, "tableName": target, "tableNames": targets,
                    "createdAt": "2026-10-15T02:00:00Z", "status": "SUCCEEDED",
                    "executionResult": {"recoveryState": "NOT_REQUESTED", "steps": [{"sql": "private-sql"}]}}

        unrelated = entry("201", "DROP", table + "_archive", [table + "_archive"])
        for mode, target, targets, expected in [
            ("CREATE", table, [table], "200"),
            ("REBUILD", table, [table], "200"),
            ("DROP", table, [table], "200"),
            ("RELEASE", "", [table, table + "_archive"], "200"),
            ("RELEASE", "", [table + "_archive"], None),
        ]:
            with self.subTest(mode=mode, targets=targets):
                self.calls.clear()
                history = [unrelated, entry("200", mode, target, targets)]

                def handler(request: httpx.Request) -> httpx.Response:
                    """只返回历史与当前结构，不受理任何删除或回收请求。"""

                    self.calls.append(request)
                    if request.method == "GET":
                        self.assertTrue(request.url.path.endswith("/table-operations"))
                        return httpx.Response(200, json=history)
                    self.assertEqual(request.method, "POST")
                    self.assertTrue(request.url.path.endswith("/tables/check"))
                    status = "NOT_CREATED" if mode in {"DROP", "RELEASE"} else "SYNCED"
                    return httpx.Response(200, json={"ready": status == "SYNCED", "datasourceId": 2,
                        "tables": [{"tableName": table, "status": status, "differences": []}]})

                result = run_entity_table_action(self.request("refresh"), client=self.client(handler))
                if expected is None:
                    self.assertIsNone(result["operation"])
                else:
                    self.assertEqual(result["operation"]["operationId"], expected)
                    self.assertEqual(result["operation"]["mode"], mode)
                self.assertEqual(len(self.calls), 2)
                self.assertNotIn("private-sql", json.dumps(result))

    def test_cleanup_modes_cannot_be_submitted_as_entity_actions(self) -> None:
        """扩大历史读取范围不允许实体入口提交删除或回收动作。"""

        with patch.object(ManagedTableClient, "request") as remote:
            for action in ["DROP", "RELEASE", "drop", "release"]:
                with self.subTest(action=action), self.assertRaises(ValueError):
                    self.request(action)
            remote.assert_not_called()

    def test_lost_submission_response_is_not_retried(self) -> None:
        """网络中断只提交一次，明确提示先核对原记录。"""

        def handler(request: httpx.Request) -> httpx.Response:
            """模拟请求已发出但响应丢失。"""
            self.calls.append(request)
            raise httpx.ReadTimeout("test timeout", request=request)

        preview = prepare_entity_table(self.request())
        with self.assertRaisesRegex(ManagedDatabaseRegistrationError, "不要重复提交"):
            run_entity_table_action(self.request("execute", confirmed=True, previewHash=preview["previewHash"]),
                                    client=self.client(handler))
        self.assertEqual(len(self.calls), 1)

    def test_false_ready_or_wrong_datasource_cannot_report_synced(self) -> None:
        """不完整的绿色结果或错误挂载不能作为当前实体已同步的证据。"""

        response = {"ready": True, "datasourceId": 2, "tables": []}

        def handler(request: httpx.Request) -> httpx.Response:
            """为历史与结构检查分别提供可控响应。"""
            return httpx.Response(200, json=[] if request.method == "GET" else response)

        with self.assertRaisesRegex(ValueError, "完整确认"):
            run_entity_table_action(self.request("refresh"), client=self.client(handler))
        response["datasourceId"] = 3
        with self.assertRaisesRegex(ValueError, "数据源"):
            run_entity_table_action(self.request("refresh"), client=self.client(handler))

    def test_invalid_field_and_external_mode_do_not_generate_sql(self) -> None:
        """拒绝字段越界及把外部数据库误作为 SaaS 执行目标。"""

        self.plan["entities"][0]["fields"][0]["name"] = "amount`); DROP TABLE other;"
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "无效或重复字段"):
            prepare_entity_table(self.request())
        self.application["datasource"]["db"]["useBuiltin"] = False
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "仅用于模拟数据库"):
            prepare_entity_table(self.request())

    def test_ag_ui_success_and_failure_have_complete_lifecycle(self) -> None:
        """成功与业务失败都发送消息、结果、状态和结束事件。"""

        async def collect(payload: dict) -> str:
            """收集一次实际协议输出用于检查生命周期。"""
            return "".join([part async for part in build_entity_tables_stream(payload=payload)])

        payload = {"threadId": "table-test", "runId": "table-run", "forwardedProps": {"entityTables": {
            "action": "prepare", "workspaceRoot": str(self.workspace), "entityId": "Order"}}}
        with patch("app.services.preview_runtime_guard.record_product_interaction"):
            success = asyncio.run(collect(payload))
            payload["forwardedProps"]["entityTables"]["action"] = "execute"
            failure = asyncio.run(collect(payload))
        for output in [success, failure]:
            for event in ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED", "entity-tables"]:
                self.assertIn(event, output)
        self.assertIn('"status":"completed"', success)
        self.assertIn('"status":"failed"', failure)
