"""验证预览运行文件与单测生成并发变化时的归因边界。"""

from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any
from unittest.mock import Mock, patch

from app.agents.test_generation.generator import (
    _internal_artifact_snapshot,
    generate_or_update_unit_tests_with_agent,
)
from app.agents.test_generation.scope import ScopedTestGenerationBackend
from app.services.preview_runtime_guard import claim_maintenance, release_maintenance
from app.services.preview_runtime_state import (
    LOG_FILES,
    begin_attempt,
    finish_attempt,
    record_progress,
    runtime_root,
)


class TestGenerationPreviewWritesTests(unittest.TestCase):
    """并发预览只更新平台运行文件，单测仍须守住真实产物的写入边界。"""

    def _generate_with_preview(
        self, mode: str = "create", unauthorized_path: str | None = None,
    ) -> dict[str, Any]:
        """在 Agent 调用期间由独立线程写入真实预览状态及启动器运行文件。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            source = root / "frontend/src/apis/orders.ts"
            source.parent.mkdir(parents=True)
            source.write_text("export const orders = [];\n", encoding="utf-8")
            preview_start = Event()

            def write_preview(marker: str) -> None:
                """复用预览状态与维护服务，模拟启动器写入 PID 和各端日志。"""

                claim_maintenance(workspace, "preview-thread", "restart")
                try:
                    begin_attempt(workspace)
                    launch = runtime_root(workspace)
                    for layer, names in LOG_FILES.items():
                        for name in names:
                            (launch / name).write_text(f"{marker}: {layer} ready\n", encoding="utf-8")
                        pid = "9999998" if marker == "initial" else "9999999"
                        (launch / f"{layer}.pid").write_text(pid, encoding="utf-8")
                        record_progress(workspace, layer, "completed", "预览已启动")
                    finish_attempt(workspace, {"status": "running", "message": "预览已就绪"})
                finally:
                    release_maintenance(workspace, "preview-thread")

            if mode != "create":
                write_preview("initial")

            def preview_worker() -> None:
                """等待测试生成已进入调用，再独立创建、刷新或删除运行文件。"""

                if not preview_start.wait(timeout=5):
                    raise TimeoutError("测试生成未进入调用")
                if mode == "delete":
                    for path in runtime_root(workspace).iterdir():
                        path.unlink()
                    (root / ".devagentstudio/runtime/preview-maintenance.json").unlink()
                else:
                    write_preview(mode)

            with ThreadPoolExecutor(max_workers=1) as executor:
                preview = executor.submit(preview_worker)

                def invoke(_payload: dict[str, Any]) -> str:
                    """生成合法测试文件，同时等待另一线程完成预览文件写入。"""

                    test = root / "frontend/tests/orders.test.ts"
                    test.parent.mkdir(parents=True)
                    test.write_text("test('orders', () => expect(true).toBe(true));\n", encoding="utf-8")
                    preview_start.set()
                    preview.result(timeout=5)
                    if unauthorized_path:
                        path = root / unauthorized_path
                        path.parent.mkdir(parents=True, exist_ok=True)
                        path.write_text("unauthorized change\n", encoding="utf-8")
                    return json.dumps({"status": "completed", "test_files": ["frontend/tests/orders.test.ts"]})

                bundle = SimpleNamespace(test_generation=SimpleNamespace(invoke=invoke))
                with patch("app.agents.create_agent_bundle", return_value=bundle):
                    result = generate_or_update_unit_tests_with_agent({
                        "unit_test_generation_context": {
                            "source_files": ["frontend/src/apis/orders.ts"],
                            "affected_layers": ["frontend"],
                        },
                    }, workspace)
                preview.result(timeout=5)
                return result

    def test_preview_creation_updates_and_cleanup_do_not_fail_generation(self) -> None:
        """预览运行文件新增、刷新和清理均不能导致合法测试生成越界失败。"""

        for mode in ("create", "update", "delete"):
            with self.subTest(mode=mode):
                result = self._generate_with_preview(mode)
                self.assertEqual(result["status"], "completed", result)
                self.assertNotIn("unauthorized_paths", result["validation"])
                self.assertEqual(result["test_files"], ["frontend/tests/orders.test.ts"])
                self.assertEqual(
                    [file["path"] for changes in result["code_change_sets"] for file in changes["files"]],
                    ["frontend/tests/orders.test.ts"],
                )

    def test_preview_does_not_hide_unrelated_runtime_or_product_writes(self) -> None:
        """预览例外不能扩大到其他运行文件、相似路径或正式产物与源码。"""

        for path in (
            ".devagentstudio/runtime/launch/other.log",
            ".devagentstudio/runtime/launch/nested/frontend.pid",
            ".devagentstudio/runtime/launch-backup/frontend.pid",
            ".devagentstudio/runtime/launch/frontend.pid.backup",
            ".devagentstudio/runtime/launch/.preview-invalid.tmp",
            ".devagentstudio/runtime/other.json",
            ".devagentstudio/runtime/nested/preview-maintenance.json",
            ".devagentstudio/plans/technical-plan.json",
            ".devagentstudio/application.json",
            "frontend/src/apis/orders.ts",
            ".env",
        ):
            with self.subTest(path=path):
                result = self._generate_with_preview(unauthorized_path=path)
                self.assertEqual(result["status"], "failed")
                self.assertEqual(result["validation"]["unauthorized_paths"], [path])

    def test_atomic_preview_temporary_files_are_not_formal_artifacts(self) -> None:
        """快照撞上预览原子替换中间态时只排除精确的临时文件模式。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace) / ".devagentstudio"
            for path in (
                "runtime/launch/.preview-0123456789abcdef0123456789abcdef.tmp",
                "runtime/preview-maintenance.tmp",
                "runtime/launch/.preview-invalid.tmp",
                "runtime/launch/.preview-0123456789abcdef0123456789abcdef.tmp.backup",
                "plans/.preview-0123456789abcdef0123456789abcdef.tmp",
            ):
                target = root / path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("runtime", encoding="utf-8")
            self.assertEqual(set(_internal_artifact_snapshot(workspace)), {
                ".devagentstudio/runtime/launch/.preview-invalid.tmp",
                ".devagentstudio/runtime/launch/.preview-0123456789abcdef0123456789abcdef.tmp.backup",
                ".devagentstudio/plans/.preview-0123456789abcdef0123456789abcdef.tmp",
            })

    def test_preview_exclusion_does_not_authorize_agent_writes(self) -> None:
        """快照排除不改变测试 Agent 的文件工具权限，预览文件仍不可写。"""

        delegate = Mock()
        backend = ScopedTestGenerationBackend(delegate)
        for path in (
            "/.devagentstudio/runtime/launch/frontend.pid",
            "/.devagentstudio/runtime/launch/frontend.stdout.log",
            "/.devagentstudio/runtime/launch/preview-runtime.json",
            "/.devagentstudio/runtime/preview-maintenance.json",
        ):
            with self.subTest(path=path):
                self.assertIn("test_generation_path_denied", backend.write(path, "bad").error)
                self.assertIn("test_generation_path_denied", backend.edit(path, "old", "bad").error)
                self.assertIn("test_generation_path_denied", backend.upload_files([(path, b"bad")])[0].error)
        delegate.write.assert_not_called()
        delegate.edit.assert_not_called()
        delegate.upload_files.assert_not_called()
