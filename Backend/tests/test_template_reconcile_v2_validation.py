"""覆盖 V2 Validation Plan 的只读验收和副作用 Sandbox 边界。"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from app.services.template_reconcile.protocol_v2 import ValidationPlanItemV2
from app.services.template_reconcile.validation_v2 import execute_validation_plan_v2, validation_plan_passed_v2


def _item(**overrides: object) -> ValidationPlanItemV2:
    """构造可被单例测试按需覆盖的合法 V2 Validation 项。"""

    values: dict[str, object] = {"validationId": "validation-0", "index": 0, "type": "FILE_EXISTS", "workingDirectory": ".", "blocking": True, "timeoutSeconds": 10, "executionMode": "REAL_WORKSPACE", "path": "src/feature.ts"}
    values.update(overrides)
    return ValidationPlanItemV2.model_validate(values)


class TemplateReconcileV2ValidationTests(unittest.TestCase):
    """验证 V2 只读后置条件与构建 Sandbox 的不可污染性。"""

    def test_capability_postcondition_aggregates_structure_and_json_checks(self) -> None:
        """Capability 验收只有所有受限子断言成功才允许通过。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "src").mkdir()
            (root / "src/feature.ts").write_text("// managed:feature\n", encoding="utf-8")
            (root / "package.json").write_text('{"dependencies":{"demo":"1.0.0"}}', encoding="utf-8")
            item = _item(type="CAPABILITY_POSTCONDITION", capabilityId="demo", checks=[{"type": "FILE_EXISTS", "path": "src/feature.ts"}, {"type": "STRUCTURE_CHECK", "path": "src/feature.ts", "containsAll": ["managed:feature"]}, {"type": "JSON_STRUCTURE_CHECK", "path": "package.json", "pointer": "/dependencies/demo", "expected": "1.0.0"}])
            results = execute_validation_plan_v2(root, [item])
            self.assertTrue(results[0].passed)
            self.assertTrue(validation_plan_passed_v2(results))

    def test_json_structure_check_retains_optional_expected_until_dual_end_sync(self) -> None:
        """确认 DTO 在双端同步收紧前仍接受旧 Package，执行器始终按 value == expected 语义处理。"""

        item = _item(type="JSON_STRUCTURE_CHECK", path="package.json", pointer="/dependencies/demo", expected=None)
        self.assertIsNone(item.expected)

    def test_blocking_postcondition_failure_stops_following_validations(self) -> None:
        """阻断性后置条件失败时不能继续生成误导性的后续通过结果。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = _item(type="CAPABILITY_POSTCONDITION", capabilityId="demo", checks=[{"type": "FILE_EXISTS", "path": "missing.ts"}])
            results = execute_validation_plan_v2(root, [failed, _item(validationId="following", index=1)])
            self.assertEqual(1, len(results))
            self.assertFalse(validation_plan_passed_v2(results))

    def test_legacy_command_validation_is_parsed_but_not_executed(self) -> None:
        """历史 Sandbox 命令项保持可解析，并由真实项目重启验收统一替代。"""

        for kind in ("NPM_BUILD", "NPM_TEST", "MAVEN_TEST", "MAVEN_PACKAGE"):
            with self.subTest(kind=kind):
                item = _item(type=kind, executionMode="SANDBOX", path=None)
                results = execute_validation_plan_v2(Path.cwd(), [item])
                self.assertTrue(results[0].passed)
                self.assertIn("未执行", results[0].message)
