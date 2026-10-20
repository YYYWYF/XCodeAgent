"""覆盖内置规则与外置数据库的隔离边界，不访问真实数据库或 SaaS。"""

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

from app.agents.main.planner import _technical_planning_prompt
from app.graph.nodes.planning import _project_plan_validation_errors
from app.services import data_sources
from app.services.data_source_policy import uses_builtin_database
from app.services.database_requirement_schema import derive_required_database_schema
from app.services.entity_definitions import database_operation_field_errors, entity_detail_table_name, normalize_entities
from app.services.entity_design import entity_design_validation_errors
from app.services.entity_detail_plan import create_entity_detail_plan
from app.services.managed_database_metadata import validate_managed_snapshots
from app.services.preview_runtime_repair import prepare_repair
from app.services.preview_runtime_state import begin_attempt, finish_attempt, runtime_snapshot


class DatabaseModeIsolationTests(unittest.TestCase):
    """同时验证外置路径放行、内置约束保留和并发资源互不阻塞。"""

    def setUp(self) -> None:
        """创建只含当前应用配置的独立临时工作区。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".devagentstudio").mkdir()
        self.application = {"datasource": {"type": "database", "db": {"useBuiltin": False}},
                            "managedDatabase": {"datasourceKey": "12ab34cd56ef"}}
        self.write_application()

    def write_application(self) -> None:
        """保存唯一权威应用配置，不向规划产物复制模式。"""

        (self.root / ".devagentstudio/application.json").write_text(json.dumps(self.application), encoding="utf-8")

    def entity(self) -> dict:
        """提供已有大写表名、英文文案与合法字段的外置库实体。"""

        return {"id": "Order", "name": "Order", "description": "Customer orders", "table_name": "CRM_ORDER",
                "fields": [{"name": "amount", "type": "decimal", "required": True,
                            "label": "Amount", "description": "Order amount"}]}

    def test_table_name_and_stale_registration_do_not_enable_builtin_policy(self) -> None:
        """显式外置模式不受旧注册信息或 table_name 字段存在的影响。"""

        self.assertFalse(uses_builtin_database(self.application))
        plan = {"entities": [self.entity()]}
        detail = create_entity_detail_plan(plan, plan["entities"][0])
        detail["database_design"] = {"matched_table": "CRM_ARCHIVE", "bindings": [
            {"entity_field": "amount", "table": "CRM_ARCHIVE", "table_column": "amount"}],
            "database_operations": [{"operation": "add_column", "table": "CRM_ARCHIVE", "column": "amount"}]}
        self.assertEqual(entity_design_validation_errors(plan, detail, builtin=False), [])
        self.assertTrue(entity_design_validation_errors(plan, detail, builtin=True))
        origin = {"matched_table": "CRM_ARCHIVE", "database_operations": detail["database_design"]["database_operations"]}
        self.assertEqual(database_operation_field_errors(plan["entities"], origin), [])
        self.assertTrue(database_operation_field_errors(plan["entities"], origin, builtin=True))

    def test_external_identifiers_survive_normalization_and_schema_derivation(self) -> None:
        """已有外置表名保持大小写和合法引用，不再套用内置小写前缀规则。"""

        entities = normalize_entities([self.entity()], with_types=True)
        self.assertEqual(entities[0]["table_name"], "CRM_ORDER")
        detail = create_entity_detail_plan({"entities": entities}, entities[0])
        detail.pop("table_name")
        self.assertEqual(entity_detail_table_name(detail), "CRM_ORDER")
        schema = derive_required_database_schema(
            [{"entity_designs": [detail], "data_source_id": "database"}],
            [{"id": "database", "type": "database", "entities": entities}], {"Order"})
        self.assertEqual(schema["tables"][0]["name"], "CRM_ORDER")
        self.assertEqual(entity_detail_table_name({"table_design": {"name": "客户`订单"}}), "客户`订单")

    def test_prompt_and_planning_gate_apply_chinese_rule_only_to_builtin(self) -> None:
        """生成提示词和确认门禁都读取应用配置，外置英文实体保持原行为。"""

        plan = {"artifact_type": "technical-plan", "entities": [self.entity()]}
        state = {"workspace": str(self.root)}
        clause = "Entity name and description, and every field label and description, must be non-empty Chinese text"
        with patch("app.graph.nodes.planning.validate_project_plan_dependencies", return_value=[]), \
             patch("app.graph.nodes.planning.validate_api_contract_consistency", return_value=[]), \
             patch("app.graph.nodes.planning.validate_project_plan_datasource_policy", return_value=[]):
            self.assertEqual(_project_plan_validation_errors(plan, state), [])
            self.assertNotIn(clause, _technical_planning_prompt({"application_config": self.application}, None))
            self.application["datasource"]["db"]["useBuiltin"] = True
            self.write_application()
            self.assertEqual(len(_project_plan_validation_errors(plan, state)), 4)
            self.assertIn(clause, _technical_planning_prompt({"application_config": self.application}, None))

    def test_external_frozen_snapshot_does_not_read_live_catalog_or_saas(self) -> None:
        """已确认外置映射即使目录已删，也不新增内置资源门禁依赖。"""

        design = {"sourceSnapshots": [{"sourceType": "database", "sourceId": "deleted-external-source",
                                       "details": {"schema": "crm", "table": "CRM_ORDER", "columns": []}}]}
        with patch("app.services.data_sources.database_source_mode") as mode, \
             patch("app.services.managed_database_metadata.read_managed_columns") as remote:
            validate_managed_snapshots(self.root, design)
        mode.assert_not_called()
        remote.assert_not_called()

    def test_builtin_snapshot_cannot_be_reused_after_switching_to_external(self) -> None:
        """已冻结内置身份的映射不能因应用切换外置模式而绕过失效检查。"""

        design = {"sourceSnapshots": [{"sourceType": "database", "sourceId": "application-database",
                                       "details": {"managedDatasourceKey": "12ab34cd56ef"}}]}
        with patch("app.services.data_sources.database_source_mode", return_value="direct"):
            with self.assertRaisesRegex(ValueError, "来源已变化"):
                validate_managed_snapshots(self.root, design)

    def test_external_database_config_failure_keeps_diagnosis_available(self) -> None:
        """外置配置错误沿用原诊断入口，不显示内置资源维护提示。"""

        begin_attempt(self.root)
        finish_attempt(self.root, {"status": "failed", "failed_stage": "backend_database_config",
                                   "backend": {"status": "failed", "message": "连接配置无效"}})
        self.assertTrue(runtime_snapshot(self.root, logs=False)["repairAvailable"])
        with patch("app.services.preview_runtime_repair.read_logs", return_value={}), \
             patch("app.agents.repair_planner.plan_build_failure_repair_with_repair_planner_agent",
                   return_value={"decision": "user_action", "reason": "请检查外置连接配置"}) as model:
            result = prepare_repair(str(self.root), "thread", {})
        model.assert_called_once()
        self.assertEqual(result["message"], "请检查外置连接配置")

    def test_slow_saas_request_does_not_hold_global_catalog_lock(self) -> None:
        """内置 SaaS 请求未完成时，另一应用仍能读取外置表清单。"""

        entered, release = Event(), Event()
        builtin = {"id": "application-database", "type": "database", "mode": "builtin"}
        external = {"id": "crm", "type": "database", "mode": "direct", "schema": "crm",
                    "managedTables": [{"table": "CRM_ORDER", "description": "订单"}]}

        def catalog(workspace, **_kwargs):
            """模拟两个工作区独立的本地目录快照。"""

            return [builtin if workspace == "builtin-workspace" else external]

        def remote(*_args, **_kwargs):
            """保持远端请求未完成，允许主线程验证外置操作已返回。"""

            entered.set()
            if not release.wait(5):
                raise TimeoutError("测试远端请求未被释放")
            return {"tables": []}

        with patch("app.services.data_sources._read_catalog", side_effect=catalog), \
             patch("app.services.managed_database_metadata.read_managed_tables", side_effect=remote), \
             ThreadPoolExecutor(max_workers=2) as pool:
            pending = pool.submit(data_sources.selected_tables, "builtin-workspace")
            try:
                self.assertTrue(entered.wait(2))
                result = pool.submit(data_sources.selected_tables, "external-workspace").result(timeout=2)
                self.assertEqual(result[0]["table"], "CRM_ORDER")
            finally:
                release.set()
            self.assertEqual(pending.result(timeout=2), [])

    def test_builtin_catalog_change_during_request_discards_stale_result(self) -> None:
        """缩短锁范围后仍复核目录身份，不能返回期间被替换的内置来源。"""

        source = {"id": "application-database", "type": "database", "mode": "builtin"}
        changed = {**source, "mode": "direct"}
        with patch("app.services.data_sources._read_catalog", side_effect=[[source], [source], [changed]]), \
             patch("app.services.managed_database_metadata.read_managed_tables", return_value={"tables": []}):
            with self.assertRaisesRegex(data_sources.DataSourceError, "配置已变化"):
                data_sources.selected_tables(self.root)
