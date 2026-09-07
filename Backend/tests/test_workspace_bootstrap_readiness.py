from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.workspace_bootstrap.models import WorkspaceBootstrapReadinessError
from app.services.workspace_bootstrap.readiness import validate_workspace_bootstrap_readiness


class _GitBaselineVerifier:
    """记录 Readiness 是否在模拟的干净 Git baseline 上执行。"""

    def __init__(self, error: Exception | None = None) -> None:
        """允许测试注入 Git 验证失败。"""

        self.error = error
        self.called = False

    def verify_baseline(self, workspace: str | Path) -> str:
        """模拟 Git baseline 验证，并保留调用证据。"""

        self.called = True
        if self.error is not None:
            raise self.error
        return "abc123"


def _requested_config(*, login: bool = False, authorization: bool = False) -> dict[str, object]:
    """构造与 RequestedConfig 编译器一致的最小请求。"""

    return {
        "capabilities": {
            "login": {"enabled": login, "config": {}},
            "authorization": {"enabled": authorization, "config": {}},
        }
    }


def _prepare_workspace(workspace: Path, *, requested: dict[str, object] | None = None, effective: dict[str, object] | None = None) -> None:
    """写入满足 Bootstrap Readiness 的最小正式产物和真实入口。"""

    specs = workspace / ".xcodeagent/specs"
    plans = workspace / ".xcodeagent/plans"
    specs.mkdir(parents=True)
    plans.mkdir(parents=True)
    for path, payload in (
        (specs / "requirement-spec.json", {"confirmation_status": "confirmed"}),
        (plans / "product-plan.json", {"confirmation_status": "confirmed"}),
        (specs / "ui-designs.json", {"confirmation_status": "confirmed"}),
        (plans / "technical-plan.json", {"confirmation_status": "confirmed", "artifact_type": "technical-plan"}),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    (workspace / "frontend").mkdir()
    (workspace / "frontend/package.json").write_text("{}\n", encoding="utf-8")
    application = workspace / "backend/src/main/java/demo/DemoApplication.java"
    application.parent.mkdir(parents=True)
    application.write_text("class DemoApplication {}\n", encoding="utf-8")
    (workspace / "backend/pom.xml").write_text("<project />\n", encoding="utf-8")
    (workspace / ".git").mkdir()
    state_path = workspace / ".xcodeagent/template-state.json"
    state_path.write_text(
        json.dumps(
            {
                "templateRevision": "template-r1",
                "managedFiles": {},
                "requested": requested or {},
                "effective": effective or {},
            }
        ),
        encoding="utf-8",
    )


class WorkspaceBootstrapReadinessTests(unittest.TestCase):
    """验证 Readiness 只放行完整且可立即进入工作台的 Bootstrap。"""

    def test_accepts_matching_requested_effective_and_real_entrypoints(self) -> None:
        """匹配请求、有效能力、正式产物和入口时应调用 Git 验证。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            capabilities = {"login": {"enabled": True}}
            _prepare_workspace(workspace, requested=capabilities, effective=capabilities)
            verifier = _GitBaselineVerifier()

            validate_workspace_bootstrap_readiness(
                workspace,
                requested_config=_requested_config(login=True),
                git_manager=verifier,
            )

            self.assertTrue(verifier.called)

    def test_rejects_requested_state_drift_before_git_validation(self) -> None:
        """Engine requested 未原样回写应用请求时不得提交 READY。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_workspace(workspace, requested={}, effective={})
            verifier = _GitBaselineVerifier()

            with self.assertRaisesRegex(WorkspaceBootstrapReadinessError, "requested"):
                validate_workspace_bootstrap_readiness(
                    workspace,
                    requested_config=_requested_config(login=True),
                    git_manager=verifier,
                )

            self.assertFalse(verifier.called)

    def test_rejects_missing_effective_requested_capability(self) -> None:
        """请求能力未出现在 effective 时不能把依赖解析失败伪装成成功。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_workspace(
                workspace,
                requested={"login": {"enabled": True}},
                effective={},
            )

            with self.assertRaisesRegex(WorkspaceBootstrapReadinessError, "effective"):
                validate_workspace_bootstrap_readiness(
                    workspace,
                    requested_config=_requested_config(login=True),
                    git_manager=_GitBaselineVerifier(),
                )

    def test_rejects_missing_formal_artifact_staging_and_git_failure(self) -> None:
        """正式产物、staging 或 Git 任一失败都必须阻断就绪提交。"""

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_workspace(workspace)
            (workspace / ".xcodeagent/specs/requirement-spec.json").unlink()
            with self.assertRaisesRegex(WorkspaceBootstrapReadinessError, "正式产物"):
                validate_workspace_bootstrap_readiness(
                    workspace,
                    requested_config=_requested_config(),
                    git_manager=_GitBaselineVerifier(),
                )

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_workspace(workspace)
            (workspace / ".xcodeagent/bootstrap-staging").mkdir()
            with self.assertRaisesRegex(WorkspaceBootstrapReadinessError, "staging"):
                validate_workspace_bootstrap_readiness(
                    workspace,
                    requested_config=_requested_config(),
                    git_manager=_GitBaselineVerifier(),
                )

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            _prepare_workspace(workspace)
            with self.assertRaisesRegex(RuntimeError, "dirty"):
                validate_workspace_bootstrap_readiness(
                    workspace,
                    requested_config=_requested_config(),
                    git_manager=_GitBaselineVerifier(RuntimeError("dirty baseline")),
                )
