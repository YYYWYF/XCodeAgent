"""验证普通实体 DDL 预览的编译一致性、只读边界及 AG-UI 生命周期。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.entity_tables import EntityTableRequest
from app.protocols.entity_tables import build_entity_tables_stream, entity_tables_capabilities
from app.services.entity_tables import run_entity_table_action


class EntityDdlTests(unittest.TestCase):
    """从当前技术规划读取 DDL，不配置数据库或连接远端。"""

    def setUp(self) -> None:
        """准备已确认技术规划，故意不创建应用配置以验证预览不依赖连接。"""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.workspace = Path(directory.name)
        self.path = self.workspace / ".devagentstudio/plans/technical-plan.json"
        self.path.parent.mkdir(parents=True)
        self.plan = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "entities": [
            {"id": "Product", "name": "商品", "fields": [
                {"name": "id", "type": "text", "label": "编号", "required": True},
                {"name": "product_name", "type": "text", "label": "商品'名称", "required": True},
                {"name": "status", "type": "enum", "label": "商品状态", "required": True},
                {"name": "description", "type": "long_text", "label": "商品说明", "required": False},
            ]},
        ]}
        self.write_plan()

    def write_plan(self) -> None:
        """写入本轮当前合同规划，便于测试修订后重新读取。"""
        self.path.write_text(json.dumps(self.plan, ensure_ascii=False), encoding="utf-8")

    def request(self, **extra) -> EntityTableRequest:
        """构造只包含工作区与实体身份的只读动作。"""
        return EntityTableRequest.model_validate({"action": "preview_ddl", "entityId": "Product",
                                                   "workspaceRoot": str(self.workspace), **extra})

    def test_preview_reuses_compiler_without_config_remote_or_writes(self) -> None:
        """固定类型、注释转义与隐式主键沿用共用编译器，预览不触发副作用。"""
        before = self.path.read_bytes()
        with patch("app.services.entity_tables.ApplicationConfigRepository") as repository, \
                patch("app.services.entity_tables.ManagedTableClient") as remote:
            preview = run_entity_table_action(self.request())["ddlPreview"]
            repository.assert_not_called()
            remote.assert_not_called()
        self.assertEqual(preview["entityId"], "Product")
        self.assertEqual(preview["tableName"], "product")
        self.assertIn("`product_name` VARCHAR(255) NOT NULL COMMENT '商品''名称'", preview["sql"])
        self.assertIn("`status` VARCHAR(32) NOT NULL", preview["sql"])
        self.assertIn("`description` TEXT NULL", preview["sql"])
        self.assertEqual(preview["sql"].count("`id` BIGINT"), 1)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(set(preview), {"entityId", "entityName", "tableName", "sql"})

    def test_preview_reads_current_fields_and_quotes_explicit_table_name(self) -> None:
        """重新读取修订后的正式字段，MySQL 标识符中的反引号不能越过表名。"""
        self.plan["entities"][0]["table_name"] = "商品`资料"
        self.plan["entities"][0]["fields"][1]["required"] = False
        self.write_plan()
        sql = run_entity_table_action(self.request())["ddlPreview"]["sql"]
        self.assertTrue(sql.startswith("CREATE TABLE `商品``资料`"))
        self.assertIn("`product_name` VARCHAR(255) NULL", sql)

    def test_missing_draft_duplicate_and_invalid_fields_are_rejected(self) -> None:
        """不回退到历史规划、空表或客户端字段，非法规划明确返回业务失败。"""
        for change in ["draft", "missing", "duplicate", "invalid", "empty"]:
            with self.subTest(change=change):
                original = json.loads(json.dumps(self.plan))
                if change == "draft":
                    self.plan["confirmation_status"] = "pending_user_confirmation"
                elif change == "missing":
                    self.plan["entities"] = []
                elif change == "duplicate":
                    self.plan["entities"] *= 2
                elif change == "invalid":
                    self.plan["entities"][0]["fields"][1]["name"] = "name`; DROP TABLE other"
                else:
                    self.plan["entities"][0]["fields"] = []
                self.write_plan()
                with self.assertRaises(ValueError):
                    run_entity_table_action(self.request())
                self.plan = original
        with self.assertRaises(ValueError):
            self.request(sql="CREATE TABLE other (id INT)")

    def test_ag_ui_readonly_success_and_failure_finish_without_workspace_write(self) -> None:
        """只读预览仍返回完整生命周期，但不登记为应用修改或启动数据库动作。"""
        async def collect() -> str:
            """收集正式协议事件，不使用另建的测试专用传输。"""
            return "".join([part async for part in build_entity_tables_stream(payload={
                "threadId": "ddl-test", "runId": "ddl-run", "forwardedProps": {"entityTables": {
                    "action": "preview_ddl", "entityId": "Product", "workspaceRoot": str(self.workspace)}}})])
        with patch("app.services.preview_runtime_guard.record_product_interaction") as interaction:
            success = asyncio.run(collect())
            self.plan["confirmation_status"] = "pending_user_confirmation"
            self.write_plan()
            failure = asyncio.run(collect())
            interaction.assert_not_called()
        for output in [success, failure]:
            for event in ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED", "entity-tables"]:
                self.assertIn(event, output)
        self.assertIn('"status":"completed"', success)
        self.assertIn('"ddlPreview"', success)
        self.assertIn('"status":"failed"', failure)
        self.assertIn("preview_ddl", entity_tables_capabilities()["actions"])
