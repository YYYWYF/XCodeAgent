"""Bootstrap 在已物化工作区上必须沿用现有工程，不重新拉取模板。

关键前提：迭代期间 `.xcodeagent` 状态文件本就是脏的，因此"已物化"的判据
不能要求 Git 工作树干净——否则每次迭代都会误判为未物化并覆盖用户累积的代码。
"""

from __future__ import annotations

import asyncio
import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.config import Settings
from app.domain.application_lifecycle import (
    ApplicationLifecycleStage,
    ApplicationLifecycleStatus,
)
from app.services.application_lifecycle import (
    create_application_lifecycle,
    load_application_lifecycle,
    transition_application_lifecycle,
    write_application_lifecycle,
)
from app.services.template_state import TEMPLATE_STATE_RELATIVE_PATH
from app.services.workspace_bootstrap.models import WorkspaceBootstrapError
from app.services.workspace_bootstrap.readiness import (
    WorkspaceTemplateStatus,
    classify_workspace_template,
)
from app.services.workspace_bootstrap.service import WorkspaceBootstrapService

_REQUESTED_CONFIG: dict[str, object] = {"capabilities": {}}


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def _materialized_workspace(workspace: Path, *, requested: dict[str, object] | None = None) -> None:
    """构造一个已物化且就绪条件齐备的工作区（含 Git baseline）。"""

    xcodeagent_dir = workspace / ".xcodeagent"
    # 正式产物：就绪校验要求 RequirementSpec/ProductPlan/UiDesign/TechnicalPlan 均已确认。
    _write_json(
        xcodeagent_dir / "specs" / "requirement-spec.json",
        {"confirmation_status": "confirmed"},
    )
    _write_json(
        xcodeagent_dir / "plans" / "product-plan.json",
        {"confirmation_status": "confirmed"},
    )
    _write_json(
        xcodeagent_dir / "specs" / "ui-designs.json",
        {"confirmation_status": "confirmed"},
    )
    _write_json(
        xcodeagent_dir / "plans" / "technical-plan.json",
        {"artifact_type": "technical-plan", "confirmation_status": "confirmed"},
    )
    # 受管根与真实入口。
    _write_json(workspace / "frontend" / "package.json", {})
    (workspace / "backend" / "src" / "main" / "java" / "com" / "demo").mkdir(parents=True)
    (workspace / "backend" / "pom.xml").write_text("<project />", encoding="utf-8")
    (workspace / "backend" / "src" / "main" / "java" / "com" / "demo" / "DemoApplication.java").write_text(
        "class DemoApplication {}", encoding="utf-8"
    )
    _write_json(
        workspace / TEMPLATE_STATE_RELATIVE_PATH,
        {
            "schemaVersion": 2,
            "templateRevision": "template-r1",
            "requested": requested if requested is not None else {},
            "effective": {},
            "appliedAdditions": {},
        },
    )
    subprocess.run(["git", "init", "-q"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=workspace, check=True)
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-qm", "baseline"], cwd=workspace, check=True)


def _settings() -> Settings:
    return Settings(model_base_url="http://model", model_api_key="key", model_name="model")


def _dirty(workspace: Path) -> None:
    """制造脏工作树：迭代期间 `.xcodeagent` 状态文件必然处于这种状态。"""

    target = workspace / ".xcodeagent" / "application.json"
    target.write_text('{"id": "app-1"}', encoding="utf-8")


class ClassifyWorkspaceTemplateTests(unittest.TestCase):
    def test_absent_when_workspace_has_no_template_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            self.assertEqual(
                classify_workspace_template(workspace, requested_config=_REQUESTED_CONFIG),
                WorkspaceTemplateStatus.ABSENT,
            )

    def test_ready_when_materialized_and_matching(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _materialized_workspace(workspace)
            self.assertEqual(
                classify_workspace_template(workspace, requested_config=_REQUESTED_CONFIG),
                WorkspaceTemplateStatus.READY,
            )

    def test_ready_even_when_git_worktree_is_dirty(self) -> None:
        """核心前提：迭代期间工作树必然是脏的，脏不等于"未物化"。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _materialized_workspace(workspace)
            _dirty(workspace)
            self.assertTrue(
                subprocess.run(
                    ["git", "status", "--porcelain"],
                    cwd=workspace,
                    capture_output=True,
                    text=True,
                    check=True,
                ).stdout.strip(),
                "前置条件不成立：工作树未变脏",
            )
            self.assertEqual(
                classify_workspace_template(workspace, requested_config=_REQUESTED_CONFIG),
                WorkspaceTemplateStatus.READY,
                "工作树脏时误判为非就绪：会导致每次迭代都重新拉取模板、覆盖用户代码",
            )

    def test_stale_when_requested_capabilities_differ(self) -> None:
        """应用配置变更导致模板请求变化时不得沿用，也不得静默覆盖。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _materialized_workspace(workspace)
            changed = {"capabilities": {"auth": {"enabled": True, "config": {}}}}
            self.assertEqual(
                classify_workspace_template(workspace, requested_config=changed),
                WorkspaceTemplateStatus.STALE,
            )

    def test_stale_when_roots_present_but_state_missing(self) -> None:
        """只有一半工程痕迹（受管根在、state 缺失）属于不一致，不能当未物化去覆盖。"""

        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _materialized_workspace(workspace)
            (workspace / TEMPLATE_STATE_RELATIVE_PATH).unlink()
            self.assertEqual(
                classify_workspace_template(workspace, requested_config=_REQUESTED_CONFIG),
                WorkspaceTemplateStatus.STALE,
            )


class BootstrapReuseTests(unittest.TestCase):
    """已物化工作区上跑 Bootstrap 必须跳过远端下载。"""

    def _generating_workspace(self, workspace: Path) -> None:
        """把 lifecycle 推进到模板生成阶段，模拟 TechnicalPlan 确认后的入口状态。"""

        state = create_application_lifecycle(application_id="app-1", application_name="测试应用")
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
            state = transition_application_lifecycle(
                state, stage=stage, status=ApplicationLifecycleStatus.RUNNING
            )
        write_application_lifecycle(workspace, state)

    def test_reuses_existing_template_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _materialized_workspace(workspace)
            _dirty(workspace)
            self._generating_workspace(workspace)
            service = WorkspaceBootstrapService(_settings())

            with (
                patch(
                    "app.services.workspace_bootstrap.service.compile_template_requested_config",
                    return_value=_REQUESTED_CONFIG,
                ),
                patch(
                    "app.services.workspace_bootstrap.service.TemplateEngineClient"
                ) as engine,
            ):
                result = asyncio.run(service._run(workspace))

            engine.assert_not_called()
            self.assertTrue(result.get("reusedExistingTemplate"))
            lifecycle = load_application_lifecycle(workspace)
            assert lifecycle is not None
            self.assertEqual(
                lifecycle.initialization.stage, ApplicationLifecycleStage.READY_FOR_WORKBENCH
            )

    def test_stale_template_reports_error_without_touching_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            _materialized_workspace(workspace)
            self._generating_workspace(workspace)
            before = (workspace / "frontend" / "package.json").read_text(encoding="utf-8")
            service = WorkspaceBootstrapService(_settings())

            with (
                patch(
                    "app.services.workspace_bootstrap.service.compile_template_requested_config",
                    return_value={"capabilities": {"auth": {"enabled": True, "config": {}}}},
                ),
                patch(
                    "app.services.workspace_bootstrap.service.TemplateEngineClient"
                ) as engine,
            ):
                with self.assertRaises(WorkspaceBootstrapError) as raised:
                    asyncio.run(service._run(workspace))

            engine.assert_not_called()
            self.assertIn("不会重新拉取模板", str(raised.exception))
            self.assertEqual(
                (workspace / "frontend" / "package.json").read_text(encoding="utf-8"), before
            )


if __name__ == "__main__":
    unittest.main()
