"""二次修改的真实前端门禁与诊断驱动修复回归。"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.direct_modification_workflow import _route_direct_validation
from app.graph.nodes.direct_modification import finalize_direct_modification, validate_direct_fix
from app.graph.nodes.direct_repair import direct_modification_repair
from app.protocols.direct_modification_projection import direct_node_process_step
from app.services.direct_repair import authorized_direct_repair_paths
from app.services.direct_validation import run_direct_fix_validation
from app.services.test_validation import create_repair_task_plan


class DirectValidationTests(unittest.TestCase):
    """验证编译短路、失败证据、精确授权与完整修复闭环。"""

    def setUp(self) -> None:
        """创建不触碰用户工程的独立前端工作区。"""
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.frontend = self.root / "frontend"
        self.source = self.frontend / "src"
        self.source.mkdir(parents=True)
        (self.frontend / "package.json").write_text('{"type":"module"}', encoding="utf-8")
        self.app = self.source / "App.tsx"
        self.app.write_text('const App = "test";\nexport default Ap;\n', encoding="utf-8")
        (self.source / "Page.tsx").write_text('export default "blue";\n', encoding="utf-8")
        self.state = {
            "workspace": str(self.root),
            "request": "将页面颜色改为蓝色",
            "conversation_intent": "implementation_fix",
            "direct_modification_owner": "frontend",
            "direct_code_change_sets": [{"files": [{"path": "frontend/src/Page.tsx"}]}],
            "direct_stage_results": {"frontend": {"status": "failed", "partialChanges": True,
                "changedFiles": ["frontend/src/Page.tsx"], "summary": "既有类型错误阻塞验证"}},
        }

    def test_typecheck_failure_short_circuits_build_and_restart(self) -> None:
        """tsc 失败必须保存退出码及诊断，不运行 Vite 或重启。"""
        with (
            patch("app.services.direct_validation.shutil.which", return_value="pnpm"),
            patch("app.services.integration_test_runner.workspace_process_registry.run",
                  return_value=subprocess.CompletedProcess([], 1,
                      "src/App.tsx(2,16): error TS2304: Cannot find name 'Ap'.", "")) as command,
            patch("app.services.direct_validation.run_project_restart_validation") as restart,
        ):
            result = validate_direct_fix(self.state)
        self.assertEqual(command.call_args.args[0], ["pnpm", "exec", "tsc", "-b"])
        command.assert_called_once()
        restart.assert_not_called()
        self.assertFalse(result["quality_gate_passed"])
        self.assertEqual(result["status"], "in_progress")
        self.assertEqual(direct_node_process_step("validate_direct_fix", result)["status"], "failed")
        self.assertEqual(_route_direct_validation(result), "direct_modification_repair")
        failed = result["revision_requests"][0]["failed_check"]
        self.assertEqual(failed["execution"]["returncode"], 1)
        self.assertEqual(failed["execution"]["cwd"], "frontend")
        self.assertIn("TS2304", Path(failed["execution"]["stdout_log"]).read_text())
        self.assertFalse(failed.get("advisory", False))
        paths = authorized_direct_repair_paths({**self.state, **result})
        self.assertEqual(paths["frontend"], ["frontend/src/Page.tsx", "frontend/src/App.tsx"])

    def test_vite_failure_after_typecheck_also_enters_repair(self) -> None:
        """tsc 成功后 Vite 打包失败也必须阻断并返修。"""
        with (
            patch("app.services.direct_validation.shutil.which", return_value="pnpm"),
            patch("app.services.integration_test_runner.workspace_process_registry.run", side_effect=[
                subprocess.CompletedProcess([], 0, "", ""),
                subprocess.CompletedProcess([], 1, "", "src/App.tsx:2:16: Build failed"),
            ]) as command,
            patch("app.services.direct_validation.run_project_restart_validation") as restart,
        ):
            result = validate_direct_fix(self.state)
        self.assertEqual(command.call_args.args[0], ["pnpm", "exec", "vite", "build"])
        self.assertEqual(command.call_count, 2)
        restart.assert_not_called()
        self.assertEqual(result["revision_requests"][0]["failed_check"]["id"], "frontend_build")
        self.assertEqual(_route_direct_validation(result), "direct_modification_repair")

    def test_success_requires_both_commands_before_restart(self) -> None:
        """只有 tsc、Vite、重启全部通过才能通过最终门禁。"""
        order: list[str] = []

        def command(argv: list[str], **_kwargs: object) -> subprocess.CompletedProcess[str]:
            """记录真实调度顺序并模拟成功命令。"""
            order.append(argv[-2])
            return subprocess.CompletedProcess(argv, 0, "", "")

        def restart(_state: dict) -> dict:
            """确认重启发生在两条构建命令之后。"""
            order.append("restart")
            return {"test_results": [{"id": "frontend_project_restart", "layer": "frontend",
                "name": "frontend 项目重启验收", "passed": True, "evidence": "ready"}]}

        with (
            patch("app.services.direct_validation.shutil.which", return_value="pnpm"),
            patch("app.services.integration_test_runner.workspace_process_registry.run", side_effect=command),
            patch("app.services.direct_validation.run_project_restart_validation", side_effect=restart),
        ):
            result = validate_direct_fix(self.state)
        self.assertEqual(order, ["tsc", "vite", "restart"])
        self.assertTrue(result["quality_gate_passed"])
        self.assertEqual(len(result["test_results"]), 3)

    def test_backend_only_change_does_not_run_frontend_commands(self) -> None:
        """后端二次修改保持原重启验收，不新增前端构建。"""
        with (
            patch("app.services.integration_test_runner.workspace_process_registry.run") as command,
            patch("app.services.direct_validation.run_project_restart_validation",
                  return_value={"test_results": []}) as restart,
        ):
            run_direct_fix_validation(self.state, affected_layers={"backend"})
        command.assert_not_called()
        restart.assert_called_once()

    def test_missing_package_or_manager_blocks_acceptance(self) -> None:
        """缺少工程或命令时必须明确失败，不能只靠服务就绪判为成功。"""
        with (
            patch("app.services.direct_validation.shutil.which", return_value=None),
            patch("app.services.direct_validation.run_project_restart_validation") as restart,
        ):
            result = validate_direct_fix(self.state)
        self.assertFalse(result["quality_gate_passed"])
        restart.assert_not_called()

    def test_diagnostics_cannot_authorize_missing_dependency_or_external_files(self) -> None:
        """诊断只能授权真实源码，拒绝依赖目录、目录逃逸和不存在的文件。"""
        dependency = self.frontend / "node_modules" / "bad.ts"
        dependency.parent.mkdir()
        dependency.write_text("bad", encoding="utf-8")
        execution = {"cwd": "frontend", "returncode": 1, "stdout_tail": "\n".join([
            "src/App.tsx(2,16): error TS2304: Cannot find name 'Ap'.",
            "src/Missing.tsx(1,1): error TS2304: missing.",
            "node_modules/bad.ts(1,1): error TS2304: bad.",
            "../outside.ts(1,1): error TS2304: bad.",
        ])}
        state = {**self.state, "revision_requests": [{"failed_check": {
            "id": "frontend_typecheck", "passed": False, "execution": execution,
        }}]}
        paths = authorized_direct_repair_paths(state)
        self.assertEqual(paths["frontend"], ["frontend/src/Page.tsx", "frontend/src/App.tsx"])
        self.assertEqual(paths["backend"], [])

    def test_vite_importer_without_line_number_is_authorized(self) -> None:
        """Vite 的无行列号 importer 诊断也能定位已有源码。"""
        state = {**self.state, "revision_requests": [{"failed_check": {
            "id": "frontend_build", "passed": False, "execution": {
                "cwd": "frontend", "returncode": 1,
                "stderr_tail": 'Could not resolve "./Missing.css" from "src/App.tsx"',
            },
        }}]}
        paths = authorized_direct_repair_paths(state)
        self.assertEqual(paths["frontend"], ["frontend/src/Page.tsx", "frontend/src/App.tsx"])

    def test_restart_failure_is_not_mistaken_for_build_success(self) -> None:
        """即使编译打包通过，重启失败仍必须让整轮验收失败。"""
        with (
            patch("app.services.direct_validation.shutil.which", return_value="pnpm"),
            patch("app.services.integration_test_runner.workspace_process_registry.run",
                  return_value=subprocess.CompletedProcess([], 0, "", "")),
            patch("app.services.direct_validation.run_project_restart_validation", return_value={
                "test_results": [{"id": "frontend_project_restart", "layer": "frontend",
                    "name": "frontend 项目重启验收", "passed": False, "repairable": False,
                    "evidence": "启动失败", "startup": {}}],
            }),
        ):
            result = validate_direct_fix(self.state)
        self.assertFalse(result["quality_gate_passed"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(_route_direct_validation(result), "finalize")

    def test_exhausted_budget_keeps_compile_failure_terminal(self) -> None:
        """修复额度耗尽后保留真实失败，不得再显示任务完成。"""
        with (
            patch("app.services.direct_validation.shutil.which", return_value="pnpm"),
            patch("app.services.integration_test_runner.workspace_process_registry.run",
                  return_value=subprocess.CompletedProcess([], 1, "TS2304", "")),
        ):
            result = validate_direct_fix({**self.state, "repair_iteration": 3})
        final = finalize_direct_modification({**self.state, **result, "launch_result": {"status": "running"}})
        self.assertEqual(_route_direct_validation(result), "finalize")
        self.assertEqual(final["status"], "failed")
        self.assertFalse(final["direct_modification_result"]["tests"]["passed"])

    def test_real_tsc_failure_repairs_preexisting_app_then_passes_vite(self) -> None:
        """真实运行 tsc/Vite，复现预先改坏 App 后由 SmallTask 修复并复验。"""
        self._prepare_real_frontend()
        packets: list[dict] = []

        def plan(**kwargs: object) -> dict:
            """复用正式修复任务编译器，保留诊断和精确范围。"""
            result = create_repair_task_plan(
                revision_requests=kwargs["revision_requests"],
                scoped_tasks=kwargs["scoped_tasks"],
                build_execution_scope=kwargs["build_execution_scope"],
                repair_attempt=kwargs["repair_attempt"],
                agent_note="修复编译诊断中的未定义导出符号。",
            )
            return {**result, "strategy": "修复编译诊断中的未定义导出符号。"}

        def repair(*, packet: dict, **_kwargs: object) -> str:
            """替代模型输出，实际 SmallTask 调度、任务包与差异归属保持生产实现。"""
            packets.append(packet)
            self.app.write_text('const App = "test";\nexport default App;\n', encoding="utf-8")
            # Agent 在自己的修复批次内执行 tsc，会刷新前一次失败留下的增量缓存。
            checked = subprocess.run(
                ["pnpm", "exec", "tsc", "-b"], cwd=self.frontend,
                capture_output=True, text=True, timeout=30, check=False,
            )
            self.assertEqual(checked.returncode, 0, checked.stdout + checked.stderr)
            return json.dumps({"status": "completed", "summary": "修复 App 导出拼写错误。",
                               "changedFiles": ["frontend/src/App.tsx"], "verification": []})

        with patch("app.services.direct_validation.run_project_restart_validation", return_value={
            "test_results": [{"id": "frontend_project_restart", "name": "frontend 项目重启验收",
                              "layer": "frontend", "passed": True, "evidence": "ready"}],
        }) as restart:
            failed = validate_direct_fix(self.state)
            restart.assert_not_called()
            self.assertIn("TS2304", failed["test_results"][0]["evidence"])
            cache = self.frontend / "tsconfig.tsbuildinfo"
            self.assertTrue(cache.is_file())
            failed_cache = cache.read_bytes()
            self.assertEqual(_route_direct_validation(failed), "direct_modification_repair")
            state = {**self.state, **failed}
            with (
                patch("app.graph.nodes.direct_repair.plan_repairs_with_repair_planner_agent", side_effect=plan),
                patch("app.services.small_task.invoke_small_task_agent", side_effect=repair),
            ):
                repaired = direct_modification_repair(state)
            self.assertEqual(repaired["status"], "in_progress", repaired.get("message"))
            self.assertNotEqual(cache.read_bytes(), failed_cache)
            self.assertEqual(repaired["repair_iteration"], 1)
            self.assertIn("frontend/src/App.tsx", packets[0]["allowedPaths"])
            self.assertEqual(packets[0]["candidateFiles"][0], "frontend/src/App.tsx")
            self.assertTrue(packets[0]["repairStrategy"])
            self.assertEqual(repaired["small_task_results"][0]["changedFiles"], ["frontend/src/App.tsx"])
            state.update(repaired)
            verified = validate_direct_fix(state)
            self.assertTrue(verified["quality_gate_passed"], verified["test_results"])
            self.assertEqual([check["id"] for check in verified["test_results"]],
                             ["frontend_typecheck", "frontend_build", "frontend_project_restart"])
            restart.assert_called_once()
        final = finalize_direct_modification({**state, **verified})
        self.assertEqual(final["status"], "completed")
        self.assertTrue((self.frontend / "dist" / "index.html").is_file())

    def test_real_vite_resolution_failure_targets_existing_importer(self) -> None:
        """真实 Vite 缺失样式导入的错误必须返修其已有源码 importer。"""
        self._prepare_real_frontend()
        self.app.write_text('import "./Missing.css";\nexport default "test";\n', encoding="utf-8")
        with patch("app.services.direct_validation.run_project_restart_validation") as restart:
            failed = validate_direct_fix(self.state)
        restart.assert_not_called()
        self.assertTrue(failed["test_results"][0]["passed"])
        self.assertFalse(failed["test_results"][1]["passed"])
        self.assertIn("Could not resolve", failed["test_results"][1]["evidence"])
        self.assertEqual(_route_direct_validation(failed), "direct_modification_repair")
        paths = authorized_direct_repair_paths({**self.state, **failed})
        self.assertEqual(paths["frontend"], ["frontend/src/Page.tsx", "frontend/src/App.tsx"])

    def _prepare_real_frontend(self) -> None:
        """复用本地已安装的工具创建可运行 tsc/Vite 的最小前端。"""
        dependencies = Path(__file__).resolve().parents[2] / "Frontend" / "node_modules"
        if not shutil.which("pnpm") or not (dependencies / ".bin" / "vite").is_file():
            self.skipTest("真实命令回归需要本地 pnpm 和已安装的前端依赖。")
        (self.frontend / "node_modules").symlink_to(dependencies, target_is_directory=True)
        (self.frontend / "tsconfig.json").write_text(json.dumps({
            "compilerOptions": {"target": "ES2020", "module": "ESNext", "noEmit": True,
                                "strict": True, "skipLibCheck": True, "types": []},
            "include": ["src"],
        }), encoding="utf-8")
        (self.frontend / "index.html").write_text(
            '<html><body><script type="module" src="/src/App.tsx"></script></body></html>',
            encoding="utf-8",
        )


if __name__ == "__main__":
    unittest.main()
