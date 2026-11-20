"""验证实体 AI DDL 的模型输入、SQL 校验、缓存及 AG-UI 生命周期。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.entity_tables import EntityTableRequest
from app.protocols.entity_tables import build_entity_tables_stream, entity_tables_capabilities
from app.services.entity_tables import run_entity_table_action


_PRODUCT_SQL = """CREATE TABLE `product` (
  `code` VARCHAR(64) NOT NULL COMMENT '商品编码（唯一标识）',
  `category_code` VARCHAR(64) NOT NULL COMMENT '分类编码',
  `status` VARCHAR(32) NOT NULL COMMENT '商品状态',
  `description` VARCHAR(512) NULL COMMENT '商品说明，含 ''引号'',  ; 和括号 )',
  `stock` INT NOT NULL COMMENT '库存数量',
  `external_serial` BIGINT NOT NULL COMMENT '外部流水号',
  `price` DECIMAL(12,2) NOT NULL COMMENT '商品单价',
  `featured` TINYINT(1) NOT NULL COMMENT '是否推荐',
  `release_date` TIMESTAMP NULL COMMENT '发布日期',
  `created_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP COMMENT '创建时间',
  `updated_at` TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP COMMENT '更新时间',
  PRIMARY KEY (`code`),
  KEY `idx_category_code` (`category_code`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_bin COMMENT='商品';"""


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
                {"name": "code", "type": "text", "label": "商品编码（唯一标识）", "required": True},
                {"name": "category_code", "type": "text", "label": "分类编码", "required": True,
                 "description": "按分类筛选商品，分类编码取值丰富，每次筛选通常只返回少量商品"},
                {"name": "status", "type": "enum", "label": "商品状态", "required": True,
                 "enum_values": ["上架", "下架"]},
                {"name": "description", "type": "long_text", "label": "商品说明", "required": False},
                {"name": "stock", "type": "number", "label": "库存数量", "required": True},
                {"name": "external_serial", "type": "number", "label": "外部流水号", "required": True,
                 "description": "上游系统使用 64 位整数作为流水号"},
                {"name": "price", "type": "decimal", "label": "商品单价", "required": True},
                {"name": "featured", "type": "boolean", "label": "是否推荐", "required": True},
                {"name": "release_date", "type": "date", "label": "发布日期", "required": False},
                {"name": "created_at", "type": "datetime", "label": "创建时间", "required": True},
                {"name": "updated_at", "type": "datetime", "label": "更新时间", "required": True},
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

    def test_preview_returns_validated_ai_sql_without_config_remote_or_writes(self) -> None:
        """保留 AI 生成的主键、索引及审计规则，并复用同一规划的结果。"""
        before = self.path.read_bytes()
        with patch("app.services.entity_tables.ApplicationConfigRepository") as repository, \
                patch("app.services.entity_tables.ManagedDatabaseClient") as remote, \
                patch("app.agents.entity_ddl.create_chat_model") as factory:
            invoke = factory.return_value.invoke
            invoke.return_value.content = json.dumps({"sql": _PRODUCT_SQL, "error": None})
            preview = run_entity_table_action(self.request())["ddlPreview"]
            self.assertEqual(run_entity_table_action(self.request())["ddlPreview"], preview)
            invoke.assert_called_once()
            system, human = invoke.call_args.args[0]
            self.assertIn("DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP", system[1])
            self.assertIn("VARCHAR(512)", system[1])
            self.assertIn("enum 使用 VARCHAR(n)", system[1])
            self.assertIn("number 默认使用 INT", system[1])
            self.assertIn("date 和 datetime 均使用不带精度参数的 TIMESTAMP", system[1])
            self.assertIn("每列、主键和普通索引各独占一行", system[1])
            self.assertIn("取值区分度高", system[1])
            self.assertEqual(json.loads(human[1].split("\n", 1)[1])["fields"], self.plan["entities"][0]["fields"])
            repository.assert_not_called()
            remote.assert_not_called()
        self.assertEqual(preview["entityId"], "Product")
        self.assertEqual(preview["tableName"], "product")
        self.assertEqual(preview["sql"], _PRODUCT_SQL)
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(preview["confirmationStatus"], "draft")
        self.assertRegex(preview["draftId"], r"^[0-9a-f]{32}$")
        self.assertFalse((self.path.parent / "entity-ddl").exists())

    def test_revised_ddl_is_saved_only_after_confirming_current_draft(self) -> None:
        """失败保留旧稿，修订换版本，只确认当前版本并幂等读取正式 SQL。"""
        saved_dir = self.path.parent / "entity-ddl"
        revised_sql = _PRODUCT_SQL.replace("`category_code` VARCHAR(64)", "`category_code` VARCHAR(128)")
        feedback = "把分类编码长度改为 128。"
        with patch("app.services.entity_ddl.generate_entity_ddl", side_effect=[
            _PRODUCT_SQL, ValueError("生成失败"), revised_sql,
        ]) as model:
            first = run_entity_table_action(self.request())["ddlPreview"]
            revise = self.request(action="revise_ddl", draftId=first["draftId"], feedback=feedback)
            with self.assertRaisesRegex(ValueError, "生成失败"):
                run_entity_table_action(revise)
            self.assertEqual(run_entity_table_action(self.request())["ddlPreview"], first)

            revised = run_entity_table_action(revise)["ddlPreview"]
            self.assertEqual(model.call_args.kwargs, {"previous_sql": _PRODUCT_SQL, "feedback": feedback})
            self.assertNotEqual(revised["draftId"], first["draftId"])
            self.assertEqual(revised["confirmationStatus"], "draft")
            self.assertEqual(revised["sql"], revised_sql)
            self.assertFalse(saved_dir.exists())
            with self.assertRaisesRegex(ValueError, "草稿已变化"):
                run_entity_table_action(self.request(action="confirm_ddl", draftId=first["draftId"], confirmed=True))
            with self.assertRaisesRegex(ValueError, "明确确认"):
                self.request(action="confirm_ddl", draftId=revised["draftId"])

            confirm = self.request(action="confirm_ddl", draftId=revised["draftId"], confirmed=True)
            expected = {**revised, "confirmationStatus": "confirmed"}
            self.assertEqual(run_entity_table_action(confirm)["ddlPreview"], expected)
            self.assertEqual(run_entity_table_action(confirm)["ddlPreview"], expected)
            self.assertEqual(run_entity_table_action(self.request())["ddlPreview"], expected)
            self.assertEqual(model.call_count, 3)
        record_path, = saved_dir.glob("*.json")
        self.assertEqual(json.loads(record_path.read_text(encoding="utf-8"))["sql"], revised_sql)

    def test_plan_change_invalidates_ddl_confirmation(self) -> None:
        """技术规划确认新版本后，先前生成的草稿不能成为正式 DDL。"""
        with patch("app.services.entity_ddl.generate_entity_ddl", return_value=_PRODUCT_SQL) as model:
            draft = run_entity_table_action(self.request())["ddlPreview"]
            self.plan["entities"][0]["description"] = "商品基础信息"
            self.write_plan()
            with self.assertRaisesRegex(ValueError, "草稿已变化"):
                run_entity_table_action(self.request(action="confirm_ddl", draftId=draft["draftId"], confirmed=True))
            current = run_entity_table_action(self.request())["ddlPreview"]
            self.assertNotEqual(current["draftId"], draft["draftId"])
            self.assertEqual(model.call_count, 2)
        self.assertFalse((self.path.parent / "entity-ddl").exists())

    def test_preview_regenerates_with_current_fields_and_escaped_table_name(self) -> None:
        """规划修改后重新调用模型，显式物理表名和字段可空性使用当前版本。"""
        revised_sql = _PRODUCT_SQL.replace("`product`", "`商品``资料`").replace(
            "`category_code` VARCHAR(64) NOT NULL", "`category_code` VARCHAR(64) NULL",
        )
        with patch("app.services.entity_ddl.generate_entity_ddl", side_effect=[_PRODUCT_SQL, revised_sql]) as model:
            run_entity_table_action(self.request())
            self.plan["entities"][0]["table_name"] = "商品`资料"
            self.plan["entities"][0]["fields"][1]["required"] = False
            self.write_plan()
            preview = run_entity_table_action(self.request())["ddlPreview"]
        self.assertEqual(model.call_count, 2)
        self.assertEqual(preview["tableName"], "商品`资料")
        self.assertEqual(preview["sql"], revised_sql)

    def test_rejects_invalid_model_sql_and_allows_retry(self) -> None:
        """拒绝不合规 SQL，后续请求可以重新生成，不缓存失败。"""
        examples = [
            _PRODUCT_SQL + " SELECT 1;",
            _PRODUCT_SQL.replace("CREATE TABLE `product`", "CREATE TABLE `other`"),
            _PRODUCT_SQL.replace("  `code`", "  `id` BIGINT NOT NULL COMMENT '系统主键',\n  `code`"),
            _PRODUCT_SQL.replace("`code` VARCHAR(64)", "`code` CHAR(64)"),
            _PRODUCT_SQL.replace("`code` VARCHAR(64)", "`code` VARCHAR(256)"),
            _PRODUCT_SQL.replace("`category_code` VARCHAR(64)", "`category_code` VARCHAR(256)"),
            _PRODUCT_SQL.replace("VARCHAR(512)", "TEXT"),
            _PRODUCT_SQL.replace("`status` VARCHAR(32)", "`status` ENUM('上架','下架')"),
            _PRODUCT_SQL.replace("`status` VARCHAR(32)", "`status` VARCHAR(1)"),
            _PRODUCT_SQL.replace("`stock` INT", "`stock` SMALLINT"),
            _PRODUCT_SQL.replace("`price` DECIMAL(12,2)", "`price` NUMERIC(12,2)"),
            _PRODUCT_SQL.replace("`featured` TINYINT(1)", "`featured` BOOLEAN"),
            _PRODUCT_SQL.replace("`release_date` TIMESTAMP", "`release_date` DATE"),
            _PRODUCT_SQL.replace("`created_at` TIMESTAMP", "`created_at` DATETIME"),
            _PRODUCT_SQL.replace("PRIMARY KEY (`code`)", "PRIMARY KEY (`code`, `category_code`)"),
            _PRODUCT_SQL.replace("  PRIMARY KEY (`code`),\n", ""),
            _PRODUCT_SQL.replace("KEY `idx_category_code`", "UNIQUE KEY `idx_category_code`"),
            _PRODUCT_SQL.replace("KEY `idx_category_code` (`category_code`)", "KEY `idx_status` (`status`)"),
            _PRODUCT_SQL.replace("KEY `idx_category_code` (`category_code`)",
                                 "FOREIGN KEY (`category_code`) REFERENCES `category` (`code`)"),
        ]
        with patch("app.services.entity_ddl.generate_entity_ddl", side_effect=[*examples, _PRODUCT_SQL]) as model:
            for sql in examples:
                with self.subTest(sql=sql), self.assertRaises(ValueError):
                    run_entity_table_action(self.request())
            self.assertEqual(run_entity_table_action(self.request())["ddlPreview"]["sql"], _PRODUCT_SQL)
        self.assertEqual(model.call_count, len(examples) + 1)

    def test_unmarked_entity_can_omit_primary_key(self) -> None:
        """未标注唯一标识时允许没有主键，且不能把普通字段作为主键。"""
        self.plan["entities"][0]["fields"][0]["label"] = "商品编码"
        self.write_plan()
        no_primary = _PRODUCT_SQL.replace("  PRIMARY KEY (`code`),\n", "")
        with patch("app.services.entity_ddl.generate_entity_ddl", side_effect=[_PRODUCT_SQL, no_primary]):
            with self.assertRaisesRegex(ValueError, "唯一标识依据"):
                run_entity_table_action(self.request())
            self.assertEqual(run_entity_table_action(self.request())["ddlPreview"]["sql"], no_primary)

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
        with patch("app.services.preview_runtime_guard.record_product_interaction") as interaction, \
                patch("app.services.entity_ddl.generate_entity_ddl", side_effect=[ValueError("模型不可用"), _PRODUCT_SQL]):
            failure = asyncio.run(collect())
            success = asyncio.run(collect())
            interaction.assert_not_called()
        for output in [success, failure]:
            for event in ["RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_END", "STATE_SNAPSHOT", "RUN_FINISHED", "entity-tables"]:
                self.assertIn(event, output)
        self.assertIn('"status":"completed"', success)
        self.assertIn('"ddlPreview"', success)
        self.assertIn('"status":"failed"', failure)
        self.assertIn("模型不可用", failure)
        self.assertIn("preview_ddl", entity_tables_capabilities()["actions"])
