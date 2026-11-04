"""权限版本交付集成回归：真实文件和投影，无 SaaS 及数据库副作用。"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from app.domain.database_delivery import AuthorizationDeliverySchema, DatabaseDeliveries
from app.config import Settings
from app.graph.nodes.lifecycle import finalize_project
from app.services.authorization_projection_sources import PO_FILES, XML_FILES, SOURCE_RECEIPT
from app.services.authorization_table_projection import fingerprint, table_mapping
from app.services.authorization_workspace_projector import RUNTIME, verify_projection
from app.services.database_delivery import export_database_scripts
from app.services.builtin_authorization_bootstrap import refresh_builtin_authorization, run_builtin_authorization_bootstrap
from app.services.managed_table_client import ManagedTableClient
from tests.authorization_delivery_fixture import install_authorization


class AuthorizationDeliveryTests(unittest.TestCase):
    """覆盖权限增量、只读核验和整个交付事务的失败边界。"""

    def setUp(self) -> None:
        """创建已确认业务实体，并禁止交付流程意外访问远端或运行初始化。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        (self.root / ".devagentstudio/plans").mkdir(parents=True)
        (self.root / "backend").mkdir()
        self.application = {
            "schemaVersion": 6, "configRevision": 1, "appName": "权限交付", "branchName": "v1",
            "auth": {"enable": False}, "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            "datasource": {"type": "database", "db": {"useBuiltin": True}},
            "managedDatabase": {"status": "REGISTERED", "datasourceKey": "12ab34cd56ef", "datasourceId": 1}}
        self.plan = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "entities": [
            {"id": "Order", "name": "订单", "description": "订单", "table_name": "12ab34cd56ef_order", "fields": []}]}
        self.history = self.root / ".devagentstudio/database-deliveries.json"
        self.guards = []
        for target in ("app.services.managed_table_client.ManagedTableClient.request",
                       "app.services.builtin_authorization_bootstrap.run_builtin_authorization_bootstrap",
                       "app.services.authorization_bootstrap.workspace_process_registry.run"):
            guard = patch(target, side_effect=AssertionError("交付不得访问数据库或执行初始化"))
            guard.start()
            self.guards.append(guard)
            self.addCleanup(guard.stop)
        self.write_inputs()

    def write_inputs(self, branch: str | None = None) -> None:
        """保存当前确认输入，并可切换到下一交付分支。"""

        if branch:
            self.application["branchName"] = branch
        self.application["authorization"]["initialAdministratorSubjects"] = (
            self.application["authorization"]["initialAdministratorSubjects"] or ["admin"]
            if self.application["authorization"]["enabled"] else [])
        if self.application["authorization"]["enabled"]:
            self.application["auth"] = {"enable": True, "yht": {
                name: "test-value" for name in ("clientId", "privateKey", "publicKey", "centerPublicKey")}}
            self.application["authorization"]["api"] = {"baseUrl": "", "viewId": ""}
        (self.root / ".devagentstudio/application.json").write_text(json.dumps(self.application), encoding="utf-8")
        (self.root / ".devagentstudio/plans/technical-plan.json").write_text(json.dumps(self.plan), encoding="utf-8")

    def enable(self) -> dict:
        """准备带精确来源与 Schema 成功事实的权限模板。"""

        compiled = install_authorization(self.root, self.application, self.plan)
        self.write_inputs()
        return compiled

    def script(self, branch: str, name: str = "create.sql") -> Path:
        """定位当前隔离版本文件。"""

        return self.root / "backend/db" / branch / name

    def test_first_delivery_and_permission_only_application_preserve_raw_sql(self) -> None:
        """首版合并完整权限语句，仅权限无业务实体时同样交付，增量始终为空。"""

        compiled = self.enable()
        before = {p: p.read_bytes() for p in self.root.rglob("*") if p.is_file()}
        export_database_scripts(self.root)
        sql = self.script("v1").read_text()
        self.assertEqual(sql.count("CREATE TABLE"), 5)
        self.assertTrue(sql.endswith("\n\n".join(compiled["sqls"])))
        self.assertEqual(self.script("v1", "alter.sql").read_bytes(), b"")
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)
        snapshot = DatabaseDeliveries.model_validate_json(self.history.read_bytes()).versions[0]
        self.assertEqual(snapshot.authorizationSchema.sqls, compiled["sqls"])
        self.assertNotIn("seedReady", snapshot.authorizationSchema.model_dump())
        self.plan["entities"] = []
        self.write_inputs()
        export_database_scripts(self.root)
        self.assertEqual(self.script("v1").read_text(), "\n\n".join(compiled["sqls"]))

    def test_enable_on_next_version_then_manifest_changes_have_no_schema_delta(self) -> None:
        """首次开启追加原文四表 CREATE；后续角色、资源变化不会产生 Schema 增量。"""

        export_database_scripts(self.root)
        original = self.script("v1").read_bytes()
        compiled = self.enable()
        self.write_inputs("v2")
        export_database_scripts(self.root)
        self.assertEqual(self.script("v2", "alter.sql").read_text(), "\n\n".join(compiled["sqls"]))
        self.assertEqual(self.script("v1").read_bytes(), original)
        self.plan["authorization_manifest"]["fingerprint"] = "sha256:new-resources"
        self.application["authorization"]["initialAdministratorSubjects"] = ["new-admin"]
        self.write_inputs("v3")
        export_database_scripts(self.root)
        self.assertEqual(self.script("v3", "alter.sql").read_bytes(), b"")

    def test_same_version_recomputes_permissions_but_old_versions_cannot_change(self) -> None:
        """同版从关闭到开启不能误复用业务快照，已有后续版本时拒绝改写。"""

        export_database_scripts(self.root)
        self.enable()
        export_database_scripts(self.root)
        self.assertEqual(self.script("v1").read_text().count("CREATE TABLE"), 5)
        self.assertEqual(self.script("v1", "alter.sql").read_bytes(), b"")
        self.write_inputs("v2")
        export_database_scripts(self.root)
        self.application["authorization"]["enabled"] = False
        self.write_inputs("v1")
        with self.assertRaisesRegex(ValueError, "旧版脚本"):
            export_database_scripts(self.root)

    def test_schema_change_or_disable_requires_explicit_migration(self) -> None:
        """本版或下一版都不能静默替换、删除已交付权限 Schema。"""

        self.enable()
        export_database_scripts(self.root)
        original = self.history.read_bytes()
        ddl = self.root / "backend/sql/authorization/schema.sql"
        original_ddl = ddl.read_text()
        for branch in ("v1", "v2"):
            for disable in (True, False):
                with self.subTest(branch=branch, disable=disable):
                    self.application["authorization"]["enabled"] = not disable
                    ddl.write_text(original_ddl if disable else original_ddl.replace("DEFAULT 0", "DEFAULT 1"))
                    self.write_inputs(branch)
                    with self.assertRaisesRegex(ValueError, "明确迁移"):
                        export_database_scripts(self.root)
                    self.assertEqual(self.history.read_bytes(), original)

    def test_missing_runtime_is_allowed_only_for_verified_delivered_schema(self) -> None:
        """首次必须有明确成功证据；历史快照与受管文件可以脱离 runtime 复验。"""

        self.enable()
        state = self.root / RUNTIME / "state.json"
        content = state.read_bytes()
        state.unlink()
        with self.assertRaisesRegex(ValueError, "待核验"):
            export_database_scripts(self.root)
        self.assertFalse(self.history.exists())
        state.write_bytes(content)
        export_database_scripts(self.root)
        state.unlink()
        (self.root / RUNTIME / "projection.json").unlink()
        self.write_inputs("v2")
        export_database_scripts(self.root)
        self.assertEqual(self.script("v2", "alter.sql").read_bytes(), b"")
        self.assertFalse(state.exists())

    def test_nonready_mismatched_or_corrupt_runtime_rejects_delivery(self) -> None:
        """失败、未知、执行中及绑定不匹配不能被交付流程伪装成已就绪。"""

        self.enable()
        state = self.root / RUNTIME / "state.json"
        ready = json.loads(state.read_text())
        for delta in ({"status": "failed"}, {"status": "running"}, {"status": "unknown"},
                      {"schemaReady": False}, {"datasourceId": 9}, {"datasourceKey": "98ab34cd56ef"},
                      {"ddlFingerprint": "sha256:wrong"}, {"verifiedAt": ""}):
            with self.subTest(delta=delta):
                state.write_text(json.dumps({**ready, **delta}))
                with self.assertRaises(ValueError):
                    export_database_scripts(self.root)
                self.assertFalse(self.history.exists())
        state.write_text("{broken")
        with self.assertRaises(ValueError):
            export_database_scripts(self.root)

    def test_invalid_ddl_or_collision_never_writes(self) -> None:
        """缺件、非法关系表主键与业务物理表冲突均在文件写入前拒绝。"""

        self.enable()
        ddl = self.root / "backend/sql/authorization/schema.sql"
        content = ddl.read_text()
        for text in ("", content.replace("AUTO_INCREMENT", "")):
            ddl.write_text(text)
            with self.assertRaises(ValueError):
                export_database_scripts(self.root)
        ddl.unlink()
        with self.assertRaises(ValueError):
            export_database_scripts(self.root)
        ddl.write_text(content)
        self.plan["entities"][0].update(id="Role", table_name="12ab34cd56ef_role")
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "冲突"):
            export_database_scripts(self.root)
        self.assertFalse((self.root / "backend/db").exists())

    def test_all_managed_references_and_sources_are_read_only_and_strict(self) -> None:
        """四个 PO 和三份多语句 XML 都核验；错误、重复或原始前缀及未知修改拒绝。"""

        self.enable()
        verify_projection(self.root, "12ab34cd56ef")
        for relative in (*PO_FILES, *XML_FILES):
            path = self.root / relative
            original = path.read_text()
            for prefix in ("", "98ab34cd56ef_", "12ab34cd56ef_12ab34cd56ef_"):
                path.write_text(original.replace("12ab34cd56ef_", prefix))
                with self.assertRaises(ValueError):
                    export_database_scripts(self.root)
            path.write_text(original + "\n// unknown change")
            with self.assertRaises(ValueError):
                export_database_scripts(self.root)
            path.unlink()
            with self.assertRaises(ValueError):
                export_database_scripts(self.root)
            path.write_text(original)
        (self.root / SOURCE_RECEIPT).unlink()
        with self.assertRaisesRegex(ValueError, "来源摘要"):
            export_database_scripts(self.root)
        self.assertFalse(self.history.exists())

    def test_snapshot_validation_and_history_without_authorization_field(self) -> None:
        """未记录权限字段的业务快照仍可验证；越界 SQL、重复表、摘要损坏都拒绝。"""

        export_database_scripts(self.root)
        history = json.loads(self.history.read_text())
        history["versions"][0].pop("authorizationSchema")
        self.history.write_text(json.dumps(history))
        export_database_scripts(self.root)
        compiled = self.enable()
        valid = {"tableNames": compiled["tables"], "sqls": compiled["sqls"], "ddlFingerprint": compiled["ddlFingerprint"]}
        for change in ({"tableNames": compiled["tables"][:3]}, {"tableNames": [compiled["tables"][0]] * 4},
                       {"ddlFingerprint": "sha256:" + "0" * 64},
                       {"sqls": ["DROP TABLE role;", *compiled["sqls"][1:]]},
                       {"sqls": [compiled["sqls"][0] + " DROP TABLE role;", *compiled["sqls"][1:]]}):
            candidate = {**valid, **change}
            if "sqls" in change:
                candidate["ddlFingerprint"] = fingerprint(candidate["sqls"])
            with self.assertRaises(ValueError):
                AuthorizationDeliverySchema.model_validate(candidate)
        self.write_inputs("v2")
        export_database_scripts(self.root)
        stored = json.loads(self.history.read_text())
        stored["versions"][-1]["datasourceKey"] = "98ab34cd56ef"
        with self.assertRaises(ValueError):
            DatabaseDeliveries.model_validate(stored)

    def test_tampering_and_atomic_failure_preserve_existing_delivery(self) -> None:
        """权限交付也保留摘要保护及写入失败回滚，不登记完成。"""

        self.enable()
        export_database_scripts(self.root)
        self.write_inputs("v2")
        export_database_scripts(self.root)
        before = {path: path.read_bytes() for path in (self.script("v2"), self.script("v2", "alter.sql"), self.history)}
        self.script("v2").write_text("-- user edit")
        with self.assertRaisesRegex(ValueError, "已被修改"):
            export_database_scripts(self.root)
        self.script("v2").write_bytes(before[self.script("v2")])
        self.plan["entities"][0]["fields"].append({"name": "note", "type": "text"})
        self.write_inputs()
        replace = os.replace
        failed = False

        def fail_once(source, target):
            """只在第二个交付文件提交失败，允许原事务回滚。"""

            nonlocal failed
            if Path(target).name == "alter.sql" and not failed:
                failed = True
                raise OSError("权限交付模拟失败")
            return replace(source, target)

        with patch("app.services.database_delivery.os.replace", side_effect=fail_once):
            result = finalize_project({"workspace": str(self.root), "accepted": True})
        self.assertEqual(result["status"], "failed")
        self.assertIn("权限交付模拟失败", result["error"])
        for path, content in before.items():
            self.assertEqual(path.read_bytes(), content)

    def test_configuration_mismatch_and_other_datasources_do_not_initialize(self) -> None:
        """权限开启要求确认规划；其他数据源保持既有跳过行为。"""

        self.application["authorization"]["enabled"] = True
        self.write_inputs()
        with self.assertRaisesRegex(ValueError, "技术规划不一致"):
            export_database_scripts(self.root)
        for source in ({"type": "database", "db": {"useBuiltin": False, "mode": "direct"}},
                       {"type": "database", "db": {"useBuiltin": False, "mode": "dbid"}},
                       {"type": "static"}, {"type": "external_api"}):
            self.application["datasource"] = source
            self.write_inputs()
            self.assertEqual(export_database_scripts(self.root), [])
        self.assertFalse(self.history.exists())

    def test_saas_batch_and_delivered_sql_are_identical_without_export_side_effects(self) -> None:
        """捕获真实 Bootstrap 客户端的批量载荷，刷新成功后逐字节比较交付 SQL。"""

        compiled = self.enable()
        for guard in self.guards:
            guard.stop()
        (self.root / RUNTIME / "state.json").unlink()
        calls, batches = [], []
        names = list(table_mapping("12ab34cd56ef").values())
        ready = False

        def respond(request: httpx.Request) -> httpx.Response:
            """只在内存中模拟受理及四表成功事实，不执行任何 SQL。"""

            nonlocal ready
            calls.append(request)
            path = request.url.path
            if path.endswith("/tables/check"):
                return httpx.Response(200, json={"datasourceId": 1, "ready": ready, "tables": [
                    {"tableName": name, "status": "SYNCED" if ready else "NOT_CREATED", "differences": []}
                    for name in names]})
            if path.endswith("/batch"):
                batches.append(json.loads(request.content)["sqls"])
                return httpx.Response(202, json={"operationId": "123", "status": "RUNNING"})
            if path.endswith("/123"):
                ready = True
                return httpx.Response(200, json={"operationId": "123", "status": "SUCCEEDED", "mode": "CREATE_BATCH",
                                               "tableNames": names, "executionResult": {"steps": []}})
            if path.endswith("/table-operations"):
                return httpx.Response(200, json=[])
            raise AssertionError(request.url)

        client = ManagedTableClient(Settings(model_base_url="", model_api_key="", model_name="",
            managed_database_base_url="https://saas.example", managed_database_service_key="test"),
            transport=httpx.MockTransport(respond))
        run_builtin_authorization_bootstrap(str(self.root), self.plan, client=client)
        result = refresh_builtin_authorization(str(self.root), "123", client=client)
        self.assertTrue(result["schema_ready"])
        self.assertFalse(result["seed_ready"])
        self.assertEqual(batches, [compiled["sqls"]])
        count = len(calls)
        export_database_scripts(self.root)
        self.assertEqual(len(calls), count)
        schema = DatabaseDeliveries.model_validate_json(self.history.read_bytes()).versions[0].authorizationSchema
        self.assertEqual(schema.sqls, batches[0])
        self.assertTrue(self.script("v1").read_text().endswith("\n\n".join(batches[0])))
