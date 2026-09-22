"""验证 agent_runtime_direct 在 Bootstrap 就绪校验与 Runtime 启动门禁上的行为。"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.services.agent_runtime_launch_support import agent_runtime_environment
from app.services.agent_runtime_project_launcher import (
    _direct_runtime_auth_enabled,
    agent_runtime_launch_required,
)
from app.services.workspace_bootstrap.models import WorkspaceBootstrapReadinessError
from app.services.workspace_bootstrap.readiness import validate_workspace_bootstrap_readiness
from tests.agent_runtime_direct_test_utils import (
    RUNTIME_CAPABILITY_ID,
    direct_technical_plan,
    direct_topology_projection,
    write_application_config,
    write_runtime_template,
    write_technical_plan,
)

# Runtime 能力的 requested 与 effective 必须逐字一致，Readiness 才放行。
_CAPABILITY_STATE = {RUNTIME_CAPABILITY_ID: {"enabled": True, "config": {}}}


class _GitBaselineVerifier:
    """记录 Readiness 是否真正推进到 Git baseline 验证。"""

    def __init__(self) -> None:
        self.called = False

    def verify_baseline(self, workspace: str | Path) -> str:
        """模拟 Git baseline 验证并记录调用事实。"""

        self.called = True
        return "HEAD"


def _requested_config() -> dict:
    """构造与 managed roots 一致的 Runtime 能力请求。"""

    return {"capabilities": _CAPABILITY_STATE}


def _prepare_direct_workspace(root: Path, **runtime_kwargs: object) -> None:
    """写入满足 direct 拓扑 Readiness 的最小工作区，且不创建 backend 目录。"""

    specs = root / ".xcodeagent" / "specs"
    plans = root / ".xcodeagent" / "plans"
    specs.mkdir(parents=True)
    plans.mkdir(parents=True)
    for path, payload in (
        (specs / "requirement-spec.json", {"confirmation_status": "confirmed"}),
        (plans / "product-plan.json", {"confirmation_status": "confirmed"}),
        (specs / "ui-designs.json", {"confirmation_status": "confirmed"}),
    ):
        path.write_text(json.dumps(payload), encoding="utf-8")
    write_technical_plan(root, direct_technical_plan())
    write_runtime_template(root, **runtime_kwargs)
    (root / ".git").mkdir()
    (root / ".xcodeagent" / "template-state.json").write_text(
        json.dumps(
            {
                "schemaVersion": 2,
                "templateRevision": "template-r1",
                "requested": _CAPABILITY_STATE,
                "effective": _CAPABILITY_STATE,
                "appliedAdditions": {},
            }
        ),
        encoding="utf-8",
    )


class DirectBootstrapReadinessTests(unittest.TestCase):
    """验证 direct 工作区在缺少 Backend 时仍能就绪，但 Runtime 证据必须真实。"""

    def test_direct_workspace_is_ready_without_backend_entrypoint(self) -> None:
        """managed roots 不含 backend 时不得要求 pom.xml 或 Spring Boot 入口。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare_direct_workspace(root)
            verifier = _GitBaselineVerifier()

            validate_workspace_bootstrap_readiness(
                root,
                requested_config=_requested_config(),
                managed_roots=("frontend", "agent-runtime"),
                git_manager=verifier,
            )

            self.assertTrue(verifier.called)
            self.assertFalse((root / "backend").exists())

    def test_direct_workspace_rejects_mismatched_manifest_topology(self) -> None:
        """Runtime 模板声明的拓扑与本轮期望拓扑不一致时必须失败关闭。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare_direct_workspace(root, manifest_topology="gateway_composed")
            verifier = _GitBaselineVerifier()

            with self.assertRaisesRegex(
                WorkspaceBootstrapReadinessError, "capability manifest 无效"
            ):
                validate_workspace_bootstrap_readiness(
                    root,
                    requested_config=_requested_config(),
                    managed_roots=("frontend", "agent-runtime"),
                    git_manager=verifier,
                )

            self.assertFalse(verifier.called)

    def test_direct_workspace_rejects_missing_capability_evidence(self) -> None:
        """已请求的 Runtime 能力缺少入口或 contract test 证据时不得就绪。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _prepare_direct_workspace(
                root,
                capabilities={
                    RUNTIME_CAPABILITY_ID: {"entrypoint": "", "contractTests": []}
                },
            )

            with self.assertRaisesRegex(
                WorkspaceBootstrapReadinessError, "缺少有效证据"
            ):
                validate_workspace_bootstrap_readiness(
                    root,
                    requested_config=_requested_config(),
                    managed_roots=("frontend", "agent-runtime"),
                    git_manager=_GitBaselineVerifier(),
                )


