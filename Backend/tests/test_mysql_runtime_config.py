"""覆盖 Agent 无凭据配置合同与启动器实时读取目录的协作边界。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.data_source.database_context import task_database_source_ids
from app.persistence.data_sources import write_sources
from app.services.backend_launch_support import _backend_runtime_environment
from app.tools.mysql_config import create_get_mysql_config_tool


class MySQLRuntimeConfigTests(unittest.TestCase):
    """使用真实本地配置与目录，不访问数据库、模型或 SaaS。"""

    def setUp(self) -> None:
        """创建未选择模拟数据库的应用工作区。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / ".devagentstudio").mkdir()
        self.write_application(False)

    def write_application(self, builtin: bool) -> None:
        """保存唯一权威的模拟数据库开关。"""

        (self.root / ".devagentstudio/application.json").write_text(json.dumps({
            "datasource": {"type": "database", **({"db": {"useBuiltin": True}} if builtin else {})},
        }), encoding="utf-8")

    def source(self, source_id: str = "orders-db", host: str = "orders.local") -> dict:
        """构造当前目录的 DBID 数据源，不复制到 application.json。"""

        return {"id": source_id, "name": "订单数据库", "type": "database", "mode": "dbid",
                "domain": host, "port": 3306, "schema": "orders", "userName": "orders_user",
                "dbid": "runtime-secret", "managedTables": []}

    def invoke(self, source_ids=None) -> dict:
        """调用与生产 Agent 同名同签名的零参数工具。"""

        return json.loads(create_get_mysql_config_tool(str(self.root), source_ids=source_ids).invoke({}))

    def test_builtin_returns_injection_policy_without_saas_or_credentials(self) -> None:
        """模拟模式生成时不取密或预检实体，仅交付可解析的运行时配置说明。"""

        self.write_application(True)
        write_sources(self.root, [{"id": "application-database", "name": "模拟数据库",
                                   "type": "database", "mode": "builtin", "managedTables": []}])
        with patch("app.services.managed_database_runtime.resolve_managed_runtime") as resolve:
            result = self.invoke(("application-database",))
        self.assertEqual((result["status"], result["mode"]), ("ok", "builtin"))
        self.assertEqual(result["springDatasource"]["url"], "${SPRING_DATASOURCE_URL}")
        self.assertNotIn("password", result)
        resolve.assert_not_called()

    def test_unused_unconfigured_and_deleted_sources_have_distinct_results(self) -> None:
        """无需数据库不能触发配置要求，已删除引用不能静默改用后来新增的数据库。"""

        self.assertEqual(self.invoke(())["status"], "not_required")
        self.assertEqual(self.invoke()["status"], "not_configured")
        tool = create_get_mysql_config_tool(str(self.root), source_ids=("orders-db",))
        write_sources(self.root, [self.source("replacement-db")])
        self.assertEqual(json.loads(tool.invoke({}))["status"], "error")

    def test_added_and_updated_database_is_used_at_each_launch(self) -> None:
        """创建时无连接、应用内新增和修改后，下一次启动读取最新连接。"""

        environment, error = _backend_runtime_environment(self.root)
        self.assertIsNone(error)
        self.assertNotIn("MYSQL_HOST", environment)
        for host in ("orders.local", "updated.local"):
            write_sources(self.root, [self.source(host=host)])
            with patch("app.services.database_credentials.resolve_dbid_password", return_value="resolved-secret") as resolve:
                result = self.invoke(("orders-db",))
                resolve.assert_not_called()
                environment, error = _backend_runtime_environment(self.root)
                resolve.assert_called_once_with("runtime-secret")
            self.assertEqual(result["status"], "ok")
            self.assertNotIn("runtime-secret", json.dumps(result))
            self.assertIsNone(error)
            self.assertEqual(environment["MYSQL_HOST"], host)
            self.assertEqual(environment["SPRING_DATASOURCE_PASSWORD"], "resolved-secret")

    def test_direct_password_is_resolved_only_at_launch(self) -> None:
        """直连密文仅交给后端解析器，模型工具不解密也不返回密文。"""

        source = self.source()
        source.pop("dbid")
        source.update(mode="direct", passwordCiphertext="encrypted-value")
        write_sources(self.root, [source])
        with patch("app.services.data_sources.is_encrypted_password", return_value=True), \
             patch("app.services.database_credentials.decrypt_application_password", return_value="direct-secret") as decrypt:
            result = self.invoke(("orders-db",))
            decrypt.assert_not_called()
            environment, error = _backend_runtime_environment(self.root)
        self.assertEqual(result["mode"], "direct")
        self.assertNotIn("encrypted-value", json.dumps(result))
        self.assertIsNone(error)
        self.assertEqual(environment["MYSQL_PWD"], "direct-secret")

    def test_task_binding_uses_confirmed_source_identity(self) -> None:
        """任务只从已确认映射提取身份，无数据库任务不绑定，跨库引用明确拒绝。"""

        snapshots = [{"sourceType": "database", "sourceId": "orders-db"}]
        tasks = [{"source_refs": {"endpoint_designs": [{"sourceSnapshots": snapshots}]}}]
        self.assertEqual(task_database_source_ids(tasks), ("orders-db",))
        self.assertEqual(task_database_source_ids([]), ())
        snapshots.append({"sourceType": "database", "sourceId": "other-db"})
        with self.assertRaisesRegex(ValueError, "多个数据库"):
            task_database_source_ids(tasks)
