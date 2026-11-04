"""验证各类启动故障共享诊断合同，不按具体文件预设修复答案。"""

import json
import tempfile
import unittest
from pathlib import Path
from xml.etree import ElementTree

from app.services.preview_diagnostics import (
    diagnostic_from_exception, diagnostics_from_command, launch_diagnostics,
    read_json_object, safe_diagnostic_path,
)
from app.services.preview_runtime_state import begin_attempt, finish_attempt, read_record, runtime_snapshot
from app.protocols.preview_runtime import preview_runtime_capabilities


class PreviewDiagnosticsTests(unittest.TestCase):
    """使用真实解析错误和临时日志验证定位、脱敏及状态保留。"""

    def setUp(self) -> None:
        """准备隔离的代码和受控日志目录。"""
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / "frontend/src").mkdir(parents=True)
        self.logs = self.root / ".devagentstudio/runtime/launch"
        self.logs.mkdir(parents=True)

    def tearDown(self) -> None:
        """只清理测试工作区。"""
        self.temp.cleanup()

    def test_arbitrary_json_files_keep_actual_parser_location(self) -> None:
        """相同解析器处理任何配置路径，不依赖 package.json 的名字。"""
        for name in ("frontend/settings.json", "frontend/src/theme.json"):
            with self.subTest(name=name):
                path = self.root / name
                path.write_text('{\n  missing": 1\n}')
                value, diagnostics = read_json_object(self.root, path, "config_read")
                self.assertIsNone(value)
                item = diagnostics[0]
                self.assertEqual(item["category"], "parse")
                self.assertEqual(item["code"], "JSONDecodeError")
                self.assertEqual(item["location"], {"path": name, "line": 2, "column": 3})
                self.assertIn('missing": 1', item["excerpt"])

    def test_health_metadata_exposes_current_diagnostic_contract(self) -> None:
        """健康元数据与实际诊断模型共用类别集合。"""
        capabilities = preview_runtime_capabilities()
        self.assertEqual(capabilities["diagnosticsField"], "runtime.diagnostics")
        self.assertEqual(capabilities["diagnosticCategories"], ["parse", "dependency", "compile", "process", "readiness"])

    def test_other_parser_errors_use_exception_position(self) -> None:
        """XML 解析异常复用同一位置适配，无需增加文件名判断。"""
        path = self.root / "frontend/settings.xml"
        path.write_text("<root>\n<bad></root>")
        try:
            ElementTree.fromstring(path.read_text())
        except ElementTree.ParseError as error:
            item = diagnostic_from_exception(self.root, "config_read", error, path)
        self.assertEqual(item["category"], "parse")
        self.assertEqual(item["location"]["path"], "frontend/settings.xml")
        self.assertEqual(item["location"]["line"], 2)

    def test_access_encoding_and_root_shape_failures_are_explicit(self) -> None:
        """文件不存在、编码错误和 JSON 根结构错误保留不同错误类型。"""
        path = self.root / "frontend/config.json"
        self.assertEqual(read_json_object(self.root, path, "config")[1][0]["code"], "FileNotFoundError")
        path.write_bytes(b"\xff")
        self.assertEqual(read_json_object(self.root, path, "config")[1][0]["code"], "UnicodeDecodeError")
        path.write_text("[]")
        self.assertIn("root must be an object", read_json_object(self.root, path, "config")[1][0]["message"])

    def test_command_categories_keep_locations_exit_code_and_logs(self) -> None:
        """安装、编译、进程和就绪失败共用命令证据合同。"""
        for category, text in (
            ("dependency", "ERR_PNPM_FETCH_404 registry unavailable"),
            ("compile", "src/Page.tsx(8,4): error TS1005: expected token"),
            ("process", "Error: address already in use"),
            ("readiness", "server readiness timed out"),
        ):
            with self.subTest(category=category):
                log = self.logs / "command.stderr.log"
                log.write_text(text)
                items = diagnostics_from_command(self.root, "command", category, "failed", {
                    "argv": ["tool", "--token", "private-value"], "cwd": str(self.root / "frontend"),
                    "returncode": 2, "timed_out": category == "readiness", "stderr_log": str(log),
                })
                item = items[0]
                self.assertEqual(item["category"], category)
                self.assertEqual(item["command"]["exitCode"], 2)
                self.assertEqual(item["command"]["timedOut"], category == "readiness")
                self.assertEqual(item["logPaths"], ["/.devagentstudio/runtime/launch/command.stderr.log"])
                self.assertNotIn("private-value", json.dumps(item))
                self.assertIn(text, item["excerpt"])
                if category == "compile":
                    self.assertEqual(item["location"], {"path": "frontend/src/Page.tsx", "line": 8, "column": 4})

    def test_backend_compiler_and_unknown_output_remain_available(self) -> None:
        """Java 诊断与无法解析的工具输出均保留真实信息。"""
        text = f"[ERROR] {self.root}/backend/src/Handler.java:[12,7] cannot find symbol"
        item = diagnostics_from_command(self.root, "backend_build", "compile", "failed", {"stdout_tail": text})[0]
        self.assertEqual(item["location"]["path"], "backend/src/Handler.java")
        self.assertEqual(item["location"]["column"], 7)
        unknown = diagnostics_from_command(self.root, "tool", "process", "failed", {"stderr_tail": "unknown format: details"})[0]
        self.assertIsNone(unknown["location"])
        self.assertIn("unknown format: details", unknown["excerpt"])

    def test_failure_snapshot_retains_diagnostics_and_success_clears_them(self) -> None:
        """证据经过落盘和运行快照后仍完整；下一次成功不带旧错误。"""
        begin_attempt(self.root)
        failed = {"status": "failed", "message": "compile failed", "server": {"returncode": 1}}
        failed["diagnostics"] = launch_diagnostics(self.root, "frontend", failed)
        finish_attempt(self.root, {"status": "failed", "frontend": failed, "failed_stage": "frontend_start"})
        self.assertEqual(read_record(self.root)["diagnostics"], failed["diagnostics"])
        self.assertEqual(runtime_snapshot(self.root)["diagnostics"], failed["diagnostics"])
        finish_attempt(self.root, {"status": "stopped"})
        self.assertEqual(read_record(self.root)["diagnostics"], [])

    def test_sensitive_and_outside_paths_are_not_read_or_exposed_as_candidates(self) -> None:
        """路径定位不能把环境文件、平台状态或越界内容加入修复范围。"""
        for value in ("../outside.ts", "/etc/passwd", "frontend/.env", ".devagentstudio/application.json", "frontend/node_modules/a.ts"):
            self.assertIsNone(safe_diagnostic_path(self.root, value), value)
        secret = self.root / "frontend/.env"
        secret.write_text("private-value")
        item = diagnostics_from_command(self.root, "test", "process", "failed", {"stderr_log": str(secret)})[0]
        self.assertNotIn("private-value", json.dumps(item))
        self.assertEqual(item["logPaths"], [])
