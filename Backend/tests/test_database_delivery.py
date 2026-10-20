"""在临时工程核对版本 SQL 及交付边界，不连接 SaaS 或执行数据库语句。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.domain.entity_tables import EntityTableRequest
from app.graph.nodes.lifecycle import finalize_project
from app.services.database_delivery import export_database_scripts
from app.services.entity_tables import prepare_entity_table


class DatabaseDeliveryTests(unittest.TestCase):
    """覆盖首次交付、跨版升级、同版重新交付和失败时不覆盖文件。"""

    def setUp(self) -> None:
        """创建带当前注册绑定、版本和已确认技术规划的最小工程。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "backend").mkdir()
        (self.root / ".devagentstudio/plans").mkdir(parents=True)
        self.application = {
            "schemaVersion": 6, "configRevision": 1, "appName": "交付测试", "branchName": "v1_0",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 1},
        }
        self.plan = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "entities": [{
            "id": "Order", "name": "订单", "description": "订单'信息", "table_name": "12ab34cd56ef_order",
            "fields": [{"name": "amount", "label": "金额", "type": "decimal", "required": True},
                       {"name": "note", "label": "备注", "type": "text", "required": False}],
        }]}
        self.history = self.root / ".devagentstudio/database-deliveries.json"
        self.write_inputs()

    def write_inputs(self) -> None:
        """写入本次明确确认的配置和结构，不修改远端或任何真实工作区。"""

        (self.root / ".devagentstudio/application.json").write_text(json.dumps(self.application), encoding="utf-8")
        (self.root / ".devagentstudio/plans/technical-plan.json").write_text(json.dumps(self.plan), encoding="utf-8")

    def script(self, branch: str, name: str) -> Path:
        """定位当前测试工程的版本脚本。"""

        return self.root / "backend/db" / branch / name

    def next_version(self, branch: str = "v1_1") -> None:
        """从当前已交付工作区开始下一版，沿用原资源身份。"""

        self.application["branchName"] = branch
        self.application["configRevision"] += 1
        self.write_inputs()

    def test_first_delivery_uses_same_sql_as_entity_creation(self) -> None:
        """首版完整 SQL 与建表入口一致，增量为空，文件只在 backend/db 对外展示。"""

        paths = export_database_scripts(self.root)
        expected = prepare_entity_table(EntityTableRequest(
            action="prepare", workspaceRoot=str(self.root), entityId="Order"))
        self.assertEqual(paths, ["backend/db/v1_0/create.sql", "backend/db/v1_0/alter.sql"])
        self.assertEqual(self.script("v1_0", "create.sql").read_text(), expected["sql"] + "\n")
        self.assertEqual(self.script("v1_0", "alter.sql").read_bytes(), b"")
        snapshot = json.loads(self.history.read_text())["versions"][0]
        self.assertIsNone(snapshot["baseBranchName"])
        self.assertNotIn("datasourceId", snapshot)
        self.assertNotIn("password", snapshot)

    def test_next_delivery_covers_table_and_column_changes(self) -> None:
        """跨版输出增表、增列、删列、类型与可空修改和注释变化，保留上一版脚本。"""

        export_database_scripts(self.root)
        original = self.script("v1_0", "create.sql").read_bytes()
        entity = self.plan["entities"][0]
        entity["description"] = "订单新说明"
        entity["fields"] = [{"name": "amount", "label": "金额", "type": "number", "required": False},
                            {"name": "status", "label": "状态", "type": "enum", "required": True}]
        self.plan["entities"].append({"id": "OrderItem", "name": "明细", "description": "订单明细",
                                      "table_name": "12ab34cd56ef_order_item", "fields": []})
        self.next_version()
        export_database_scripts(self.root)
        alter = self.script("v1_1", "alter.sql").read_text()
        self.assertIn("CREATE TABLE `12ab34cd56ef_order_item`", alter)
        self.assertIn("DROP COLUMN `note`", alter)
        self.assertIn("MODIFY COLUMN `amount` BIGINT NULL", alter)
        self.assertIn("ADD COLUMN `status` VARCHAR(32) NOT NULL", alter)
        self.assertIn("COMMENT='订单新说明'", alter)
        self.assertNotIn("DROP TABLE `12ab34cd56ef_order`", alter)
        self.assertEqual(self.script("v1_0", "create.sql").read_bytes(), original)
        self.assertEqual(self.script("v1_1", "create.sql").read_text().count("CREATE TABLE"), 2)

    def test_same_version_regeneration_keeps_original_upgrade_baseline(self) -> None:
        """同版再次交付仍相对上一版生成累计增量，不能把刚生成的当前版当基线。"""

        export_database_scripts(self.root)
        self.plan["entities"][0]["fields"].append({"name": "status", "label": "状态", "type": "enum"})
        self.next_version()
        export_database_scripts(self.root)
        first = self.script("v1_1", "alter.sql").read_bytes()
        export_database_scripts(self.root)
        self.assertEqual(self.script("v1_1", "alter.sql").read_bytes(), first)
        self.plan["entities"][0]["fields"].append({"name": "reason", "label": "事由", "type": "long_text"})
        self.write_inputs()
        export_database_scripts(self.root)
        alter = self.script("v1_1", "alter.sql").read_text()
        self.assertIn("ADD COLUMN `status`", alter)
        self.assertIn("ADD COLUMN `reason`", alter)
        self.assertEqual(len(json.loads(self.history.read_text())["versions"]), 2)

    def test_unchanged_new_version_has_empty_alter(self) -> None:
        """仅业务代码改变的下一版仍有完整初始化脚本，增量保持空文件。"""

        export_database_scripts(self.root)
        self.next_version("release/v1_1")
        export_database_scripts(self.root)
        self.assertEqual(self.script("release/v1_1", "alter.sql").read_bytes(), b"")
        self.assertEqual(self.script("release/v1_1", "create.sql").read_bytes(),
                         self.script("v1_0", "create.sql").read_bytes())

    def test_deleted_entity_generates_explicit_drop(self) -> None:
        """实体删除生成带丢数提示的 DROP，当前完整结构中不再包含旧表。"""

        export_database_scripts(self.root)
        self.plan["entities"] = []
        self.next_version()
        export_database_scripts(self.root)
        self.assertEqual(self.script("v1_1", "create.sql").read_bytes(), b"")
        self.assertIn("DROP TABLE `12ab34cd56ef_order`;", self.script("v1_1", "alter.sql").read_text())

    def test_reordered_columns_generate_positions(self) -> None:
        """顺序变化保持列数据，通过 MODIFY 的 AFTER 表达当前完整设计。"""

        export_database_scripts(self.root)
        self.plan["entities"][0]["fields"].reverse()
        self.next_version()
        export_database_scripts(self.root)
        alter = self.script("v1_1", "alter.sql").read_text()
        self.assertIn("MODIFY COLUMN `note`", alter)
        self.assertIn("AFTER `id`", alter)
        self.assertNotIn("DROP COLUMN", alter)

    def test_no_guessing_or_overwriting_when_baseline_is_missing_or_changed(self) -> None:
        """丢失基线或用户手工编辑脚本都明确拒绝，不误当首版或覆盖修改。"""

        export_database_scripts(self.root)
        history = self.history.read_bytes()
        script = self.script("v1_0", "create.sql")
        original = script.read_bytes()
        script.write_text("-- 用户调整\n")
        with self.assertRaisesRegex(ValueError, "已被修改"):
            export_database_scripts(self.root)
        self.assertEqual(script.read_text(), "-- 用户调整\n")
        self.assertEqual(self.history.read_bytes(), history)
        script.write_bytes(original)
        self.history.unlink()
        with self.assertRaisesRegex(ValueError, "缺少结构基线"):
            export_database_scripts(self.root)
        self.assertEqual(script.read_bytes(), original)

    def test_invalid_snapshot_and_cross_resource_binding_are_rejected(self) -> None:
        """损坏快照和资源重绑不能成为自动迁移依据。"""

        export_database_scripts(self.root)
        original = self.history.read_bytes()
        self.history.write_text('{"schemaVersion":1,"versions":[{}]}')
        with self.assertRaises(ValueError):
            export_database_scripts(self.root)
        self.history.write_bytes(original)
        self.application["managedDatabase"]["datasourceKey"] = "98ab34cd56ef"
        self.plan["entities"][0]["table_name"] = "98ab34cd56ef_order"
        self.next_version()
        with self.assertRaisesRegex(ValueError, "跨资源"):
            export_database_scripts(self.root)
        self.assertFalse(self.script("v1_1", "create.sql").exists())

    def test_older_delivered_version_cannot_be_rewritten(self) -> None:
        """后续版本已存在时，不允许旧版变更使已有升级链失效。"""

        export_database_scripts(self.root)
        self.next_version()
        export_database_scripts(self.root)
        self.plan["entities"][0]["fields"].pop()
        self.next_version("v1_0")
        with self.assertRaisesRegex(ValueError, "旧版脚本"):
            export_database_scripts(self.root)

    def test_unconfirmed_plan_and_invalid_version_never_write_scripts(self) -> None:
        """技术规划未确认或版本越界时都在落盘前停止。"""

        self.plan["confirmation_status"] = "draft"
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "确认当前技术规划"):
            export_database_scripts(self.root)
        self.plan["confirmation_status"] = "confirmed"
        self.application["branchName"] = "../../outside"
        self.write_inputs()
        with self.assertRaises(ValueError):
            export_database_scripts(self.root)
        self.assertFalse((self.root / "backend/db").exists())

    def test_symlink_output_is_rejected(self) -> None:
        """即使目录仍在工作区内，也不通过软链改写其他目录中的用户文件。"""

        (self.root / "elsewhere").mkdir()
        (self.root / "backend/db").symlink_to(self.root / "elsewhere", target_is_directory=True)
        with self.assertRaisesRegex(ValueError, "软链接"):
            export_database_scripts(self.root)
        self.assertEqual(list((self.root / "elsewhere").iterdir()), [])

    def test_partial_write_failure_rolls_back_and_does_not_mark_complete(self) -> None:
        """第二份脚本写入失败时恢复已写文件，不留成功快照或完成状态。"""

        replace = os.replace
        failed = False

        def fail_once(source, target):
            """只让第一次提交 alter.sql 失败，允许回滚恢复此前文件。"""

            nonlocal failed
            if Path(target).name == "alter.sql" and not failed:
                failed = True
                raise OSError("模拟写入失败")
            return replace(source, target)

        with patch("app.services.database_delivery.os.replace", side_effect=fail_once):
            result = finalize_project({"workspace": str(self.root), "accepted": True})
        self.assertEqual(result["status"], "failed")
        self.assertIn("模拟写入失败", result["error"])
        self.assertFalse(self.script("v1_0", "create.sql").exists())
        self.assertFalse(self.history.exists())

    def test_finalize_requires_acceptance_and_projects_generated_paths(self) -> None:
        """既有 AG-UI 完成节点在验收前不产出，验收后返回两份文件的相对路径。"""

        state = {"workspace": str(self.root)}
        self.assertEqual(finalize_project(state)["status"], "failed")
        self.assertFalse(self.history.exists())
        result = finalize_project({**state, "accepted": True})
        self.assertEqual(result["status"], "completed")
        self.assertIn("backend/db/v1_0/create.sql", result["message"])
        self.assertIn("backend/db/v1_0/alter.sql", result["message"])

    def test_external_database_does_not_export_builtin_scripts(self) -> None:
        """外部数据库应用维持原交付行为，不强加内置前缀或生成目录。"""

        self.application["datasource"]["db"]["useBuiltin"] = False
        self.application.pop("managedDatabase")
        self.write_inputs()
        self.assertEqual(export_database_scripts(self.root), [])
        self.assertFalse(self.history.exists())
