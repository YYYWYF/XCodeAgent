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

    def test_blocking_postcondition_failure_stops_following_validations(self) -> None:
        """阻断性后置条件失败时不能继续生成误导性的后续通过结果。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = _item(type="CAPABILITY_POSTCONDITION", capabilityId="demo", checks=[{"type": "FILE_EXISTS", "path": "missing.ts"}])
            results = execute_validation_plan_v2(root, [failed, _item(validationId="following", index=1)])
            self.assertEqual(1, len(results))
            self.assertFalse(validation_plan_passed_v2(results))

    def test_sandbox_command_cannot_write_into_real_workspace(self) -> None:
        """验证命令即使在 cwd 中写入产物，Sandbox 销毁后真实 Workspace 仍保持不变。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "package.json").write_text('{"scripts":{"build":"noop"}}', encoding="utf-8")
            (root / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            item = _item(type="NPM_BUILD", executionMode="SANDBOX", path=None)
            commands: list[list[str]] = []

            def runner(argv: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
                """模拟构建在 Sandbox 中写入产物，而不依赖本机 pnpm。"""

                commands.append(argv)
                if argv[:2] == ["pnpm", "install"]:
                    return subprocess.CompletedProcess(argv, 0, "prepared", "")
                self.assertEqual(["pnpm", "run", "build"], argv)
                Path(str(kwargs["cwd"]), "dist.txt").write_text("sandbox", encoding="utf-8")
                return subprocess.CompletedProcess(argv, 0, "ok", "")

            results = execute_validation_plan_v2(root, [item], command_runner=runner)
            self.assertTrue(results[0].passed)
            self.assertEqual(["pnpm", "install", "--frozen-lockfile", "--ignore-scripts"], commands[0])
            self.assertFalse((root / "dist.txt").exists())
            self.assertIsNotNone(results[0].stdout_log_ref)
            self.assertTrue((root / str(results[0].stdout_log_ref)).is_file())
            self.assertFalse(hasattr(results[0], "stdout"))