class DirectRuntimeLaunchGateTests(unittest.TestCase):
    """验证 Runtime 是否启动完全由已确认服务边界决定。"""

    def test_launch_required_follows_confirmed_service_boundary(self) -> None:
        """服务边界含 Runtime 才启动；显式不含时不得按 Agent 数量回退猜测。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            write_technical_plan(root, direct_technical_plan())
            self.assertTrue(agent_runtime_launch_required(root))

            write_technical_plan(
                root,
                direct_technical_plan(
                    topology=direct_topology_projection(
                        serviceIds=["frontend", "backend"],
                        publicEdgeServiceId="backend",
                    )
                ),
            )
            self.assertFalse(agent_runtime_launch_required(root))

            (root / ".xcodeagent" / "plans" / "technical-plan.json").unlink()
            self.assertFalse(agent_runtime_launch_required(root))

    def test_launch_required_keeps_legacy_fallback_for_unmigrated_plan(self) -> None:
        """没有拓扑投影的历史计划仍按 agent_contracts 推断，保证旧流程不变。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            write_technical_plan(
                root,
                {
                    "artifact_type": "technical-plan",
                    "confirmation_status": "confirmed",
                    "agent_contracts": [{"agentId": "assistant"}],
                },
            )
            self.assertTrue(agent_runtime_launch_required(root))

            write_technical_plan(
                root,
                {
                    "artifact_type": "technical-plan",
                    "confirmation_status": "confirmed",
                    "agent_contracts": [],
                },
            )
            self.assertFalse(agent_runtime_launch_required(root))

    def test_direct_runtime_auth_flag_reads_topology_and_application_config(self) -> None:
        """只有在 Runtime 承担公开入口时，才按 application.json 决定托管认证。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            write_technical_plan(root, direct_technical_plan())
            write_application_config(root, auth_enabled=True)
            self.assertIs(_direct_runtime_auth_enabled(root), True)

            write_application_config(root, auth_enabled=False)
            self.assertIs(_direct_runtime_auth_enabled(root), False)

            write_technical_plan(
                root,
                direct_technical_plan(
                    topology=direct_topology_projection(
                        serviceIds=["frontend", "backend"],
                        publicEdgeServiceId="backend",
                    )
                ),
            )
            self.assertIsNone(_direct_runtime_auth_enabled(root))

            write_technical_plan(
                root,
                {
                    "artifact_type": "technical-plan",
                    "confirmation_status": "confirmed",
                    "agent_contracts": [{"agentId": "assistant"}],
                },
            )
            self.assertIsNone(_direct_runtime_auth_enabled(root))


class _ModelFallbackSettings:
    """提供 Runtime 兜底模型配置，使环境注入测试不依赖真实 .env。"""

    model_provider = "deepseek"
    model_api_name = "deepseek-chat"
    model_base_url = "http://127.0.0.1:9/v1"
    model_api_key = "test-key"
    model_timeout_seconds = 30
    model_max_retries = 2
    default_temperature = 0.2
    default_max_tokens = 1024


class DirectRuntimeEnvironmentTests(unittest.TestCase):
    """验证 direct 拓扑下 Runtime 自持认证，且宿主凭据不得被继承。"""

    def test_gateway_managed_runtime_receives_shared_internal_token(self) -> None:
        """公开入口不由 Runtime 承担时，仍注入网关共享内部凭据。"""

        environment = agent_runtime_environment(
            _ModelFallbackSettings(), port=5100, gateway_token="internal-token"
        )

        self.assertEqual(environment["AGENT_RUNTIME_GATEWAY_TOKEN"], "internal-token")
        self.assertNotIn("AGENT_RUNTIME_PROFILE", environment)

    def test_direct_runtime_drops_gateway_token_and_owns_authentication(self) -> None:
        """direct 拓扑下 Runtime 自行终止认证，只接收本地认证配置。"""

        environment = agent_runtime_environment(
            _ModelFallbackSettings(),
            port=5100,
            gateway_token="session-secret",
            direct_auth_enabled=True,
        )

        self.assertNotIn("AGENT_RUNTIME_GATEWAY_TOKEN", environment)
        self.assertEqual(environment["AGENT_RUNTIME_PROFILE"], "local")
        self.assertEqual(environment["AGENT_RUNTIME_AUTH_ENABLED"], "true")
        self.assertEqual(environment["AGENT_RUNTIME_AUTH_MODE"], "local")
        self.assertEqual(
            environment["AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET"], "session-secret"
        )
        self.assertIn(
            "http://127.0.0.1:5173", environment["AGENT_RUNTIME_ALLOWED_ORIGINS"]
        )
        self.assertNotIn("AGENT_RUNTIME_DEBUG_TOKEN", environment)

    def test_direct_runtime_debug_token_requires_explicit_request(self) -> None:
        """调试令牌只在显式请求时下发，并复用本轮共享随机串。"""

        environment = agent_runtime_environment(
            _ModelFallbackSettings(),
            port=5100,
            gateway_token="session-secret",
            direct_auth_enabled=False,
            include_debug_access=True,
        )

        self.assertEqual(environment["AGENT_RUNTIME_AUTH_ENABLED"], "false")
        self.assertEqual(environment["AGENT_RUNTIME_DEBUG_TOKEN"], "session-secret")

    @patch.dict(
        os.environ,
        {
            "AGENT_RUNTIME_GATEWAY_TOKEN": "host-leaked-token",
            "AGENT_RUNTIME_PROFILE": "host-profile",
            "AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET": "host-secret",
        },
    )
    def test_direct_runtime_never_inherits_host_runtime_credentials(self) -> None:
        """宿主残留的 Runtime 凭据不得随环境继承进本轮生成的 Runtime。"""

        environment = agent_runtime_environment(
            _ModelFallbackSettings(),
            port=5100,
            gateway_token="fresh-secret",
            direct_auth_enabled=True,
        )

        self.assertNotIn("AGENT_RUNTIME_GATEWAY_TOKEN", environment)
        self.assertEqual(environment["AGENT_RUNTIME_PROFILE"], "local")
        self.assertEqual(
            environment["AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET"], "fresh-secret"
        )


if __name__ == "__main__":
    unittest.main()
