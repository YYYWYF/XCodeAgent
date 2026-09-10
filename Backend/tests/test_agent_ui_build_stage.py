from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.agents.frontend.generator import _frontend_generation_prompt
from app.domain.application_lifecycle import PendingInteractionType
from app.graph.nodes.lifecycle import finalize_project
from app.graph.subgraphs.acceptance import acceptance_review
from app.graph.subgraphs.build import _verify_business_results
from app.graph.workflow import route_build_result
from app.protocols.workflow.lifecycle import _pending_interaction
from app.services.agent_ui_build_contract import (
    AGENT_UI_BUILD_CONTRACT_VERSION,
    apply_agent_ui_delivery_boundary,
    project_agent_ui_build_contracts,
)
from app.services.business_acceptance import compile_business_acceptance
from app.services.business_acceptance_verifier import verify_business_acceptance
from app.services.build_task_planner import build_task_candidate_contract_errors
from app.services.build_unit_compiler import apply_unit_compilation


def _product_plan() -> dict:
    """构造包含一个浮窗 Agent Surface 的最小已确认 ProductPlan。"""

    return {
        "pages": [
            {
                "pageId": "orders",
                "name": "订单列表",
                "information_items": [
                    {"itemId": "selected_order_ids", "label": "已选择订单"}
                ],
                "actions": [{"actionId": "open_order_assistant"}],
            }
        ],
        "agents": [
            {
                "agentId": "order_assistant",
                "name": "订单助手",
                "purpose": "分析订单并协助跟进。",
                "entryPageIds": ["orders"],
                "capabilities": [
                    {"capabilityId": "analyze_orders", "name": "分析异常订单"}
                ],
                "pageActionBindings": [
                    {
                        "pageId": "orders",
                        "actionIds": ["open_order_assistant"],
                        "surface": {
                            "type": "floating_panel",
                            "enabled": True,
                            "contextItemIds": ["selected_order_ids"],
                        },
                    }
                ],
            }
        ],
    }


def _technical_plan() -> dict:
    """构造 Agent Gateway 稳定引用和页面实现契约。"""

    return {
        "agent_contracts": [
            {
                "agentId": "order_assistant",
                "invocation": {"gatewayEndpointId": "agent_gateway.run"},
            }
        ],
        "page_implementation_contracts": [
            {
                "pageId": "orders",
                "requiredEndpointIds": ["orders.list", "agent_gateway.run"],
            }
        ],
        "api_contracts": [],
    }


def _agent_ui_task(contract: dict, path: str = "frontend/src/pages/Orders/index.tsx") -> dict:
    """构造携带平台 Agent UI 来源的前端页面任务。"""

    return {
        "id": "page:orders::implementation",
        "unit_id": "page:orders",
        "owner": "frontend",
        "status": "completed",
        "change_scope": [{"operation": "modify", "path": path}],
        "allowed_paths": [path],
        "target_files": [path],
        "source_refs": {"agent_ui": contract},
        "deliverables": [
            {
                "id": "orders-page",
                "kind": "frontend.page",
                "target_id": "orders",
                "paths": [path],
                "provides": ["orders.render"],
            }
        ],
    }


