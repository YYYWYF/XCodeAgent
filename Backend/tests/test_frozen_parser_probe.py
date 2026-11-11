"""回归冻结代码图探测误启动后端及 Windows 控制台弹窗。"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.code_graph import parser_probe
from app.services.code_graph.adapter import CodeReviewGraphAdapter


class FrozenParserProbeTests(unittest.TestCase):
    """验证专用分流、隐藏窗口、隔离失败和真实 CRG 接入。"""

    def test_windows_probe_uses_hidden_dedicated_entry(self) -> None:
        """冻结探测只传语法名称并隐藏窗口，不再传 Python 的 -c 参数。"""
        with (
            patch.object(parser_probe, "os", SimpleNamespace(name="nt")),
            patch.object(parser_probe.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
        ):
            self.assertTrue(parser_probe.run_frozen_parser_probe("java", 2.5))
        run.assert_called_once_with(
            [sys.executable, parser_probe.PARSER_PROBE_ARGUMENT, "java"],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=2.5, check=False, creationflags=0x08000000,
        )

    def test_probe_failures_remain_unavailable(self) -> None:
        """崩溃返回码、启动失败和超时均不得误报语法可用。"""
        for failure in (SimpleNamespace(returncode=1), OSError("launch failed"),
                        subprocess.TimeoutExpired("probe", 1)):
            with self.subTest(failure=failure):
                options = {"side_effect": failure} if isinstance(failure, Exception) else {"return_value": failure}
                with patch.object(parser_probe.subprocess, "run", **options):
                    self.assertFalse(parser_probe.run_frozen_parser_probe("java", 1))

    def test_posix_probe_has_no_windows_flags(self) -> None:
        """macOS 冻结入口沿用同一探测协议而不传 Windows 标志。"""
        with (
            patch.object(parser_probe, "os", SimpleNamespace(name="posix")),
            patch.object(parser_probe.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as run,
        ):
            self.assertTrue(parser_probe.run_frozen_parser_probe("tsx", 5))
        self.assertEqual(run.call_args.kwargs["creationflags"], 0)

    def test_frozen_adapter_retains_upstream_cache(self) -> None:
        """真实 CRG 按语言缓存成功与失败，只在冻结模式替换启动边界。"""
        import code_review_graph.parser as parser

        original = parser._run_parser_load_probe
        try:
            with patch.object(sys, "frozen", True, create=True):
                CodeReviewGraphAdapter()._load_imports()
            self.assertIs(parser._run_parser_load_probe, parser_probe.run_frozen_parser_probe)
            parser._clear_parser_probe_cache()
            with patch.object(parser_probe.subprocess, "run", side_effect=[
                SimpleNamespace(returncode=0), SimpleNamespace(returncode=1),
            ]) as run:
                for _ in range(2):
                    self.assertTrue(parser._parser_load_probe_succeeds("java"))
                    self.assertFalse(parser._parser_load_probe_succeeds("tsx"))
                self.assertEqual(run.call_count, 2)
        finally:
            parser._run_parser_load_probe = original
            parser._clear_parser_probe_cache()

    def test_source_adapter_preserves_default_probe(self) -> None:
        """源码开发不替换上游探测实现。"""
        import code_review_graph.parser as parser

        original = parser._run_parser_load_probe
        with patch.object(sys, "frozen", False, create=True):
            CodeReviewGraphAdapter()._load_imports()
        self.assertIs(parser._run_parser_load_probe, original)

    def test_probe_entry_never_initializes_server(self) -> None:
        """入口收到专用参数时仅加载语法，配置和 Uvicorn 都不得启动。"""
        entry = Path(__file__).resolve().parents[1] / "packaging/backend_server.py"
        spec = importlib.util.spec_from_file_location("probe_backend_entry", entry)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with (
            patch.object(sys, "argv", ["backend.exe", parser_probe.PARSER_PROBE_ARGUMENT, "java"]),
            patch.object(module, "load_backend_env") as load,
            patch("tree_sitter_language_pack.get_parser") as get_parser,
            self.assertRaises(SystemExit) as result,
        ):
            module.main()
        self.assertEqual(result.exception.code, 0)
        get_parser.assert_called_once_with("java")
        load.assert_not_called()

    def test_real_entry_loads_grammar_without_configuration(self) -> None:
        """真实子进程无需环境配置即可探测，坏语法及解释器参数快速失败。"""
        root = Path(__file__).resolve().parents[1]
        for arguments, succeeds in (([parser_probe.PARSER_PROBE_ARGUMENT, "java"], True),
                                    ([parser_probe.PARSER_PROBE_ARGUMENT, "missing_grammar"], False),
                                    ([parser_probe.PARSER_PROBE_ARGUMENT], False),
                                    (["-I", "-c", "pass"], False)):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, str(root / "packaging/backend_server.py"), *arguments],
                    cwd=root, env={**os.environ, "PYTHONPATH": str(root),
                                   "DEVAGENTSTUDIO_BACKEND_ENV_FILE": str(root / "missing-test.env")},
                    capture_output=True, timeout=10,
                )
                self.assertEqual(result.returncode == 0, succeeds, result.stderr.decode())
