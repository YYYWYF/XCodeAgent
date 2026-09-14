"""独立预览维护的 AG-UI、互斥、日志和确认回归。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import patch

from app.protocols.preview_runtime import build_preview_runtime_stream, blocking_task
from app.services.preview_runtime_guard import claim_maintenance, maintenance_owner, release_maintenance, require_no_maintenance
from app.services.preview_runtime_repair import execute_repair, load_repair, prepare_repair, save_repair, source_digest, safe_file
from app.services.preview_runtime_state import (
    begin_attempt,
    finish_attempt,
    read_logs,
    read_record,
    record_progress,
    redact,
    runtime_root,
    runtime_snapshot,
)


class PreviewRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """用真实流编码和临时工作区验证产品边界，不调用模型或启动真实进程。"""

    def setUp(self) -> None:
        """创建当前契约的最小应用工作区。"""
        self.temp = tempfile.TemporaryDirectory()
        self.workspace = self.temp.name
        root = Path(self.workspace)
        (root / ".xcodeagent").mkdir()
        (root / ".xcodeagent/application.json").write_text("{}")
        (root / "frontend").mkdir()
        (root / "frontend/main.ts").write_text("broken")
        self.thread = "preview-test"

    def tearDown(self) -> None:
        """释放维护占用和临时文件。"""
        owner = maintenance_owner(self.workspace)
        if owner:
            release_maintenance(self.workspace, owner["threadId"])
        self.temp.cleanup()

    async def request(self, action: str, **fields: str) -> list[dict]:
        """消费实际 AG-UI 流，验证所有业务结果均有完整终态。"""
        payload = {"threadId": self.thread, "runId": "preview-test-run", "forwardedProps": {"previewRuntime": {"workspace": self.workspace, "action": action, **fields}}}
        events = []
        async for frame in build_preview_runtime_stream(payload=payload):
            for line in frame.splitlines():
                if line.startswith("data: "):
                    events.append(json.loads(line[6:]))
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertIn("TEXT_MESSAGE_END", [event["type"] for event in events])
        self.assertIn("STATE_SNAPSHOT", [event["type"] for event in events])
        return events

    def result(self, events: list[dict]) -> dict:
        """提取最终业务快照。"""
        return events[-1]["result"]["previewRuntime"]

    def fail_launch(self) -> str:
        """记录一轮真实身份的失败启动。"""
        attempt = begin_attempt(self.workspace)
        finish_attempt(self.workspace, {"status": "failed", "failed_stage": "frontend_start", "frontend": {"status": "failed", "message": "编译错误"}})
        return attempt

    async def test_get_has_complete_lifecycle(self) -> None:
        """未启动的项目也有有效状态，读取不占用维护锁。"""
        value = self.result(await self.request("get"))
        self.assertEqual(value["runtime"]["status"], "stopped")
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_error_has_complete_lifecycle(self) -> None:
        """无启动失败证据时不能诊断，但必须正常结束 AG-UI。"""
        value = self.result(await self.request("diagnose"))
        self.assertEqual(value["status"], "failed")
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_waiting_execution_blocks_restart(self) -> None:
        """其他阶段等待确认时阻止重启，并允许读取状态。"""
        lifecycle = SimpleNamespace(active_executions={"task": SimpleNamespace(thread_id="other", run_id="other-run", status="awaiting_user")})
        with patch("app.protocols.preview_runtime.load_application_lifecycle", return_value=lifecycle), patch("app.protocols.preview_runtime.launch_project_preview") as launch:
            self.assertEqual(self.result(await self.request("restart"))["status"], "failed")
            value = self.result(await self.request("get"))
        launch.assert_not_called()
        self.assertEqual(value["blockedBy"]["threadId"], "other")

    async def test_stale_attempt_cannot_diagnose(self) -> None:
        """拒绝用上一轮失败身份诊断新的代码。"""
        self.fail_launch()
        with patch("app.protocols.preview_runtime.prepare_repair") as prepare:
            self.assertEqual(self.result(await self.request("diagnose", attemptId="old"))["status"], "failed")
        prepare.assert_not_called()

    async def test_diagnosis_confirmation_cancel(self) -> None:
        """诊断产生确认卡并保留占用，停止后释放而不执行修复。"""
        attempt = self.fail_launch()
        repair = {"status": "awaiting_confirmation", "planId": "p", "attemptId": attempt, "iteration": 0, "markdown": "# 修复计划"}
        with patch("app.protocols.preview_runtime.prepare_repair", return_value=repair):
            value = self.result(await self.request("diagnose", attemptId=attempt))
        self.assertEqual(value["repair"]["status"], "awaiting_confirmation")
        self.assertIsNotNone(maintenance_owner(self.workspace))
        value = self.result(await self.request("cancel"))
        self.assertEqual(value["repair"]["status"], "stopped")
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_changed_source_rejects_confirmation(self) -> None:
        """待确认文件被修改后不能继续执行原计划。"""
        attempt = self.fail_launch()
        repair = {"status": "awaiting_confirmation", "planId": "p", "attemptId": attempt, "paths": ["frontend/main.ts"], "digest": source_digest(self.workspace, ["frontend/main.ts"]), "iteration": 0, "markdown": "# 修复计划"}
        save_repair(self.workspace, self.thread, repair)
        claim_maintenance(self.workspace, self.thread, "diagnose")
        (Path(self.workspace) / "frontend/main.ts").write_text("new user edit")
        with patch("app.protocols.preview_runtime.execute_repair") as execute:
            value = self.result(await self.request("confirm", planId="p"))
        execute.assert_not_called()
        self.assertEqual(value["status"], "failed")
        self.assertEqual((Path(self.workspace) / "frontend/main.ts").read_text(), "new user edit")

    async def test_confirmation_charges_budget_even_when_worker_fails(self) -> None:
        """实际派发前记录次数，异常不能绕过三轮预算。"""
        attempt = self.fail_launch()
        paths = ["frontend/main.ts"]
        save_repair(self.workspace, self.thread, {"status": "awaiting_confirmation", "planId": "p", "attemptId": attempt, "paths": paths, "digest": source_digest(self.workspace, paths), "iteration": 1, "markdown": "# 修复计划"})
        with patch("app.protocols.preview_runtime.execute_repair", side_effect=RuntimeError("worker failed")):
            await self.request("confirm", planId="p")
        self.assertEqual(load_repair(self.workspace, self.thread)["iteration"], 2)
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_read_during_other_maintenance(self) -> None:
        """另一个维护占用时仍允许查看服务和日志。"""
        claim_maintenance(self.workspace, "other", "restart")
        self.assertEqual(self.result(await self.request("get"))["status"], "completed")
        self.assertEqual(self.result(await self.request("restart"))["status"], "failed")

    def test_reverse_guard_and_workspace_isolation(self) -> None:
        """普通任务启动受维护栅栏约束，不影响其他应用。"""
        claim_maintenance(self.workspace, self.thread, "diagnose")
        with self.assertRaises(RuntimeError):
            require_no_maintenance(self.workspace)
        require_no_maintenance(self.workspace + "-other")

    def test_new_attempt_clears_old_logs(self) -> None:
        """本轮没有前端输出时不能把上轮错误交给模型。"""
        first = self.fail_launch()
        log = runtime_root(self.workspace) / "frontend.stderr.log"
        log.write_text("old failure")
        second = begin_attempt(self.workspace)
        self.assertNotEqual(first, second)
        self.assertEqual(read_logs(self.workspace)["frontend"], [])

    def test_logs_are_bounded_redacted_and_reject_symlinks(self) -> None:
        """只读取白名单日志尾部，禁止沿符号链接暴露其他文件。"""
        self.fail_launch()
        root = runtime_root(self.workspace)
        (root / "frontend.stdout.log").write_text("x" * 40000 + " password=secret-value https://user:private@localhost")
        (root / "frontend.stderr.log").symlink_to(Path(self.workspace) / "frontend/main.ts")
        logs = read_logs(self.workspace)["frontend"]
        self.assertEqual(len(logs), 1)
        self.assertTrue(logs[0]["truncated"])
        self.assertNotIn("secret-value", logs[0]["content"])
        self.assertNotIn(":private@", logs[0]["content"])

    def test_dead_process_is_not_healthy(self) -> None:
        """历史成功的已退出进程必须显示失败。"""
        begin_attempt(self.workspace)
        finish_attempt(self.workspace, {"status": "running", "frontend": {"status": "running", "server": {"pid": 12345}}, "backend": {"status": "skipped"}})
        with patch("app.services.preview_runtime_state.os.kill", side_effect=ProcessLookupError):
            value = runtime_snapshot(self.workspace)
        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["backend"]["status"], "skipped")
        self.assertTrue(value["repairAvailable"])

    def test_completed_progress_waits_for_final_runtime_evidence(self) -> None:
        """后端完成进度在 PID 落盘前保持启动中，避免重启期间短暂显示失败。"""
        begin_attempt(self.workspace)

        record_progress(self.workspace, "backend", "completed", "后端服务已就绪。")
        value = runtime_snapshot(self.workspace, logs=False)

        self.assertEqual(value["status"], "starting")
        self.assertEqual(value["backend"]["status"], "starting")
        self.assertNotEqual(value.get("failedStage"), "backend_process")

    def test_live_vite_process_uses_current_ready_log_when_http_probe_is_unavailable(self) -> None:
        """HTTP 探测受限时，本轮存活 PID 和 Vite 就绪日志不会被误判为启动失败。"""
        import os

        begin_attempt(self.workspace)
        root = runtime_root(self.workspace)
        root.joinpath("frontend.pid").write_text(str(os.getpid()))
        root.joinpath("frontend.stdout.log").write_text(
            "VITE v6.4.3 ready in 341 ms\n➜ Local: http://localhost:3000/\n"
        )
        root.joinpath("frontend.stderr.log").write_text("")
        finish_attempt(
            self.workspace,
            {
                "status": "running",
                "preview_url": "http://localhost:3000",
                "frontend": {
                    "status": "running",
                    "preview_url": "http://localhost:3000",
                    "server": {"pid": os.getpid(), "ready": True},
                },
                "backend": {"status": "skipped"},
            },
        )

        with patch(
            "app.services.frontend_project_launcher._preview_is_ready",
            return_value=False,
        ):
            value = runtime_snapshot(self.workspace)

        self.assertEqual(value["status"], "running")
        self.assertEqual(value["frontend"]["status"], "running")
        self.assertEqual(value["frontend"]["port"], 3000)
        self.assertEqual(value["previewUrl"], "http://localhost:3000")
        self.assertFalse(value["repairAvailable"])

    def test_service_ports_are_projected_from_launch_result_and_backend_logs(self) -> None:
        """前后端状态使用实际端口，旧后端快照可从受控启动日志补齐端口。"""
        begin_attempt(self.workspace)
        root = runtime_root(self.workspace)
        root.joinpath("backend.stdout.log").write_text(
            "Tomcat initialized with port(s): 8080 (http)\n"
        )
        finish_attempt(
            self.workspace,
            {
                "status": "running",
                "preview_url": "http://localhost:3000",
                "frontend": {
                    "status": "stopped",
                    "preview_url": "http://localhost:3000",
                    "server": {},
                },
                "backend": {
                    "status": "stopped",
                    "server": {},
                },
            },
        )

        value = runtime_snapshot(self.workspace)

        self.assertEqual(value["frontend"]["port"], 3000)
        self.assertEqual(value["backend"]["port"], 8080)

    def test_ready_log_does_not_hide_current_frontend_fatal_error(self) -> None:
        """本轮 stderr 已出现致命错误时不能仅凭 Vite 就绪日志保留 running。"""
        import os

        begin_attempt(self.workspace)
        root = runtime_root(self.workspace)
        root.joinpath("frontend.pid").write_text(str(os.getpid()))
        root.joinpath("frontend.stdout.log").write_text("VITE ready in 341 ms\n")
        root.joinpath("frontend.stderr.log").write_text("Cannot find module 'missing-package'\n")
        finish_attempt(
            self.workspace,
            {
                "status": "running",
                "preview_url": "http://localhost:3000",
                "frontend": {
                    "status": "running",
                    "preview_url": "http://localhost:3000",
                    "server": {"pid": os.getpid(), "ready": True},
                },
                "backend": {"status": "skipped"},
            },
        )

        with patch(
            "app.services.frontend_project_launcher._preview_is_ready",
            return_value=False,
        ):
            value = runtime_snapshot(self.workspace)

        self.assertEqual(value["status"], "failed")
        self.assertEqual(value["frontend"]["status"], "failed")
        self.assertTrue(value["repairAvailable"])

    def test_diagnosis_receives_health_calibrated_failure(self) -> None:
        """实时检查产生的失败必须进入诊断证据，不能继续传递历史成功摘要。"""
        import os

        begin_attempt(self.workspace)
        root = runtime_root(self.workspace)
        root.joinpath("frontend.pid").write_text(str(os.getpid()))
        finish_attempt(
            self.workspace,
            {
                "status": "running",
                "preview_url": "http://localhost:3000",
                "frontend": {
                    "status": "running",
                    "preview_url": "http://localhost:3000",
                    "server": {"pid": os.getpid(), "ready": True},
                },
                "backend": {"status": "skipped"},
            },
        )

        with (
            patch(
                "app.services.frontend_project_launcher._preview_is_ready",
                return_value=False,
            ),
            patch(
                "app.agents.repair_planner.plan_build_failure_repair_with_repair_planner_agent",
                return_value={"decision": "manual", "reason": "服务未就绪"},
            ) as planner,
        ):
            result = prepare_repair(self.workspace, self.thread, {})

        failure = planner.call_args.kwargs["repair_input"]["failure"]["launch"]
        self.assertEqual(failure["status"], "failed")
        self.assertEqual(failure["failedStage"], "frontend_process")
        self.assertEqual(result["status"], "failed")

    def test_budget_exhaustion_does_not_call_model(self) -> None:
        """三轮上限后直接停止，不再调用模型。"""
        self.fail_launch()
        with patch("app.agents.repair_planner.plan_build_failure_repair_with_repair_planner_agent") as planner:
            result = prepare_repair(self.workspace, self.thread, {"iteration": 3})
        planner.assert_not_called()
        self.assertEqual(result["status"], "failed")

    def test_failed_validation_does_not_restart(self) -> None:
        """受影响检查失败时不能宣告恢复或启动服务。"""
        paths = ["frontend/main.ts"]
        repair = {"paths": paths, "digest": source_digest(self.workspace, paths), "iteration": 1, "tasks": [{"owner": "frontend"}]}
        with patch("app.services.small_task.execute_small_task_batch", return_value={"results": [{"status": "completed"}]}), patch("app.services.integration_test_runner.run_integration_checks", return_value={"test_results": [{"passed": False}]}), patch("app.services.project_launcher.launch_project_preview") as launch:
            result = execute_repair(self.workspace, repair, lambda *_: None, Event())
        launch.assert_not_called()
        self.assertEqual(result["status"], "retry")

    async def test_concurrent_restart_is_rejected_until_real_worker_exits(self) -> None:
        """并发点击只能产生一个启动器调用，工作未结束不得释放占用。"""
        entered, finish = Event(), Event()

        def launch(*_args: object, **_kwargs: object) -> dict:
            """阻塞模拟启动器，使第二个请求进入竞争窗口。"""
            entered.set()
            finish.wait(5)
            return {"status": "failed", "message": "模拟失败"}

        with patch("app.protocols.preview_runtime.launch_project_preview", side_effect=launch) as launcher:
            first = asyncio.create_task(self.request("restart"))
            await asyncio.to_thread(entered.wait, 3)
            try:
                value = self.result(await self.request("restart"))
                self.assertEqual(value["status"], "failed")
                self.assertIsNotNone(maintenance_owner(self.workspace))
                with self.assertRaises(RuntimeError):
                    from app.protocols.workflow.run_control import workflow_run_registry
                    workflow_run_registry.register("ordinary", asyncio.current_task(), workspace=self.workspace)
            finally:
                finish.set()
                await first
        self.assertEqual(launcher.call_count, 1)
        self.assertIsNone(maintenance_owner(self.workspace))

    async def test_preview_does_not_create_lifecycle_or_test_report(self) -> None:
        """服务读取和失败诊断不写入开发完成计数或测试阶段报告。"""
        await self.request("get")
        await self.request("diagnose", attemptId="invalid")
        root = Path(self.workspace) / ".xcodeagent"
        self.assertFalse((root / "application-lifecycle.json").exists())
        self.assertFalse((root / "test-report.json").exists())

    def test_log_redaction_handles_json_and_xml_credentials(self) -> None:
        """常见配置日志的引号和 XML 格式也必须隐藏凭据。"""
        value = redact('{"password":"private-value"} <token>secret-token</token>')
        self.assertNotIn("private-value", value)
        self.assertNotIn("secret-token", value)

    def test_repair_paths_are_exact_and_cannot_escape(self) -> None:
        """拒绝模型输出通配符、敏感路径和工作区外的符号链接。"""
        for path in ("frontend/**", "frontend/.npmrc", "frontend/.env", "backend/../../outside", "frontend"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                safe_file(self.workspace, path)
        (Path(self.workspace) / "frontend/outside").symlink_to("/tmp")
        with self.assertRaises(ValueError):
            safe_file(self.workspace, "frontend/outside/file.ts")

    async def test_successful_repair_and_duplicate_confirmation(self) -> None:
        """贯穿诊断、精确计划确认、修改、复测和启动，旧确认不可覆盖成功结果。"""
        import os
        attempt = self.fail_launch()
        plan = {"decision": "repair", "strategy": "修复编译错误", "repair_tasks": [{"title": "修复入口", "description": "修正语法", "change_scope": [{"path": "frontend/main.ts", "operation": "modify"}]}]}
        with patch("app.agents.repair_planner.plan_build_failure_repair_with_repair_planner_agent", return_value=plan):
            prepared = self.result(await self.request("diagnose", attemptId=attempt))
        self.assertEqual((Path(self.workspace) / "frontend/main.ts").read_text(), "broken")

        def execute(**_kwargs: object) -> dict:
            """只在确认后模拟受限任务真正修改目标文件。"""
            (Path(self.workspace) / "frontend/main.ts").write_text("fixed")
            return {"results": [{"status": "completed"}], "codeChangeSets": []}

        def launch(*_args: object, **_kwargs: object) -> dict:
            """记录可被实际运行快照验证的启动结果。"""
            runtime_root(self.workspace).joinpath("frontend.pid").write_text(str(os.getpid()))
            result = {"status": "running", "preview_url": "http://localhost:3000", "frontend": {"status": "running", "preview_url": "http://localhost:3000", "server": {"pid": os.getpid(), "ready": True}}, "backend": {"status": "skipped"}}
            finish_attempt(self.workspace, result)
            return result

        with patch("app.services.small_task.execute_small_task_batch", side_effect=execute), patch("app.services.integration_test_runner.run_integration_checks", return_value={"test_results": [{"passed": True}]}), patch("app.services.project_launcher.launch_project_preview", side_effect=launch), patch("app.services.frontend_project_launcher._preview_is_ready", return_value=True):
            result = self.result(await self.request("confirm", planId=prepared["repair"]["planId"]))
        self.assertEqual(result["repair"]["status"], "completed")
        self.assertEqual(result["repair"]["iteration"], 1)
        self.assertEqual(result["runtime"]["previewUrl"], "http://localhost:3000")
        self.assertIsNone(maintenance_owner(self.workspace))
        replay = self.result(await self.request("confirm", planId=prepared["repair"]["planId"]))
        self.assertEqual(replay["status"], "failed")
        self.assertEqual(load_repair(self.workspace, self.thread)["status"], "completed")


if __name__ == "__main__":
    unittest.main()
