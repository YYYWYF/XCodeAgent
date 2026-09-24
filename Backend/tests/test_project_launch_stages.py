"""验证预览启动阶段严格按拓扑蓝图声明的顺序执行。"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.project_launch_stages import (
    LEGACY_LAUNCH_STAGES,
    LaunchStageState,
    resolve_launch_stages,
    run_launch_stages,
)
from tests.agent_runtime_direct_test_utils import (
    direct_technical_plan,
    write_application_config,
    write_technical_plan,
)


# direct 拓扑蓝图声明的启动顺序，与 definitions/agent_runtime_direct.py 一致。
DIRECT_LAUNCH_STAGES = (
    "structure",
    "agent_runtime",
    "frontend",
    "integration_probe",
    "ready",
)


class _StageRecorder:
    """记录启动阶段回调顺序，用于断言阶段序列被真实执行。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def __call__(self, stage: str, status: str, message: str) -> None:
        """按调用顺序记录阶段与状态。"""

        self.calls.append((stage, status))

    @property
    def stages(self) -> list[str]:
        """返回出现过的阶段名序列。"""

        return [stage for stage, _status in self.calls]


def _running_runtime() -> dict[str, object]:
    """构造本轮成功启动的 Agent Runtime 结果，含进程句柄与公开入口地址。"""

    return {
        "status": "running",
        "message": "Agent Runtime 已启动并就绪。",
        "_public_edge_url": "http://127.0.0.1:51000",
        "_process": MagicMock(),
    }


def _running_frontend() -> dict[str, object]:
    """构造本轮成功启动的前端结果。"""

    return {
        "status": "running",
        "message": "前端服务已就绪。",
        "preview_url": "http://localhost:5173",
    }


class ResolveLaunchStagesTests(unittest.TestCase):
    """验证启动阶段顺序来自已确认拓扑，未迁移流程回退到当前固定顺序。"""

    def test_unmigrated_workspace_falls_back_to_current_flow(self) -> None:
        """没有拓扑投影的工作区必须沿用旧流程顺序，行为不变。"""

        with tempfile.TemporaryDirectory() as workspace:
            self.assertEqual(
                resolve_launch_stages(Path(workspace)), LEGACY_LAUNCH_STAGES
            )

    def test_direct_workspace_reads_blueprint_stage_order(self) -> None:
        """已确认 direct 拓扑的工作区必须读到蓝图声明的顺序。"""

        with tempfile.TemporaryDirectory() as workspace:
            root = Path(workspace).resolve()
            write_technical_plan(root, direct_technical_plan())
            write_application_config(root, auth_enabled=True)

            self.assertEqual(resolve_launch_stages(root), DIRECT_LAUNCH_STAGES)

    def test_direct_order_has_no_backend_stage(self) -> None:
        """Direct 拓扑不含 Java Backend 阶段，这是它相对旧流程的核心差异。"""

        self.assertIn("backend", LEGACY_LAUNCH_STAGES)
        self.assertNotIn("backend", DIRECT_LAUNCH_STAGES)
        self.assertIn("integration_probe", DIRECT_LAUNCH_STAGES)


