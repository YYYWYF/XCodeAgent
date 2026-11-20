from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    transition_application_lifecycle,
    write_application_lifecycle,
)
from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator
from app.services.workspace_bootstrap.models import TemplatePackageDownload, WorkspaceBootstrapError
from app.services.workspace_bootstrap.service import WorkspaceBootstrapService
from app.services.workspace_bootstrap.template_package import validate_template_package


def _settings() -> SimpleNamespace:
    """构造 Bootstrap 服务运行所需的最小只读设置。"""

    return SimpleNamespace(
        template_engine_base_url="http://engine.invalid",
        template_engine_connect_timeout_seconds=1,
        template_engine_read_timeout_seconds=1,
        template_package_max_bytes=1024 * 1024,
        template_package_max_files=100,
        template_package_max_extracted_bytes=1024 * 1024,
    )


def _prepare_generating_workspace(workspace: Path) -> None:
    """准备已确认规划且已进入模板生成阶段的最小工作区。"""

    specs = workspace / ".devagentstudio/specs"
    plans = workspace / ".devagentstudio/plans"
    specs.mkdir(parents=True)
    plans.mkdir(parents=True)
    for path, payload in (
        (
            workspace / ".devagentstudio/application.json",
            {
                "schemaVersion": 6,
                "configRevision": 1,
                "appName": "任务中心",
                "appIcon": "AppstoreOutlined",
                "theme": {"primaryColor": "#6b3cf0"},
                "auth": {"enable": False},
                "authorization": {"enabled": False, "initialAdministratorSubjects": []},
            },
        ),
        (specs / "requirement-spec.json", {"confirmation_status": "confirmed"}),
        (plans / "product-plan.json", {"confirmation_status": "confirmed"}),
        (specs / "ui-designs.json", {"confirmation_status": "confirmed"}),
        (
            plans / "technical-plan.json",
            {
                "confirmation_status": "confirmed",
                "artifact_type": "technical-plan",
                "sourceConfigRevision": 1,
                "authorization_manifest": {"enabled": False},
                "template_capabilities": {},
            },
        ),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
    for stage in (
        ApplicationLifecycleStage.ANALYZING_REQUIREMENT,
        ApplicationLifecycleStage.GENERATING_REQUIREMENT_DOCUMENT,
        ApplicationLifecycleStage.AWAITING_REQUIREMENT_DOCUMENT_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_UI_DESIGNS,
        ApplicationLifecycleStage.AWAITING_UI_DESIGN_CONFIRMATION,
        ApplicationLifecycleStage.AWAITING_PLANNING_STAGE_ENTRY,
        ApplicationLifecycleStage.GENERATING_TECHNICAL_PLAN,
        ApplicationLifecycleStage.AWAITING_TECHNICAL_PLAN_CONFIRMATION,
        ApplicationLifecycleStage.GENERATING_APPLICATION_TEMPLATE_FILES,
    ):
        state = transition_application_lifecycle(state, stage=stage, status=ApplicationLifecycleStatus.RUNNING)
    write_application_lifecycle(workspace, state)


def _write_package(path: Path, *, include_application: bool) -> None:
    """构造最小 Engine ZIP；可故意省略 Spring Boot 入口触发 Readiness。"""

    state = {
        "schemaVersion": 3,
        "templateRevision": "2026.10.17.1",
        "requested": {},
        "effective": {},
        "installedArtifacts": {},
        "managedContributions": {},
        "managedConfiguration": {
            "appName": "任务中心",
            "appIcon": "AppstoreOutlined",
            "primaryColor": "#6b3cf0",
        },
    }
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("frontend/package.json", "{}\n")
        package.writestr(
            "frontend/.env",
            'PUBLIC_APP_NAME="任务中心"\nPUBLIC_APP_ICON="AppstoreOutlined"\nPUBLIC_THEME_PRIMARY_COLOR="#6b3cf0"\n',
        )
        package.writestr("backend/pom.xml", "<project />\n")
        if include_application:
            package.writestr("backend/src/main/java/demo/DemoApplication.java", "class DemoApplication {}\n")
        package.writestr(".devagentstudio/template-state.json", json.dumps(state))


class WorkspaceBootstrapServiceTests(unittest.TestCase):
    """验证服务把 Readiness 失败限定在物化事务并投影为不可恢复失败。"""

    def test_locked_download_does_not_change_ready_or_skip_coordinator_release(self) -> None:
        """模拟下载包被占用，READY 仍成功且协调器确实释放。"""

        from app.services.workspace_bootstrap.coordinator import template_mutation_coordinator

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            _prepare_generating_workspace(workspace)
            package = Path(directory) / "engine.zip"
            _write_package(package, include_application=True)
            download = TemplatePackageDownload(package, "ignored", package.stat().st_size, "application/zip")
            original_unlink = Path.unlink

            def unlink(path: Path, *args, **kwargs):
                """只阻止下载包清理，不干扰物化和日志文件。"""

                if path == package:
                    raise PermissionError("download is locked")
                return original_unlink(path, *args, **kwargs)

            with (
                patch("app.services.workspace_bootstrap.service.TemplateEngineClient.generate", new=AsyncMock(return_value=download)),
                patch("app.services.workspace_bootstrap.service.push_workspace_branch", return_value={"status": "skipped"}),
                patch.object(Path, "unlink", new=unlink),
                patch.object(template_mutation_coordinator, "finish", wraps=template_mutation_coordinator.finish) as finish,
            ):
                result = asyncio.run(WorkspaceBootstrapService(_settings())._run(workspace))
            self.assertIn("lifecycle", result)
            finish.assert_called_once_with(workspace)
            self.assertTrue(package.exists())

    def test_success_keeps_template_projected_environment_in_first_git_baseline(self) -> None:
        """首次生成必须原样提交模板服务投影的环境配置。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory) / "workspace"
            workspace.mkdir()
            _prepare_generating_workspace(workspace)
            package = Path(directory) / "engine.zip"
            _write_package(package, include_application=True)
            download = TemplatePackageDownload(
                temporary_path=package,
                sha256="ignored",
                size=package.stat().st_size,
                content_type="application/zip",
            )
            service = WorkspaceBootstrapService(_settings())
            with (
                patch(
                    "app.services.workspace_bootstrap.service.TemplateEngineClient.generate",
                    new=AsyncMock(return_value=download),
                ),
                patch(
                    "app.services.workspace_bootstrap.service.push_workspace_branch",
                    return_value={"status": "skipped"},
                ),
            ):
                result = asyncio.run(service._run(workspace))

            generated = (workspace / "frontend/.env").read_text(
                encoding="utf-8"
            )
            self.assertIn('PUBLIC_APP_NAME="任务中心"', generated)
            self.assertIn('PUBLIC_APP_ICON="AppstoreOutlined"', generated)
            self.assertIn('PUBLIC_THEME_PRIMARY_COLOR="#6b3cf0"', generated)
            self.assertIn("lifecycle", result)
            state = json.loads(
                (workspace / ".devagentstudio/template-state.json").read_text(encoding="utf-8")
            )
            self.assertEqual(state["managedConfiguration"]["appName"], "任务中心")
            log_directory = workspace / ".devagentstudio/runtime/template-download"
            self.assertTrue(log_directory.is_dir())
            self.assertEqual([], list(log_directory.glob("*.log")))

            committed = subprocess.run(
                ["git", "show", "HEAD:frontend/.env"],
                cwd=workspace,
                check=True,
                capture_output=True,
                text=True,
            ).stdout
            self.assertEqual(committed, generated)

    def test_readiness_failure_rolls_back_roots_and_marks_lifecycle_failed(self) -> None:
        """缺少后端入口时不得留下模板半成品，且 lifecycle 必须为 FAILED。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_generating_workspace(workspace)
            package = workspace / "engine.zip"
            _write_package(package, include_application=False)
            download = TemplatePackageDownload(
                temporary_path=package,
                sha256="ignored",
                size=package.stat().st_size,
                content_type="application/zip",
            )
            service = WorkspaceBootstrapService(_settings())
            with patch(
                "app.services.workspace_bootstrap.service.TemplateEngineClient.generate",
                new=AsyncMock(return_value=download),
            ):
                with self.assertRaisesRegex(WorkspaceBootstrapError, "Application"):
                    asyncio.run(service._run(workspace))

            lifecycle = load_application_lifecycle(workspace)
            self.assertIsNotNone(lifecycle)
            self.assertEqual(
                lifecycle.initialization.stage,
                ApplicationLifecycleStage.APPLICATION_TEMPLATE_GENERATION_FAILED,
            )
            for relative in ("frontend", "backend", ".git", ".devagentstudio/template-state.json"):
                self.assertFalse((workspace / relative).exists(), relative)
            self.assertTrue((workspace / ".devagentstudio/application.json").is_file())
            self.assertTrue((workspace / ".devagentstudio/application-lifecycle.json").is_file())
            logs = list((workspace / ".devagentstudio/runtime/template-download").glob("*.log"))
            self.assertEqual(1, len(logs))
            log_content = logs[0].read_text(encoding="utf-8")
            self.assertIn('stage="readiness_validation"', log_content)
            self.assertIn('event="stage_failed"', log_content)
            self.assertIn("validate_workspace_bootstrap_readiness", log_content)
            self.assertIn("Traceback (most recent call last)", log_content)

    def test_corrupt_package_failure_retains_validation_diagnostics(self) -> None:
        """损坏 ZIP 必须在模板契约校验阶段留下异常堆栈并保留运行日志。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_generating_workspace(workspace)
            package = workspace / "engine.zip"
            package.write_bytes(b"not a zip archive")
            download = TemplatePackageDownload(
                temporary_path=package,
                sha256="unknown",
                size=package.stat().st_size,
                content_type="application/zip",
            )
            service = WorkspaceBootstrapService(_settings())
            with patch(
                "app.services.workspace_bootstrap.service.TemplateEngineClient.generate",
                new=AsyncMock(return_value=download),
            ):
                with self.assertRaisesRegex(WorkspaceBootstrapError, "损坏"):
                    asyncio.run(service._run(workspace))

            logs = list((workspace / ".devagentstudio/runtime/template-download").glob("*.log"))
            self.assertEqual(1, len(logs))
            content = logs[0].read_text(encoding="utf-8")
            self.assertIn('stage="template_package_validation"', content)
            self.assertIn("BadZipFile: File is not a zip file", content)
            self.assertIn("validate_template_package", content)

    def test_cancel_waits_for_archive_reader_before_cleanup(self) -> None:
        """校验或物化仍打开 ZIP 时，连续取消不能提前删文件或释放协调器。"""
        for stage in ("validation", "materialization"):
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as directory:
                workspace = Path(directory)
                _prepare_generating_workspace(workspace)
                package = workspace / "engine.zip"
                _write_package(package, include_application=True)
                download = TemplatePackageDownload(package, "ignored", package.stat().st_size, "application/zip")
                asyncio.run(self._assert_cancel_drains_reader(workspace, download, stage))
                self.assertFalse(package.exists())
                self.assertFalse(template_mutation_coordinator.has_active_mutation(workspace))

    async def _assert_cancel_drains_reader(
        self, workspace: Path, download: TemplatePackageDownload, stage: str,
    ) -> None:
        """以同步读取线程构造可确定的取消时序。"""
        loop = asyncio.get_running_loop()
        started = asyncio.Event()
        finish = threading.Event()
        bytes_read: list[bytes] = []

        def read_archive(*args, **kwargs):
            """持有打开的文件，收到放行后再读取最后的数据。"""
            with download.temporary_path.open("rb") as archive:
                loop.call_soon_threadsafe(started.set)
                if not finish.wait(5):
                    raise TimeoutError("测试未放行 ZIP 读取线程")
                bytes_read.append(archive.read(2))
            if stage == "validation":
                return validate_template_package(*args, **kwargs)
            raise RuntimeError("物化线程退出时的错误不能覆盖取消")

        stub = (
            "app.services.workspace_bootstrap.service.validate_template_package"
            if stage == "validation"
            else "app.services.workspace_bootstrap.service.WorkspaceMaterializer.materialize"
        )
        with (
            patch("app.services.workspace_bootstrap.service.TemplateEngineClient.generate", new=AsyncMock(return_value=download)),
            patch(stub, side_effect=read_archive),
        ):
            service = WorkspaceBootstrapService(_settings())
            task = asyncio.create_task(service._run(workspace))
            try:
                await asyncio.wait_for(started.wait(), timeout=5)
                for _ in range(2):
                    task.cancel()
                    await asyncio.sleep(0)
                    self.assertFalse(task.done())
                    self.assertTrue(download.temporary_path.exists())
                    self.assertTrue(template_mutation_coordinator.has_active_mutation(workspace))
            finally:
                finish.set()
                if not task.done():
                    task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
        self.assertEqual(bytes_read, [b"PK"])

    def test_non_generating_lifecycle_rejects_before_calling_engine(self) -> None:
        """READY 等非生成阶段必须在网络调用前被 lifecycle gate 拒绝。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            state = create_application_lifecycle(application_id="app-1", application_name="任务中心")
            write_application_lifecycle(workspace, state)
            service = WorkspaceBootstrapService(_settings())
            generate = AsyncMock()
            with patch(
                "app.services.workspace_bootstrap.service.TemplateEngineClient.generate",
                new=generate,
            ):
                for _ in range(2):
                    with self.assertRaisesRegex(Exception, "只有用户确认 TechnicalPlan"):
                        asyncio.run(service._run(workspace))

            generate.assert_not_awaited()
            logs = list((workspace / ".devagentstudio/runtime/template-download").glob("*.log"))
            self.assertEqual(2, len(logs))
            self.assertEqual(2, len({path.stem for path in logs}))
            for path in logs:
                self.assertIn('stage="lifecycle_precondition"', path.read_text(encoding="utf-8"))
