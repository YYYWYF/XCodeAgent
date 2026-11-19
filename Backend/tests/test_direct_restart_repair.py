"""二次修改重启验收的故障归因、文件授权与修复重验。"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

from app.graph.direct_modification_workflow import _route_direct_validation
from app.graph.nodes.direct_modification import validate_direct_fix
from app.graph.nodes.direct_repair import direct_modification_repair
from app.protocols.direct_modification_projection import direct_node_process_step
from app.services.backend_project_launcher import _run_backend_build
from app.services.direct_repair import authorized_direct_repair_paths
from app.services.direct_restart_diagnostics import capture_direct_restart_failure
from app.services.preview_runtime_state import runtime_root
from app.services.test_validation import create_repair_task_plan
from app.workspace.content_artifacts import read_artifact


class DirectRestartRepairTests(unittest.TestCase):
    """通过实际验证节点、修复调度器和隔离工程验证重启门禁。"""

    def setUp(self) -> None:
        """建立隔离工作区及当前启动器可识别的前后端目录。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name).resolve()
        self.frontend = self.root / "frontend"
        self.backend = self.root / "backend"
        self.logs = runtime_root(self.root)
        self.logs.mkdir(parents=True)
        self.page = self._write("frontend/src/Page.tsx", 'export default "blue";')
        self._write("frontend/package.json", '{"packageManager":"pnpm@10.0.0","scripts":{"dev":"vite","build":"vite build"}}')
        self._write("backend/pom.xml", "<project/>")
        self.java = self._write("backend/src/main/java/example/Clock.java", "class Clock {}")
        self.java_test = self._write("backend/src/test/java/example/ClockTest.java", "class ClockTest { Clock clock; }")
        self.state = {
            "workspace": str(self.root), "conversation_intent": "implementation_fix",
            "direct_modification_owner": "frontend",
            "direct_code_change_sets": [{"files": [{"path": "frontend/src/Page.tsx"}]}],
        }

    def _write(self, path: str, content: str) -> Path:
        """在隔离工作区写入工程或测试夹具文件。"""

        target = self.root / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        return target

    def _failed_checks(self, owner: str, stage: str, output: str) -> list[dict[str, Any]]:
        """创建两个共享同一启动失败的展示检查，保留受控日志和命令。"""

        log = self.logs / f"{owner}.stdout.log"
        log.write_text(output, encoding="utf-8")
        operation = "build" if stage == "backend_build" else "server"
        launch = {
            "status": "failed", "failed_stage": stage,
            owner: {"status": "failed", operation: {
                "cwd": str(self.root / owner), "returncode": 1, "stdout_log": str(log),
            }},
        }
        return [
            {"id": f"{layer}_project_restart", "layer": layer, "passed": False,
             "name": f"{layer} 项目重启验收", "evidence": "启动失败", "blocking": True,
             "repairable": False, "startup": {"launch": launch}}
            for layer in ("frontend", "backend")
        ]

    def test_frontend_change_repairs_backend_surefire_failure_and_restarts(self) -> None:
        """使用真实启动编排和 SmallTask 调度，替换模型、外部命令与服务进程。"""

        initial_log = (
            "[ERROR] Tests run: 1, Failures: 1, Errors: 0, Skipped: 0 -- in example.ClockTest\n"
            "    at example.ClockTest.readsClock(ClockTest.java:5)\n"
            "password=private-example\n"
        )
        commands: list[list[str]] = []

        def command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            """由后端文件状态决定 Maven 首轮失败、修复后成功。"""

            commands.append(argv)
            if argv[0] == "mvn" and "fixed" not in self.java.read_text(encoding="utf-8"):
                return subprocess.CompletedProcess(argv, 1, initial_log, "")
            return subprocess.CompletedProcess(argv, 0, "current round succeeded", "")

        def backend(root: Path, **_kwargs: object) -> dict[str, Any]:
            """运行真实 Maven 命令封装并模拟后端进程就绪。"""

            build = _run_backend_build(maven_command="mvn", workspace=root, cwd=self.backend, runtime_root=self.logs)
            if build["returncode"]:
                return {"status": "failed", "failed_stage": "backend_build", "build": build,
                        "message": "后端 Maven 构建失败。"}
            return {"status": "running"}

        def plan(**kwargs: Any) -> dict[str, Any]:
            """使用正式任务编译器验证 Planner 收到的责任方与精确 scope。"""

            self.assertEqual(len(kwargs["revision_requests"]), 1)
            self.assertEqual(kwargs["revision_requests"][0]["owner"], "backend")
            self.assertEqual(len(kwargs["scoped_tasks"]), 1)
            return create_repair_task_plan(
                revision_requests=kwargs["revision_requests"], scoped_tasks=kwargs["scoped_tasks"],
                build_execution_scope=kwargs["build_execution_scope"], repair_attempt=kwargs["repair_attempt"],
                agent_note="修复时钟源码。",
            )

        def repair(*, packet: dict[str, Any], **_kwargs: object) -> str:
            """只写诊断授权的业务源码，验证完整执行路径和真实差异。"""

            self.assertEqual(packet["owner"], "backend")
            self.assertEqual(set(packet["allowedPaths"]), {
                "backend/src/main/java/example/Clock.java", "backend/src/test/java/example/ClockTest.java",
            })
            self.assertIn("保留有效用例和断言", packet["description"])
            self.java.write_text("class Clock { /* fixed */ }", encoding="utf-8")
            return json.dumps({"status": "completed", "summary": "已修复时钟源码", "verification": []})

        with (
            patch("app.services.direct_validation.shutil.which", return_value="pnpm"),
            patch("app.services.integration_test_runner.workspace_process_registry.run", side_effect=command),
            patch("app.services.project_launcher.resolve_frontend_node", return_value=None),
            patch("app.services.project_launcher.launch_backend_project", side_effect=backend),
            patch("app.services.project_launcher.launch_frontend_project", return_value={"status": "running"}) as frontend,
            patch("app.graph.nodes.direct_repair.plan_repairs_with_repair_planner_agent", side_effect=plan),
            patch("app.services.small_task.invoke_small_task_agent", side_effect=repair),
        ):
            failed = validate_direct_fix(self.state)
            frontend.assert_not_called()
            self.assertEqual(_route_direct_validation(failed), "direct_modification_repair")
            step = direct_node_process_step("validate_direct_fix", failed)
            check = step["checks"][-1]
            self.assertEqual(check["id"], "frontend_project_restart")
            self.assertEqual(check["status"], "failed")

            exhausted = validate_direct_fix({**self.state, "repair_iteration": 3})
            self.assertEqual(exhausted["status"], "failed")
            self.assertEqual(_route_direct_validation(exhausted), "finalize")
            state = {**self.state, **failed}
            fixed = direct_modification_repair(state)
            self.assertEqual(fixed["repair_iteration"], 1, fixed.get("message"))
            verified = validate_direct_fix({**state, **fixed})
            self.assertTrue(verified["quality_gate_passed"], verified["test_results"])
            frontend.assert_called_once()

        self.assertEqual(commands, [
            ["pnpm", "exec", "tsc", "-b"], ["pnpm", "run", "build"], ["mvn", "clean", "install"],
        ] * 3)
        log = "".join(read_artifact(self.root, reference) for reference in check["errorLogReferences"])
        self.assertIn("Failures: 1", log)
        self.assertNotIn("private-example", log)
        self.assertNotIn("current round succeeded", log)

    def test_compiler_and_runtime_diagnostics_repair_only_the_failed_side(self) -> None:
        """编译、Java 启动和 Vite 启动均精确选中单个展示行与诊断源码。"""

        for owner, stage, output, expected in (
            ("backend", "backend_build", "[ERROR] src/main/java/example/Clock.java:[2,1] cannot find symbol", self.java),
            ("backend", "backend_start", "Caused by: java.lang.IllegalStateException\n at example.Clock.current(Clock.java:3)", self.java),
            ("frontend", "frontend_start", 'Could not resolve "./Missing.css" from "src/Page.tsx"', self.page),
        ):
            with self.subTest(stage=stage):
                checks = self._failed_checks(owner, stage, output)
                capture_direct_restart_failure(self.root, checks)
                eligible = [check for check in checks if check["repairable"]]
                self.assertEqual(len(eligible), 1)
                self.assertEqual(eligible[0]["repair_owner"], owner)
                self.assertEqual(eligible[0]["restart_repair_paths"], [expected.relative_to(self.root).as_posix()])
                self.assertEqual(checks[0]["errorLogReferences"], checks[1]["errorLogReferences"])

    def test_environment_unknown_and_outside_paths_remain_blocking(self) -> None:
        """环境报错即使带栈帧也不授权，缺失路径和输出目录同样不授权。"""

        self._write("backend/target/generated/Generated.java", "class Generated {}")
        for output in (
            "Caused by: java.net.BindException: Address already in use\n    at example.Clock.current(Clock.java:3)",
            "Communications link failure\n    at example.Clock.current(Clock.java:3)",
            "[ERROR] ../outside.java:[1,1] failed\n[ERROR] target/generated/Generated.java:[1,1] failed",
            "[ERROR] src/main/java/example/Missing.java:[1,1] failed",
        ):
            with self.subTest(output=output):
                checks = self._failed_checks("backend", "backend_build", output)
                capture_direct_restart_failure(self.root, checks)
                self.assertTrue(all(check["blocking"] and not check["repairable"] for check in checks))

    def test_restart_dispatch_rechecks_file_identity_without_falling_back_to_diff(self) -> None:
        """规划前再次拒绝已经消失或换成指向另一文件的授权目标。"""

        checks = self._failed_checks("backend", "backend_build", "at example.Clock.current(Clock.java:3)")
        capture_direct_restart_failure(self.root, checks)
        self.java.unlink()
        self.java.symlink_to(self.java_test)
        state = {**self.state, "revision_requests": [{"failed_check": checks[1]}]}
        self.assertEqual(authorized_direct_repair_paths(state), {"backend": [], "frontend": []})