class AgentUiBuildStageTests(unittest.TestCase):
    """验证阶段 6 的平台配置、Mock 检查和生命周期门禁。"""

    def test_projects_platform_owned_mock_contract(self) -> None:
        """Agent UI Build 契约必须包含固定组件、配置和未来网关引用。"""

        contracts = project_agent_ui_build_contracts(_product_plan(), _technical_plan())

        contract = contracts["orders"]
        self.assertEqual(contract["version"], AGENT_UI_BUILD_CONTRACT_VERSION)
        self.assertEqual(contract["mode"], "mock")
        self.assertEqual(contract["component"], "AgentFloatingPanel")
        self.assertEqual(contract["gatewayEndpointId"], "agent_gateway.run")
        self.assertEqual(contract["mockExemptEndpointIds"], ["agent_gateway.run"])
        self.assertEqual(contract["config"]["actionId"], "open_order_assistant")
        self.assertEqual(contract["config"]["contextItems"][0]["id"], "selected_order_ids")

    def test_unit_compiler_overwrites_candidate_agent_ui_source(self) -> None:
        """模型候选不得覆盖页面 Unit 的平台 Agent UI 来源。"""

        contract = project_agent_ui_build_contracts(_product_plan(), _technical_plan())["orders"]
        task = _agent_ui_task({"agentId": "forged"})
        plan = {"build_units": {"page:orders": {"source_refs": {}}}, "unit_graph": {"edges": []}}
        context = {
            "target": {"type": "page", "id": "orders"},
            "source_refs": {"agent_ui": contract},
            "required_unit_ids": ["page:orders"],
        }

        compiled = apply_unit_compilation(plan, [task], context)[0]

        self.assertEqual(compiled["source_refs"]["agent_ui"], contract)
        errors = build_task_candidate_contract_errors(
            {"tasks": [{**task, "source_refs": {"agent_ui": {"agentId": "forged"}}}]}
        )
        self.assertTrue(any("source_refs.agent_ui" in error for error in errors))

    def test_frontend_prompt_forces_fixed_mock_composition_only_for_agent_task(self) -> None:
        """只有 Agent 页面任务才加载专用 Skill 并禁止真实网络与 adapter 注入。"""

        contract = project_agent_ui_build_contracts(_product_plan(), _technical_plan())["orders"]
        agent_prompt = _frontend_generation_prompt(
            project_plan={"app": {"name": "demo"}},
            build_task_plan={"summary": {}},
            tasks=[_agent_ui_task(contract)],
        )
        normal_prompt = _frontend_generation_prompt(
            project_plan={"app": {"name": "demo"}},
            build_task_plan={"summary": {}},
            tasks=[{"id": "page:home", "allowed_paths": ["frontend/src/pages/Home/index.tsx"]}],
        )

        self.assertIn("agent-ui-surface-template/SKILL.md", agent_prompt)
        self.assertIn("AgentFloatingPanel", agent_prompt)
        self.assertIn("AGENT_UI_CONFIG", agent_prompt)
        self.assertIn("must not pass the `adapter` prop", agent_prompt)
        self.assertIn("must not call the future Gateway Endpoint", agent_prompt)
        self.assertNotIn("agent-ui-surface-template/SKILL.md", normal_prompt)

    def test_agent_ui_mock_check_compiles_and_rejects_network_or_adapter_override(self) -> None:
        """Mock 检查必须验证精确配置、默认 Adapter 和页面无直连网络。"""

        contract = project_agent_ui_build_contracts(_product_plan(), _technical_plan())["orders"]
        context = {
            "project_plan": _technical_plan(),
            "target": {"type": "page", "id": "orders"},
            "page_implementation_contract": _technical_plan()["page_implementation_contracts"][0],
        }
        task = compile_business_acceptance([_agent_ui_task(contract)], context)[0]
        checks = {check["kind"]: check for check in task["business_acceptance_checks"]}

        self.assertIn("frontend.agent_ui_mock_contract", checks)
        self.assertEqual(
            checks["frontend.page_endpoint_usage"]["expected"]["required_endpoint_ids"],
            ["orders.list"],
        )
        source = (
            "import React from 'react'\n"
            "import { ProCard } from '@ant-design/pro-components'\n"
            "import { AgentFloatingPanel } from '@/components/AgentConversation'\n"
            "import type { AgentUiTemplateConfig } from '@/typings/agentConversation'\n"
            f"const AGENT_UI_CONFIG: AgentUiTemplateConfig = {contract['config']!r}\n"
            "export default function Orders(){return <><ProCard>订单</ProCard><AgentFloatingPanel config={AGENT_UI_CONFIG} /></>}\n"
        ).replace("True", "true").replace("False", "false").replace("'", '"')
        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend/src/pages/Orders/index.tsx"
            target.parent.mkdir(parents=True)
            target.write_text(source, encoding="utf-8")
            passed = verify_business_acceptance(
                {**task, "business_acceptance_checks": [checks["frontend.agent_ui_mock_contract"]]},
                workspace,
                formal_artifacts={**_technical_plan(), "_product_plan": _product_plan()},
            )
            target.write_text(
                source.replace(
                    "config={AGENT_UI_CONFIG}",
                    "config={AGENT_UI_CONFIG} adapter={createNetworkAdapter()}",
                )
                + "\nconst socket = new WebSocket(\"ws://localhost\")\n",
                encoding="utf-8",
            )
            failed = verify_business_acceptance(
                {**task, "business_acceptance_checks": [checks["frontend.agent_ui_mock_contract"]]},
                workspace,
                formal_artifacts={**_technical_plan(), "_product_plan": _product_plan()},
            )

        self.assertEqual(passed["status"], "passed")
        self.assertEqual(failed["status"], "failed")

    def test_agent_ui_mock_check_blocks_stale_product_plan(self) -> None:
        """ProductPlan 改动后旧 Agent UI 合同必须因正式来源哈希变化而失效。"""

        contract = project_agent_ui_build_contracts(_product_plan(), _technical_plan())["orders"]
        context = {
            "project_plan": _technical_plan(),
            "target": {"type": "page", "id": "orders"},
            "page_implementation_contract": _technical_plan()["page_implementation_contracts"][0],
        }
        task = compile_business_acceptance([_agent_ui_task(contract)], context)[0]
        check = next(
            item
            for item in task["business_acceptance_checks"]
            if item["kind"] == "frontend.agent_ui_mock_contract"
        )
        changed_plan = _product_plan()
        changed_plan["agents"][0]["name"] = "新的订单助手"

        with tempfile.TemporaryDirectory() as workspace:
            target = Path(workspace) / "frontend/src/pages/Orders/index.tsx"
            target.parent.mkdir(parents=True)
            target.write_text("export default function Orders(){return null}\n", encoding="utf-8")
            result = verify_business_acceptance(
                {**task, "business_acceptance_checks": [check]},
                workspace,
                formal_artifacts={**_technical_plan(), "_product_plan": changed_plan},
            )

        self.assertEqual(result["status"], "blocked")
        self.assertEqual(
            result["business_acceptance_evidence"][0]["facts"]["reason_code"],
            "formal_source_changed",
        )

    @patch("app.graph.subgraphs.build.dag_business_self_check_enabled", return_value=False)
    def test_agent_ui_mock_check_runs_when_optional_business_checks_are_disabled(self, _flag) -> None:
        """关闭普通业务自检时，Agent UI Mock 检查仍必须执行并阻断失败。"""

        task = _agent_ui_task({"mode": "mock"})
        task["business_acceptance_checks"] = [
            {
                "id": "agent-ui",
                "kind": "frontend.agent_ui_mock_contract",
                "description": "required",
                "sources": [],
                "expected": {"agent_ui": {"mode": "mock"}},
                "target_paths": task["target_files"],
                "verification": {
                    "mode": "deterministic",
                    "verifier": "frontend_agent_ui_mock_contract",
                },
                "required": True,
                "verification_stage": "build",
            }
        ]
        with patch(
            "app.graph.subgraphs.build.verify_business_acceptance",
            return_value={
                "status": "failed",
                "business_acceptance_evidence": [
                    {"check_id": "agent-ui", "kind": "frontend.agent_ui_mock_contract", "status": "failed"}
                ],
                "business_acceptance_summary": {"total": 1, "passed": 0, "failed": 1, "blocked": 0},
            },
        ) as verify:
            result = _verify_business_results(
                {"project_plan": {}},
                [task],
                [{"task_id": task["id"], "status": "completed"}],
                workspace_root="/tmp/workspace",
            )[0]

        verify.assert_called_once()
        self.assertEqual(result["status"], "failed")

    def test_mock_completion_stops_before_testing_launch_and_acceptance(self) -> None:
        """Mock 页面完成后必须停在真实集成待办，不能进入测试或最终验收。"""

        contract = project_agent_ui_build_contracts(_product_plan(), _technical_plan())["orders"]
        summary = apply_agent_ui_delivery_boundary(
            {"status": "completed", "total": 1, "completed": 1},
            [_agent_ui_task(contract)],
        )
        state = {"build_summary": summary, "tasks": [_agent_ui_task(contract)]}

        self.assertEqual(summary["status"], "mock_completed")
        self.assertEqual(summary["delivery_boundary"]["real_integration"], "pending")
        self.assertEqual(route_build_result(state), "await_user_input")
        self.assertEqual(acceptance_review({**state, "acceptance_decision": "accepted"})["accepted"], False)
        self.assertEqual(finalize_project(state)["status"], "requires_user_input")
        interaction_type, payload = _pending_interaction(
            {
                "clarification": {
                    "mode": "agent_ui_integration_pending",
                    "status": "requires_user_input",
                }
            }
        )
        self.assertEqual(
            interaction_type,
            PendingInteractionType.AGENT_UI_INTEGRATION_PENDING,
        )
        self.assertEqual(payload["mode"], "agent_ui_integration_pending")


if __name__ == "__main__":
    unittest.main()
