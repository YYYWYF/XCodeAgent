from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from app.services.database_execution import (
    _mysql_connection_config,
    create_database_execution_context,
    execute_database_plan,
)
from app.tools.mysql_info import (
    create_execute_mysql_ddl_tool,
    create_get_mysql_config_tool,
    execute_mysql_ddl,
    get_mysql_config,
)


def _write_application(
    workspace: Path,
    *,
    host: str,
    port: int,
    user: str,
    password: str,
    database: str,
) -> None:
    """写入测试所需的应用级数据库连接配置。"""

    application_file = workspace / ".xcodeagent" / "application.json"
    application_file.parent.mkdir(parents=True)
    application_file.write_text(
        json.dumps(
            {
                "datasource": {
                    "type": "DataBase",
                    "db": {
                        "useBuiltin": False,
                        "plantMode": {
                            "domain": host,
                            "port": port,
                            "userName": user,
                            "pwd": password,
                            "schema": database,
                        },
                    },
                }
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class _FakeCursor:
    """记录 DDL 执行语句的最小 DB-API 游标替身。"""

    def __init__(self) -> None:
        self.executed: list[str] = []
        self.rowcount = 0

    def __enter__(self) -> _FakeCursor:
        """进入游标上下文。"""

        return self

    def __exit__(self, *_args: object) -> None:
        """退出游标上下文。"""

    def execute(self, statement: str) -> None:
        """记录一次 SQL 执行。"""

        self.executed.append(statement)


class _FakeConnection:
    """提供数据库执行测试所需的最小连接上下文。"""

    def __init__(self, cursor: _FakeCursor) -> None:
        self._cursor = cursor
        self.committed = False

    def __enter__(self) -> _FakeConnection:
        """进入连接上下文。"""

        return self

    def __exit__(self, *_args: object) -> None:
        """退出连接上下文。"""

    def cursor(self) -> _FakeCursor:
        """返回记录 SQL 的测试游标。"""

        return self._cursor

    def commit(self) -> None:
        """记录事务提交。"""

        self.committed = True


def _fake_pymysql(connect: Mock) -> SimpleNamespace:
    """构造不触发真实网络连接的 pymysql 模块替身。"""

    return SimpleNamespace(
        connect=connect,
        cursors=SimpleNamespace(DictCursor=object),
        MySQLError=Exception,
    )


class DatabaseExecutionTests(unittest.TestCase):
    """验证 DDL 执行只使用当前应用工作区的数据库配置。"""

    def test_connection_config_resolves_distinct_workspaces_without_environment(self) -> None:
        """不同工作区必须得到各自配置，不能被全局 MYSQL_* 覆盖。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            root = Path(temporary_root)
            workspace_a = root / "app-a"
            workspace_b = root / "app-b"
            _write_application(
                workspace_a,
                host="app-a.mysql.local",
                port=3307,
                user="app_a_user",
                password="app-a-password",
                database="app_a_schema",
            )
            _write_application(
                workspace_b,
                host="app-b.mysql.local",
                port=3308,
                user="app_b_user",
                password="app-b-password",
                database="app_b_schema",
            )
            with patch.dict(
                os.environ,
                {
                    "MYSQL_HOST": "global.mysql.local",
                    "MYSQL_PORT": "3306",
                    "MYSQL_USER": "global_user",
                    "MYSQL_PWD": "global-password",
                    "MYSQL_DATABASE": "global_schema",
                },
            ):
                config_a = _mysql_connection_config(workspace_a)
                config_b = _mysql_connection_config(workspace_b)

        self.assertEqual(config_a["host"], "app-a.mysql.local")
        self.assertEqual(config_a["database"], "app_a_schema")
        self.assertEqual(config_b["host"], "app-b.mysql.local")
        self.assertEqual(config_b["database"], "app_b_schema")

    def test_execute_database_plan_uses_workspace_credentials(self) -> None:
        """实际 DDL 连接参数必须来自当前工作区，而不是 .env。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root) / "app"
            _write_application(
                workspace,
                host="application.mysql.local",
                port=3310,
                user="application_user",
                password="application-password",
                database="application_schema",
            )
            cursor = _FakeCursor()
            connection = _FakeConnection(cursor)
            connect = Mock(return_value=connection)
            fake_pymysql = _fake_pymysql(connect)
            execution_context = create_database_execution_context(
                {
                    "database": "application_schema",
                    "database_exists": True,
                    "tables": [],
                    "summary": "已连接。",
                }
            )
            with patch.dict(
                os.environ,
                {
                    "MYSQL_HOST": "global.mysql.local",
                    "MYSQL_PORT": "3306",
                    "MYSQL_USER": "global_user",
                    "MYSQL_PWD": "global-password",
                    "MYSQL_DATABASE": "global_schema",
                },
            ), patch.dict(sys.modules, {"pymysql": fake_pymysql}):
                result = execute_database_plan(
                    plan={"statements": ["CREATE TABLE orders (id BIGINT)"]},
                    execution_context=execution_context,
                    workspace_root=workspace,
                )

        self.assertEqual(result["status"], "completed")
        self.assertTrue(connection.committed)
        self.assertEqual(cursor.executed, ["USE `application_schema`", "CREATE TABLE orders (id BIGINT)"])
        connection_kwargs = connect.call_args.kwargs
        self.assertEqual(connection_kwargs["host"], "application.mysql.local")
        self.assertEqual(connection_kwargs["port"], 3310)
        self.assertEqual(connection_kwargs["user"], "application_user")
        self.assertEqual(connection_kwargs["password"], "application-password")

    def test_unbound_legacy_ddl_tool_fails_closed(self) -> None:
        """旧的无工作区 DDL 入口必须拒绝执行，即使环境变量存在。"""

        with patch.dict(
            os.environ,
            {
                "MYSQL_HOST": "global.mysql.local",
                "MYSQL_PORT": "3306",
                "MYSQL_USER": "global_user",
                "MYSQL_PWD": "global-password",
                "MYSQL_DATABASE": "global_schema",
            },
        ):
            result = json.loads(execute_mysql_ddl.invoke({"statements": ["DROP TABLE orders"]}))

        self.assertEqual(result["status"], "error")
        self.assertIn("缺少当前应用工作区", result["error"])

    def test_bound_mysql_config_tool_uses_application_credentials(self) -> None:
        """生成工具必须返回当前应用配置，而不是全局环境变量。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root) / "app"
            _write_application(
                workspace,
                host="generation.mysql.local",
                port=3312,
                user="generation_user",
                password="generation-password",
                database="generation_schema",
            )
            with patch.dict(
                os.environ,
                {
                    "MYSQL_HOST": "global.mysql.local",
                    "MYSQL_PORT": "3306",
                    "MYSQL_USER": "global_user",
                    "MYSQL_PWD": "global-password",
                    "MYSQL_DATABASE": "global_schema",
                },
            ):
                result = json.loads(create_get_mysql_config_tool(workspace).invoke({}))

        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["host"], "generation.mysql.local")
        self.assertEqual(result["port"], 3312)
        self.assertEqual(result["user"], "generation_user")
        self.assertEqual(result["password"], "generation-password")
        self.assertEqual(result["database"], "generation_schema")
        self.assertIn("generation.mysql.local:3312/generation_schema", result["jdbc_url"])

    def test_unbound_mysql_config_tool_fails_closed(self) -> None:
        """旧的无工作区配置工具必须拒绝读取全局环境变量。"""

        with patch.dict(
            os.environ,
            {
                "MYSQL_HOST": "global.mysql.local",
                "MYSQL_PORT": "3306",
                "MYSQL_USER": "global_user",
                "MYSQL_PWD": "global-password",
                "MYSQL_DATABASE": "global_schema",
            },
        ):
            result = json.loads(get_mysql_config.invoke({}))

        self.assertEqual(result["status"], "error")
        self.assertIn("缺少当前应用工作区", result["error"])

    def test_bound_legacy_ddl_tool_uses_application_credentials(self) -> None:
        """绑定工作区的旧 DDL 工具也必须使用应用级连接配置。"""

        with tempfile.TemporaryDirectory() as temporary_root:
            workspace = Path(temporary_root) / "app"
            _write_application(
                workspace,
                host="bound.mysql.local",
                port=3311,
                user="bound_user",
                password="bound-password",
                database="bound_schema",
            )
            cursor = _FakeCursor()
            connection = _FakeConnection(cursor)
            connect = Mock(return_value=connection)
            fake_pymysql = _fake_pymysql(connect)
            with patch.dict(sys.modules, {"pymysql": fake_pymysql}):
                result = json.loads(
                    create_execute_mysql_ddl_tool(workspace).invoke(
                        {"statements": ["ALTER TABLE orders ADD COLUMN total DECIMAL(10, 2)"]}
                    )
                )

        self.assertEqual(result["status"], "completed")
        self.assertEqual(cursor.executed[0], "USE `bound_schema`")
        connection_kwargs = connect.call_args.kwargs
        self.assertEqual(connection_kwargs["host"], "bound.mysql.local")
        self.assertEqual(connection_kwargs["port"], 3311)
        self.assertEqual(connection_kwargs["user"], "bound_user")
        self.assertEqual(connection_kwargs["password"], "bound-password")


if __name__ == "__main__":
    unittest.main()
