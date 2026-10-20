"""统一操作记录合同的范围、数据脱敏和异常输入测试。"""

import unittest

from app.domain.managed_database_operations import parse_database_operations


class ManagedDatabaseOperationTests(unittest.TestCase):
    """只验证本地只读响应，不访问任何数据库或 SaaS。"""

    def entry(self, **changes) -> dict:
        """提供当前 SaaS 的完整记录，可指定不同资源操作与范围。"""

        return {"operationId": "123", "mode": "CREATE", "tableName": "12ab34cd56ef_order",
                "tableNames": ["12ab34cd56ef_order"], "status": "SUCCEEDED",
                "createdAt": "2026-10-15T02:00:00Z", "executionResult": {
                    "errorCode": None, "errorSummary": None, "recoveryState": "NOT_REQUESTED",
                    "steps": [{"sql": "private-sql"}], "manualResolution": {"databaseActor": "private-user"}},
                **changes}

    def test_release_matches_membership_without_single_table(self) -> None:
        """资源回收允许空 tableName，仅精确清单成员与目标实体匹配。"""

        operation = parse_database_operations([self.entry(mode="RELEASE", tableName="")])[0]
        self.assertTrue(operation.targets_table("12ab34cd56ef_order"))
        self.assertFalse(operation.targets_table("12ab34cd56ef_order_item"))
        self.assertNotIn("private-sql", str(operation.model_dump()))
        self.assertNotIn("private-user", str(operation.model_dump()))

    def test_all_current_statuses_and_modes_are_readable(self) -> None:
        """四种模式和四种状态均能独立读取，不将成功记录当作结构通过。"""

        for mode in ["CREATE", "REBUILD", "DROP", "RELEASE"]:
            for status in ["RUNNING", "SUCCEEDED", "FAILED", "UNKNOWN"]:
                with self.subTest(mode=mode, status=status):
                    operation = parse_database_operations([self.entry(mode=mode, status=status)])[0]
                    self.assertEqual((operation.mode, operation.status), (mode, status))

    def test_malformed_or_non_current_records_remain_errors(self) -> None:
        """不放宽未知模式、缺失字段及错误类型，也不静默兼容旧响应。"""

        for changes in [{"mode": "ALTER"}, {"mode": "DROP", "tableName": ""},
                        {"tableNames": "12ab34cd56ef_order"}, {"operationId": 123}]:
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, "当前合同"):
                parse_database_operations([self.entry(**changes)])
        missing = self.entry()
        missing.pop("tableNames")
        with self.assertRaisesRegex(ValueError, "当前合同"):
            parse_database_operations([missing])

    def test_duplicate_and_over_limit_history_remain_errors(self) -> None:
        """统一解析保留历史上限和重复编号拦截，两个入口使用相同规则。"""

        with self.assertRaisesRegex(ValueError, "编号重复"):
            parse_database_operations([self.entry(), self.entry()])
        with self.assertRaisesRegex(ValueError, "超出支持范围"):
            parse_database_operations([self.entry(operationId=str(index + 1)) for index in range(101)])
