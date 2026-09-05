from __future__ import annotations

import asyncio
import json
import tempfile
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
from app.services.workspace_bootstrap.models import TemplatePackageDownload, WorkspaceBootstrapError
from app.services.workspace_bootstrap.service import WorkspaceBootstrapService


def _settings() -> SimpleNamespace:
    """构造 Bootstrap 服务运行所需的最小只读设置。"""

    return SimpleNamespace(
        template_engine_base_url="http://engine.invalid",
        template_engine_token="test-token",
        template_engine_connect_timeout_seconds=1,
        template_engine_read_timeout_seconds=1,
        template_package_max_bytes=1024 * 1024,
        template_package_max_files=100,
        template_package_max_extracted_bytes=1024 * 1024,
    )


def _prepare_generating_workspace(workspace: Path) -> None:
    """准备已确认规划且已进入模板生成阶段的最小工作区。"""

    specs = workspace / ".xcodeagent/specs"
    plans = workspace / ".xcodeagent/plans"
    specs.mkdir(parents=True)
    plans.mkdir(parents=True)
    for path, payload in (
        (workspace / ".xcodeagent/application.json", {"auth": {"enable": False}, "authorization": {"enabled": False}}),
        (specs / "requirement-spec.json", {"confirmation_status": "confirmed"}),
        (plans / "product-plan.json", {"confirmation_status": "confirmed"}),
        (specs / "ui-designs.json", {"confirmation_status": "confirmed"}),
        (
            plans / "technical-plan.json",
            {
                "confirmation_status": "confirmed",
                "artifact_type": "technical-plan",
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
        "schemaVersion": 2,
        "templateRevision": "r1",
        "requested": {},
        "effective": {},
        "appliedAdditions": {},
    }
    with zipfile.ZipFile(path, "w") as package:
        package.writestr("frontend/package.json", "{}\n")
        package.writestr("backend/pom.xml", "<project />\n")
        if include_application:
            package.writestr("backend/src/main/java/demo/DemoApplication.java", "class DemoApplication {}\n")
        package.writestr(".xcodeagent/template-state.json", json.dumps(state))


class WorkspaceBootstrapServiceTests(unittest.TestCase):
    """验证服务把 Readiness 失败限定在物化事务并投影为不可恢复失败。"""

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
            for relative in ("frontend", "backend", ".git", ".xcodeagent/template-state.json"):
                self.assertFalse((workspace / relative).exists(), relative)
            self.assertTrue((workspace / ".xcodeagent/application.json").is_file())
            self.assertTrue((workspace / ".xcodeagent/application-lifecycle.json").is_file())

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
                with self.assertRaisesRegex(Exception, "只有用户确认 TechnicalPlan"):
                    asyncio.run(service._run(workspace))

            generate.assert_not_awaited()
