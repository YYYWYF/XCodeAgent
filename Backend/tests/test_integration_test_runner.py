from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.services.integration_test_runner import run_integration_checks
from app.services.test_validation import create_revision_requests, evaluate_quality_gate


class IntegrationTestRunnerTests(unittest.TestCase):
    def test_static_application_skips_all_backend_checks(self) -> None:
        """Static 即使保留 Maven 模板，也只执行前端质量门。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            frontend = root / "frontend"
            backend = root / "backend"
            application_dir = root / ".xcodeagent"
            frontend.mkdir()
            backend.mkdir()
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"static"}}',
                encoding="utf-8",
            )
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build"}}',
                encoding="utf-8",
            )
            (frontend / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'",
                encoding="utf-8",
            )
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录 Static 实际执行命令并统一返回成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks({"workspace": workspace})

        check_ids = [item["id"] for item in result["test_results"]]
        self.assertIn("frontend_build", check_ids)
        self.assertFalse(any(check_id.startswith("backend_") for check_id in check_ids))
        self.assertFalse(any("clean" in argv and "install" in argv for argv in calls))
        report = evaluate_quality_gate(
            test_results=result["test_results"],
        )
        self.assertFalse(
            any(
                check_id.startswith("backend_")
                for check_id in report["quality_gate"]["required_checks"]
            )
        )

    def test_runs_real_project_scripts_and_returns_structured_results(self) -> None:
        """验证真实项目脚本会生成结构化结果，且完全跳过 E2E 命令。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build","lint":"eslint .","typecheck":"tsc --noEmit","test":"vitest run","test:e2e":"playwright test"}}',
                encoding="utf-8",
            )
            (frontend / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'", encoding="utf-8")

            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录待执行命令并返回成功结果。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks({"workspace": workspace})

        ids = [item["id"] for item in result["test_results"]]
        self.assertIn("frontend_install", ids)
        self.assertIn("frontend_build", ids)
        self.assertIn("frontend_typecheck", ids)
        self.assertIn("frontend_unit_tests", ids)
        self.assertNotIn("e2e_tests", ids)
        self.assertIn(["pnpm", "install"], calls)
        self.assertIn(["pnpm", "run", "build"], calls)
        self.assertNotIn(["pnpm", "run", "test"], calls)
        self.assertNotIn(["pnpm", "run", "test:e2e"], calls)
        self.assertNotIn(["npx", "playwright", "test"], calls)
        frontend_unit_tests = next(
            item for item in result["test_results"] if item["id"] == "frontend_unit_tests"
        )
        self.assertTrue(frontend_unit_tests["passed"])
        self.assertTrue(frontend_unit_tests["skipped"])
        self.assertTrue(all(item["passed"] for item in result["test_results"]))
        self.assertTrue(all("execution" in item for item in result["test_results"]))
        report = evaluate_quality_gate(
            test_results=result["test_results"],
        )
        self.assertNotIn("e2e_tests", report["quality_gate"]["required_checks"])

    def test_frontend_unit_tests_run_after_build_when_flat_test_exists(self) -> None:
        """验证约定测试文件存在时优先执行 test:unit，且位于构建检查之后。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            tests = frontend / "tests"
            tests.mkdir(parents=True)
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build","test":"jest","test:unit":"jest --runInBand"}}',
                encoding="utf-8",
            )
            (frontend / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'",
                encoding="utf-8",
            )
            (tests / "page-orders.test.tsx").write_text(
                "test('renders', () => expect(true).toBe(true));",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录前端命令并统一模拟成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks({"workspace": workspace})

        unit_tests = next(
            item for item in result["test_results"] if item["id"] == "frontend_unit_tests"
        )
        self.assertTrue(unit_tests["passed"])
        self.assertFalse(unit_tests["skipped"])
        self.assertEqual(
            calls,
            [
                ["pnpm", "install"],
                ["pnpm", "run", "build"],
                ["pnpm", "run", "test:unit"],
            ],
        )

    def test_build_and_unit_phases_do_not_repeat_frontend_build(self) -> None:
        """构建阶段完成后进入单测阶段时不得重复安装或构建。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            tests = frontend / "tests"
            tests.mkdir(parents=True)
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build","test:unit":"jest --runInBand"}}',
                encoding="utf-8",
            )
            (frontend / "pnpm-lock.yaml").write_text(
                "lockfileVersion: '9.0'",
                encoding="utf-8",
            )
            application_dir = Path(workspace) / ".xcodeagent"
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"static"}}',
                encoding="utf-8",
            )
            test_path = "frontend/tests/page-orders.test.tsx"
            (tests / "page-orders.test.tsx").write_text(
                "test('renders', () => expect(true).toBe(true));",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录两个阶段的前端命令并统一模拟成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                build_result = run_integration_checks(
                    {
                        "workspace": workspace,
                        "unit_test_affected_layers": ["frontend"],
                    },
                    phase="build",
                )
                unit_result = run_integration_checks(
                    {
                        "workspace": workspace,
                        "unit_test_affected_layers": ["frontend"],
                        "unit_test_generation": {"test_files": [test_path]},
                        "test_results": build_result["test_results"],
                    },
                    phase="unit",
                )

        self.assertEqual(
            [item["id"] for item in build_result["test_results"]],
            ["frontend_install", "frontend_typecheck", "frontend_build"],
        )
        self.assertEqual(
            [item["id"] for item in unit_result["test_results"]],
            ["frontend_unit_tests"],
        )
        self.assertEqual(
            calls,
            [
                ["pnpm", "install"],
                ["pnpm", "run", "build"],
                ["pnpm", "run", "test:unit"],
            ],
        )

    def test_affected_layers_skip_existing_frontend_unit_tests(self) -> None:
        """验证受影响层未包含 frontend 时不执行已有的前端测试。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            tests = frontend / "tests"
            tests.mkdir(parents=True)
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build","test":"jest"}}',
                encoding="utf-8",
            )
            (tests / "page-orders.test.tsx").write_text(
                "test('renders', () => expect(true).toBe(true));",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录非测试前端命令并统一模拟成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks(
                    {
                        "workspace": workspace,
                        "unit_test_affected_layers": ["backend"],
                    }
                )

        unit_tests = next(
            item for item in result["test_results"] if item["id"] == "frontend_unit_tests"
        )
        self.assertNotIn(["pnpm", "run", "test"], calls)
        self.assertTrue(unit_tests["passed"])
        self.assertTrue(unit_tests["skipped"])
        self.assertIn("未影响前端", unit_tests["evidence"])

    def test_missing_package_manager_returns_structured_failures(self) -> None:
        """验证缺少包管理器时返回结构化检查失败而不是把 None 传给 subprocess。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build"}}',
                encoding="utf-8",
            )
            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    return_value=None,
                ),
                patch("app.services.integration_test_runner.subprocess.run") as run,
            ):
                result = run_integration_checks({"workspace": workspace})

        install = next(item for item in result["test_results"] if item["id"] == "frontend_install")
        build = next(item for item in result["test_results"] if item["id"] == "frontend_build")
        self.assertFalse(install["passed"])
        self.assertFalse(build["passed"])
        self.assertIn("未找到包管理器命令", install["evidence"])
        run.assert_not_called()

    def test_python_project_markers_do_not_trigger_backend_commands(self) -> None:
        """验证 Python 配置文件不会触发解释器探测或 pytest 命令。"""

        for marker, content in (
            ("pyproject.toml", "[tool.pytest.ini_options]\n"),
            ("pytest.ini", "[pytest]\n"),
            ("setup.cfg", "[tool:pytest]\n"),
        ):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory() as workspace:
                (Path(workspace) / marker).write_text(content, encoding="utf-8")
                with patch("app.services.integration_test_runner.subprocess.run") as run:
                    result = run_integration_checks({"workspace": workspace})

                backend_build = next(
                    item
                    for item in result["test_results"]
                    if item["id"] == "backend_build"
                )
                self.assertTrue(backend_build["skipped"])
                self.assertIsNone(backend_build["command"])
                self.assertIsNone(backend_build["language"])
                run.assert_not_called()

    def test_maven_wrapper_matches_host_platform(self) -> None:
        """验证 Maven 工程优先使用当前系统可执行的 wrapper。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "Backend"
            application_dir = root / ".xcodeagent"
            backend.mkdir()
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"database"}}',
                encoding="utf-8",
            )
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            (backend / "mvnw").write_text("#!/bin/sh\n", encoding="utf-8")
            (backend / "mvnw.cmd").write_text("@echo off\r\n", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录 Maven 命令并返回成功结果。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch(
                "app.services.integration_test_runner.subprocess.run",
                side_effect=fake_run,
            ):
                run_integration_checks({"workspace": workspace})

        wrapper_name = "mvnw.cmd" if os.name == "nt" else "mvnw"
        self.assertTrue(calls)
        self.assertTrue(all(Path(argv[0]).name == wrapper_name for argv in calls))

    def test_maven_zero_tests_passes_and_skips_test_command(self) -> None:
        """验证 Maven 工程没有测试类时只构建，并以 passed/skipped 放行单元测试。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "backend"
            application_dir = root / ".xcodeagent"
            backend.mkdir()
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"database"}}',
                encoding="utf-8",
            )
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录 Maven 命令并统一模拟成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks({"workspace": workspace})

        unit_tests = next(
            item for item in result["test_results"] if item["id"] == "backend_unit_tests"
        )
        self.assertEqual(
            calls,
            [["mvn", "-B", "-Dmaven.test.skip=true", "clean", "install"]],
        )
        self.assertTrue(unit_tests["passed"])
        self.assertTrue(unit_tests["skipped"])
        self.assertIsNone(unit_tests["command"])

    def test_maven_build_and_unit_tests_are_separate_and_ordered(self) -> None:
        """验证 Maven 先跳过测试完成构建，再独立执行非空单元测试。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "Backend"
            tests = backend / "src" / "test" / "java" / "example"
            application_dir = root / ".xcodeagent"
            tests.mkdir(parents=True)
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"database"}}',
                encoding="utf-8",
            )
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            (tests / "OrderServiceTest.java").write_text(
                "class OrderServiceTest {}",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录 Maven 两阶段命令并统一模拟成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks({"workspace": workspace})

        unit_tests = next(
            item for item in result["test_results"] if item["id"] == "backend_unit_tests"
        )
        self.assertEqual(
            calls,
            [
                ["mvn", "-B", "-Dmaven.test.skip=true", "clean", "install"],
                ["mvn", "-B", "-DfailIfNoTests=true", "test"],
            ],
        )
        self.assertTrue(unit_tests["passed"])
        self.assertFalse(unit_tests["skipped"])

    def test_affected_layers_skip_existing_backend_unit_tests(self) -> None:
        """验证受影响层未包含 backend 时仍构建后端但不执行已有测试。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "backend"
            tests = backend / "src" / "test" / "java" / "example"
            application_dir = root / ".xcodeagent"
            tests.mkdir(parents=True)
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"database"}}',
                encoding="utf-8",
            )
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            (tests / "OrderServiceTest.java").write_text(
                "class OrderServiceTest {}",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录后端构建命令并统一模拟成功。"""

                calls.append(argv)
                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks(
                    {
                        "workspace": workspace,
                        "unit_test_affected_layers": ["frontend"],
                    }
                )

        unit_tests = next(
            item for item in result["test_results"] if item["id"] == "backend_unit_tests"
        )
        self.assertEqual(
            calls,
            [["mvn", "-B", "-Dmaven.test.skip=true", "clean", "install"]],
        )
        self.assertTrue(unit_tests["passed"])
        self.assertTrue(unit_tests["skipped"])
        self.assertIn("未影响后端", unit_tests["evidence"])

    def test_maven_build_failure_blocks_unit_test_command(self) -> None:
        """验证生产构建失败时不重复执行 Maven 单元测试命令。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace)
            backend = root / "backend"
            tests = backend / "src" / "test" / "java" / "example"
            application_dir = root / ".xcodeagent"
            tests.mkdir(parents=True)
            application_dir.mkdir()
            (application_dir / "application.json").write_text(
                '{"datasource":{"type":"database"}}',
                encoding="utf-8",
            )
            (backend / "pom.xml").write_text("<project />", encoding="utf-8")
            (tests / "OrderServiceTest.java").write_text(
                "class OrderServiceTest {}",
                encoding="utf-8",
            )
            calls: list[list[str]] = []

            def fake_run(argv, **kwargs):
                """记录构建失败前实际执行的 Maven 命令。"""

                calls.append(argv)
                return SimpleNamespace(returncode=1, stdout="", stderr="compile failed")

            with (
                patch(
                    "app.services.integration_test_runner.shutil.which",
                    side_effect=lambda name: name,
                ),
                patch(
                    "app.services.integration_test_runner.subprocess.run",
                    side_effect=fake_run,
                ),
            ):
                result = run_integration_checks({"workspace": workspace})

        unit_tests = next(
            item for item in result["test_results"] if item["id"] == "backend_unit_tests"
        )
        self.assertEqual(
            calls,
            [["mvn", "-B", "-Dmaven.test.skip=true", "clean", "install"]],
        )
        self.assertTrue(unit_tests["passed"])
        self.assertTrue(unit_tests["skipped"])
        self.assertIn("构建检查失败", unit_tests["evidence"])

    def test_timeout_bytes_are_decoded_and_recorded_as_failed_check(self) -> None:
        """验证超时命令的字节输出会被解码并记录为失败检查。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build"}}',
                encoding="utf-8",
            )
            timeout = subprocess.TimeoutExpired(
                cmd=["npm", "install"],
                timeout=180,
                output="安装中".encode(),
                stderr=b"network timeout",
            )

            with patch(
                "app.services.integration_test_runner.subprocess.run",
                side_effect=timeout,
            ):
                result = run_integration_checks({"workspace": workspace})

            install = next(
                item
                for item in result["test_results"]
                if item["id"] == "frontend_install"
            )
            stdout_log = Path(install["execution"]["stdout_log"])
            stderr_log = Path(install["execution"]["stderr_log"])
            self.assertFalse(install["passed"])
            self.assertTrue(install["execution"]["timed_out"])
            self.assertEqual(stdout_log.read_text(encoding="utf-8"), "安装中")
            self.assertEqual(
                stderr_log.read_text(encoding="utf-8"),
                "network timeout",
            )
            self.assertTrue(install["execution"]["stdout_log_virtual"].startswith("/.xcodeagent/"))
            self.assertTrue(install["execution"]["stderr_log_virtual"].startswith("/.xcodeagent/"))
            self.assertEqual(install["execution"]["stdout_tail"], "安装中")
            self.assertEqual(install["execution"]["stderr_tail"], "network timeout")

    def test_reports_running_and_terminal_progress_for_each_check(self) -> None:
        """验证实时进度先进入运行中，再以通过、跳过或失败状态收敛。"""

        with tempfile.TemporaryDirectory() as workspace:
            frontend = Path(workspace) / "Frontend"
            frontend.mkdir()
            (frontend / "package.json").write_text(
                '{"scripts":{"build":"vite build"}}',
                encoding="utf-8",
            )
            progress: list[dict] = []

            def fake_run(argv, **kwargs):
                """模拟成功命令，避免测试依赖本机工具链。"""

                return SimpleNamespace(returncode=0, stdout="ok", stderr="")

            with patch(
                "app.services.integration_test_runner.subprocess.run",
                side_effect=fake_run,
            ):
                run_integration_checks(
                    {"workspace": workspace},
                    on_progress=progress.append,
                )

        frontend_install_states = [
            event["status"]
            for event in progress
            if event["check"]["id"] == "frontend_install"
        ]
        backend_build_states = [
            event["status"]
            for event in progress
            if event["check"]["id"] == "backend_build"
        ]
        self.assertEqual(frontend_install_states, ["running", "passed"])
        self.assertEqual(backend_build_states, ["skipped"])

    def test_javascript_tests_directory_does_not_trigger_pytest(self) -> None:
        """验证仅包含 JavaScript 测试的 tests 目录不会被误判为 pytest 项目。"""

        with tempfile.TemporaryDirectory() as workspace:
            backend_tests = Path(workspace) / "tests" / "backend"
            backend_tests.mkdir(parents=True)
            (backend_tests / "api.test.js").write_text(
                "console.log('node test');",
                encoding="utf-8",
            )

            with patch("app.services.integration_test_runner.subprocess.run") as run:
                result = run_integration_checks({"workspace": workspace})

        backend_build = next(
            item
            for item in result["test_results"]
            if item["id"] == "backend_build"
        )
        self.assertTrue(backend_build["skipped"])
        self.assertIsNone(backend_build["command"])
        self.assertIsNone(backend_build["language"])
        run.assert_not_called()

    def test_failed_result_becomes_scheduler_friendly_revision_request(self) -> None:
        """验证失败检查会转换为调度器可消费的返修请求。"""

        failed_result = {
            "id": "frontend_build",
            "name": "前端构建检查",
            "passed": False,
            "failure_category": "compile_error",
            "command": "pnpm run build",
            "evidence": "命令执行失败：pnpm run build；退出码：1。",
            "execution": {
                "argv": ["pnpm", "run", "build"],
                "cwd": "Frontend",
                "returncode": 1,
                "stdout_log": "/tmp/stdout.log",
                "stderr_log": "/tmp/stderr.log",
            },
        }

        requests = create_revision_requests([failed_result])

        self.assertEqual(requests[0]["failed_attempt"]["failure_category"], "compile_error")
        self.assertEqual(requests[0]["failed_attempt"]["logs"]["stderr"], "/tmp/stderr.log")
        self.assertEqual(requests[0]["failed_attempt"]["execution"]["returncode"], 1)


if __name__ == "__main__":
    unittest.main()