class RunLaunchStagesTests(unittest.TestCase):
    """验证阶段编排按蓝图顺序执行，并如实传递前端注入与失败证据。"""

    def setUp(self) -> None:
        """为本轮编排准备一个隔离的工作区目录。"""

        self._workspace = tempfile.TemporaryDirectory()
        self.addCleanup(self._workspace.cleanup)
        self.root = Path(self._workspace.name).resolve()

    def _state(self, **overrides: object) -> LaunchStageState:
        """构造 direct 拓扑的启动状态，可按需覆盖公开入口判定。"""

        fields: dict[str, object] = {
            "root": self.root,
            "force_restart": False,
            "technical_plan": direct_technical_plan(),
            "runtime_public_edge": True,
        }
        fields.update(overrides)
        return LaunchStageState(**fields)

    def test_unknown_stage_fails_closed(self) -> None:
        """蓝图声明了未实现的阶段时必须立即失败，而不是静默跳过。"""

        with self.assertRaises(ValueError) as caught:
            run_launch_stages(self._state(), ("teleport",), lambda *_: None)

        self.assertIn("teleport", str(caught.exception))

    def test_direct_stages_skip_java_backend_entirely(self) -> None:
        """Direct 阶段序列不含 Backend，因此不得调用后端启动器。"""

        recorder = _StageRecorder()
        probe = {
            "passed": True,
            "message": "公开入口已接受 AG-UI 调用并开始本次运行。",
        }
        with (
            patch(
                "app.services.project_launch_stages.agent_runtime_launch_required",
                return_value=True,
            ),
            patch(
                "app.services.project_launch_stages.launch_backend_project"
            ) as launch_backend,
            patch(
                "app.services.project_launch_stages.launch_agent_runtime_project",
                return_value=_running_runtime(),
            ),
            patch(
                "app.services.project_launch_stages.launch_frontend_project",
                return_value=_running_frontend(),
            ) as launch_frontend,
            patch(
                "app.services.project_launch_stages.probe_direct_runtime_integration",
                return_value=probe,
            ) as run_probe,
        ):
            result = run_launch_stages(self._state(), DIRECT_LAUNCH_STAGES, recorder)

        launch_backend.assert_not_called()
        self.assertEqual(result["status"], "running")
        self.assertIsNone(result["backend"])
        self.assertIn("Agent Runtime 与前端项目均已启动并就绪。", result["message"])
        self.assertEqual(result["integration_probe"], probe)
        self.assertEqual(recorder.stages[-1], "ready")
        # 前端必须拿到本轮真实 Runtime 公开入口地址，而不是任何默认值。
        self.assertEqual(
            launch_frontend.call_args.kwargs["environment_overrides"],
            {
                "VITE_AGENT_RUNTIME_BASE_URL": "http://127.0.0.1:51000",
                "VITE_BACKEND_BASE_URL": "http://127.0.0.1:51000",
            },
        )
        # 探针必须使用与注入前端完全相同的地址与前端源。
        self.assertEqual(
            run_probe.call_args.kwargs["runtime_url"], "http://127.0.0.1:51000"
        )
        self.assertEqual(
            run_probe.call_args.kwargs["frontend_origin"], "http://localhost:5173"
        )

    def test_failed_probe_blocks_ready_and_stops_started_services(self) -> None:
        """集成探针失败必须阻断就绪，并回滚已启动的 Runtime 进程。"""

        recorder = _StageRecorder()
        with (
            patch(
                "app.services.project_launch_stages.agent_runtime_launch_required",
                return_value=True,
            ),
            patch(
                "app.services.project_launch_stages.launch_agent_runtime_project",
                return_value=_running_runtime(),
            ),
            patch(
                "app.services.project_launch_stages.launch_frontend_project",
                return_value=_running_frontend(),
            ),
            patch(
                "app.services.project_launch_stages.probe_direct_runtime_integration",
                return_value={
                    "passed": False,
                    "message": "公开入口没有该调用路径（HTTP 404），调用契约与实际路由不一致。",
                },
            ),
            patch(
                "app.services.project_launch_stages.stop_agent_runtime_project"
            ) as stop_runtime,
        ):
            result = run_launch_stages(self._state(), DIRECT_LAUNCH_STAGES, recorder)

        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failed_stage"], "integration_probe")
        self.assertIn("前端直连 Agent Runtime 公开入口验证失败", result["message"])
        self.assertNotIn("ready", recorder.stages)
        stop_runtime.assert_called_once()

    def test_legacy_stages_keep_backend_order_without_runtime_injection(self) -> None:
        """未迁移流程仍按 Backend → Runtime → Frontend 启动，且不注入 Runtime 地址。"""

        recorder = _StageRecorder()
        backend = {
            "status": "running",
            "message": "后端服务已就绪。",
            "_process": MagicMock(),
        }
        with (
            patch(
                "app.services.project_launch_stages.find_backend_project_root",
                return_value=self.root / "backend",
            ),
            patch(
                "app.services.project_launch_stages.agent_runtime_launch_required",
                return_value=True,
            ),
            patch(
                "app.services.project_launch_stages.launch_backend_project",
                return_value=backend,
            ) as launch_backend,
            patch(
                "app.services.project_launch_stages.launch_agent_runtime_project",
                return_value={
                    "status": "running",
                    "message": "Agent Runtime 已就绪。",
                },
            ),
            patch(
                "app.services.project_launch_stages.launch_frontend_project",
                return_value=_running_frontend(),
            ) as launch_frontend,
        ):
            result = run_launch_stages(
                self._state(runtime_public_edge=False),
                LEGACY_LAUNCH_STAGES,
                recorder,
            )

        launch_backend.assert_called_once()
        self.assertEqual(result["status"], "running")
        self.assertIn("Java 后端、Agent Runtime 与前端项目均已启动并就绪。", result["message"])
        self.assertEqual(
            recorder.stages,
            [
                "structure",
                "structure",
                "backend",
                "backend",
                "agent_runtime",
                "agent_runtime",
                "frontend",
                "frontend",
                "ready",
            ],
        )
        self.assertEqual(launch_frontend.call_args.kwargs, {})


if __name__ == "__main__":
    unittest.main()
