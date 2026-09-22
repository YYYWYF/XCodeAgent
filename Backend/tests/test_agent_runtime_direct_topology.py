"""验证 agent_runtime_direct 在拓扑事实读取、Bootstrap 能力与界面契约上的行为。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.agent_ui_build_contract import project_agent_ui_build_contracts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.project_plan import _agent_runtime_direct_candidate
from app.services.workspace_bootstrap.requested_config import bootstrap_managed_roots
from app.topologies import (
    AGENT_RUNTIME_SERVICE_ID,
    BACKEND_SERVICE_ID,
    confirmed_authentication_termination,
    confirmed_public_edge_service_id,
    confirmed_service_ids,
    includes_backend_service,
    read_confirmed_technical_plan,
    serves_agent_runtime_public_edge,
)
from tests.agent_runtime_direct_test_utils import (
    direct_product_plan,
    direct_project_plan,
    direct_technical_plan,
    direct_topology_projection,
    write_application_config,
    write_technical_plan,
)


class TopologyFactQueryTests(unittest.TestCase):
    """验证下游只从已确认拓扑投影读取服务边界和公开入口。"""

    def test_direct_projection_exposes_service_boundary_and_public_edge(self) -> None:
        """direct 投影必须宣告只含 Runtime 服务，且公开入口由 Runtime 承担。"""

        plan = direct_technical_plan()

        self.assertEqual(confirmed_service_ids(plan), (AGENT_RUNTIME_SERVICE_ID,))
        self.assertEqual(confirmed_public_edge_service_id(plan), AGENT_RUNTIME_SERVICE_ID)
        self.assertEqual(
            confirmed_authentication_termination(plan), AGENT_RUNTIME_SERVICE_ID
        )
        self.assertFalse(includes_backend_service(plan))
        self.assertTrue(serves_agent_runtime_public_edge(plan))

    def test_unmigrated_plan_keeps_frontend_backend_defaults(self) -> None:
        """未迁移计划没有服务边界，必须保守地维持旧流程「含 Backend」的判定。"""

        plan = {"agent_contracts": [{"agentId": "assistant"}]}

        self.assertEqual(confirmed_service_ids(plan), ())
        self.assertIsNone(confirmed_public_edge_service_id(plan))
        self.assertIsNone(confirmed_authentication_termination(plan))
        self.assertTrue(includes_backend_service(plan))
        self.assertFalse(serves_agent_runtime_public_edge(plan))

    def test_confirmed_plan_is_only_read_when_artifact_is_confirmed(self) -> None:
        """未确认、损坏或缺失的 TechnicalPlan 不得被当作已确认事实读取。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans = root / ".xcodeagent" / "plans"
            plans.mkdir(parents=True)
            path = plans / "technical-plan.json"

            self.assertIsNone(read_confirmed_technical_plan(root))

            path.write_text(
                json.dumps(
                    {
                        "artifact_type": "technical-plan",
                        "confirmation_status": "draft",
                        "topology": direct_topology_projection(),
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(read_confirmed_technical_plan(root))

            path.write_text("{ 损坏", encoding="utf-8")
            self.assertIsNone(read_confirmed_technical_plan(root))

            write_technical_plan(root, direct_technical_plan())
            plan = read_confirmed_technical_plan(root)
            self.assertIsNotNone(plan)
            assert plan is not None
            self.assertTrue(serves_agent_runtime_public_edge(plan))


class DirectBuildUnitSkeletonTests(unittest.TestCase):
    """验证 direct 拓扑的 Unit 骨架既不生成 Backend，也不遗漏 Agent 侧单元。"""

    def test_direct_plan_omits_backend_units(self) -> None:
        """服务边界不含 Backend 时不得出现任何 backend 前缀 Unit。"""

        skeleton = ensure_build_unit_skeleton(direct_project_plan(), {})
        unit_ids = set(skeleton["build_units"])

        self.assertFalse(
            [unit_id for unit_id in unit_ids if unit_id.startswith("backend:")]
        )
        self.assertEqual(skeleton["build_units"]["agent:runtime"]["kind"], "agent")
        for expected in (
            "application:root",
            "frontend:shell",
            "frontend:api-client",
            "agent:assistant",
            "page:assistant",
            "app:integration",
        ):
            self.assertIn(expected, unit_ids)

    def test_direct_auth_guard_follows_authentication_termination(self) -> None:
        """认证终止在 Runtime 时需要 auth-guard，匿名会话时不得保留该 Unit。"""

        guarded = ensure_build_unit_skeleton(direct_project_plan(), {})
        self.assertIn("frontend:auth-guard", guarded["build_units"])

        anonymous_plan = direct_project_plan(
            topology=direct_topology_projection(
                authenticationTermination="anonymous-session"
            )
        )
        anonymous = ensure_build_unit_skeleton(anonymous_plan, {})
        self.assertNotIn("frontend:auth-guard", anonymous["build_units"])

    def test_backend_service_boundary_keeps_backend_units(self) -> None:
        """显式声明 Backend 服务边界时必须保留 backend Unit，证明收敛不是一刀切。"""

        plan = direct_project_plan(
            topology=direct_topology_projection(
                serviceIds=["frontend", BACKEND_SERVICE_ID, AGENT_RUNTIME_SERVICE_ID],
                publicEdgeServiceId=BACKEND_SERVICE_ID,
            )
        )

        skeleton = ensure_build_unit_skeleton(plan, {})
        self.assertIn("backend:bootstrap", skeleton["build_units"])
        self.assertIn("frontend:auth-guard", skeleton["build_units"])


class DirectAgentUiContractTests(unittest.TestCase):
    """验证 direct 页面合同指向 Runtime Public Edge，而不是 Gateway Mock。"""

    def test_direct_page_contract_uses_public_edge_without_mock_exemption(self) -> None:
        """公开入口由 Runtime 承担时，页面合同必须是 direct 模式且不豁免 Mock。"""

        contracts = project_agent_ui_build_contracts(
            direct_product_plan(), direct_technical_plan()
        )

        contract = contracts["assistant"]
        self.assertEqual(contract["mode"], "direct")
        self.assertEqual(contract["publicPath"], "/ag-ui/agents/assistant")
        self.assertEqual(contract["serviceId"], AGENT_RUNTIME_SERVICE_ID)
        self.assertEqual(contract["mockExemptEndpointIds"], [])

    def test_direct_page_contract_requires_public_path(self) -> None:
        """direct 合同缺少已确认 Public Edge path 时必须失败而不是退化为 Mock。"""

        plan = direct_technical_plan()
        plan["agent_contracts"][0]["invocation"] = {
            "serviceId": AGENT_RUNTIME_SERVICE_ID,
            "exposure": "public",
            "path": "",
        }

        with self.assertRaisesRegex(ValueError, "Public Edge path"):
            project_agent_ui_build_contracts(direct_product_plan(), plan)


class DirectManagedRootsTests(unittest.TestCase):
    """验证 Bootstrap managed roots 完全由已确认拓扑蓝图决定。"""

    def test_direct_plan_bootstraps_frontend_and_runtime_only(self) -> None:
        """direct 拓扑只物化 Frontend 与 Runtime，不物化 Backend。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_application_config(root)
            write_technical_plan(root, direct_technical_plan())

            self.assertEqual(
                bootstrap_managed_roots(root), ("frontend", "agent-runtime")
            )

    def test_unmigrated_plan_keeps_frontend_backend_roots(self) -> None:
        """没有拓扑投影的历史计划必须保持旧流程的三端 roots 推断。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_application_config(root)
            write_technical_plan(
                root,
                {
                    "artifact_type": "technical-plan",
                    "confirmation_status": "confirmed",
                    "agent_contracts": [{"agentId": "assistant"}],
                },
            )

            self.assertEqual(
                bootstrap_managed_roots(root),
                ("frontend", "backend", "agent-runtime"),
            )


class DirectPlanningCandidateTests(unittest.TestCase):
    """验证规划期候选判据只对纯 Agent 结构事实成立。"""

    def _config(self, *, auth: object = True, authorization: bool = False) -> dict:
        """构造规划期消费的应用能力快照。"""

        return {
            "configRevision": 1,
            "auth": {"enable": auth},
            "authorization": {"enabled": authorization},
        }

    def _facts(self) -> dict:
        """构造纯 Agent 的最小结构事实集合。"""

        return {
            "application_config": self._config(),
            "product_agents": [{"agentId": "assistant"}],
            "entities": [],
            "api_contracts": [],
            "pages": [{"pageId": "assistant", "references": {}}],
        }

    def test_pure_agent_facts_select_direct_candidate(self) -> None:
        """没有实体与业务 Endpoint 的纯 Agent 事实应命中 direct 候选。"""

        self.assertTrue(_agent_runtime_direct_candidate(**self._facts()))

    def test_backend_or_rbac_facts_reject_direct_candidate(self) -> None:
        """实体、API 契约、RBAC、非法认证开关、页面端点依赖都必须被拒绝。"""

        base = self._facts()
        rejected = (
            {"entities": [{"id": "Order"}]},
            {"api_contracts": [{"id": "orders-api"}]},
            {"application_config": self._config(authorization=True)},
            {"application_config": self._config(auth="yes")},
            {"product_agents": []},
            {
                "pages": [
                    {
                        "pageId": "assistant",
                        "references": {
                            "endpoint_dependencies": [{"endpoint_id": "orders.list"}]
                        },
                    }
                ]
            },
        )
        for override in rejected:
            self.assertFalse(
                _agent_runtime_direct_candidate(**{**base, **override}), override
            )


if __name__ == "__main__":
    unittest.main()
