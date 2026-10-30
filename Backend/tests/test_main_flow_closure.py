"""主流程五处断点的当前合同回归，不访问模型、数据库或真实业务服务。"""

from copy import deepcopy
import asyncio
import json
from unittest import TestCase
from unittest.mock import patch

from app.agents.main.document_sync import sync_project_plan_from_markdown
from app.domain.application_planning_recovery import (
    ApplicationPlanningOperation, ApplicationPlanningRecoveryBoundary,
    application_planning_boundary_payload, parse_application_planning_boundary, validate_application_planning_baseline,
)
from app.graph.nodes.application_technical_planning import technical_planning_confirm, technical_planning_begin
from app.services.agent_ui_build_contract import project_agent_ui_build_contracts
from app.services.business_acceptance_verifiers.frontend_agent_ui import verify_agent_ui_runtime_source
from app.services.frontend_agent_ui_assets import validate_agent_ui_frontend_files
from app.services.frontend_agent_ui_scaffold import inject_frontend_agent_ui_scaffold
from app.services.stage_artifacts import load_stage_artifacts
from app.services.technical_plan_topology_selection import select_technical_plan_topology
from app.workspace.plan_documents import commit_technical_plan_document
from tests.test_technical_planning_recovery_journey import TechnicalPlanningRecoveryJourneyHarness
from tests.test_agent_integration_1008 import fixture


class MainFlowClosureTests(TestCase):
    """覆盖磁盘基线与失败候选分离、当前应用上下文和正式 Runtime 组合。"""

    def setUp(self):
        """用生产辅助函数创建具有正式上游文档和生命周期的隔离工作区。"""

        self.h = TechnicalPlanningRecoveryJourneyHarness()
        self.addCleanup(self.h.close)

    def state(self, plan):
        """生成可直接进入确认节点的完整当前事务输入。"""

        h = self.h
        return {"workspace": str(h.workspace), "workflow_scope": "application_planning",
            "active_run_id": h.source_run_id, "active_thread_id": h.thread_id,
            "requirement_spec": h.requirement_spec, "product_plan": h.product_plan, "ui_designs": h.ui_designs,
            "technical_plan": plan,
            "application_planning_recovery_boundary": application_planning_boundary_payload(
                operation_id="confirm", operation=ApplicationPlanningOperation.INITIAL,
                boundary=ApplicationPlanningRecoveryBoundary.ARTIFACT_COMMITTED, request=h.request, candidate=plan)}

    def test_confirmation_failure_reenters_begin_with_disk_baseline(self):
        """复验失败后的真实磁盘刷新不能与 repair baseline 冲突，候选不得伪装已确认。"""

        plan = deepcopy(self.h.candidate)
        state = self.state(plan)
        commit_technical_plan_document(state, plan)
        with patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=["页面合同需要修复"]):
            result = technical_planning_confirm(state)
        self.assertEqual(result["technical_plan"], plan)
        self.assertEqual(result["technical_plan_repair_candidate"]["confirmation_status"], "pending_user_confirmation")
        reentry = {**state, **result}
        reentry.update(load_stage_artifacts(reentry, "technical_planning_begin"))
        boundary = parse_application_planning_boundary(reentry["application_planning_recovery_boundary"])
        validate_application_planning_baseline(boundary, reentry["technical_plan"])
        generated = asyncio.run(technical_planning_begin(reentry))
        self.assertEqual(generated["application_planning_recovery_boundary"]["boundary"], "generation_ready")

    def test_markdown_confirmation_uses_current_application_config(self):
        """人工编辑后仍消费实际配置版本与正式产品，不退回默认配置或跳过 Agent 合同。"""

        h = self.h
        application = {**h.application_config, "configRevision": 7}
        (h.workspace / ".devagentstudio/application.json").write_text(json.dumps(application))
        plan = select_technical_plan_topology({**h.candidate, "sourceConfigRevision": 7}, "backend_direct", application)
        state = self.state(plan)
        commit_technical_plan_document(state, plan)
        synced = {key: deepcopy(plan[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        with (
            patch("app.workspace.plan_documents.edited_technical_plan_markdown", return_value="# 人工编辑的技术规划"),
            patch("app.agents.main.document_sync._invoke_sync_model", return_value=synced),
            patch("app.graph.nodes.planning._project_plan_validation_errors", return_value=["用于检查候选上下文"]),
        ):
            result = technical_planning_confirm(state)
        candidate = result.get("technical_plan_repair_candidate") or result["technical_plan"]
        self.assertEqual(candidate["sourceConfigRevision"], 7)
        with self.assertRaisesRegex(ValueError, "当前应用配置"):
            sync_project_plan_from_markdown(plan, h.requirement_spec, "缺少实际配置的调用")

    def test_formal_agent_ui_is_runtime_and_rejects_mock_or_foreign_endpoint(self):
        """正式页面要求精确 Runtime 路径及默认组件，不能通过配置伪装为真实会话。"""

        _, product, plan = fixture()
        contract = next(iter(project_agent_ui_build_contracts(product, plan).values()))
        self.assertEqual(contract["mode"], "runtime")
        source = (
            f"import {{ {contract['component']} }} from '{contract['componentModule']}';\n"
            f"const AGENT_UI_CONFIG = {json.dumps(contract['config'], ensure_ascii=False)};\n"
            f"export default function Page() {{ return <{contract['component']} config={{AGENT_UI_CONFIG}} />; }}"
        )
        self.assertEqual(verify_agent_ui_runtime_source({"page.tsx": source}, {"agent_ui": contract})["status"], "passed")
        fake = deepcopy(contract)
        fake["config"].pop("runtime")
        self.assertEqual(verify_agent_ui_runtime_source({"page.tsx": source}, {"agent_ui": fake})["status"], "blocked")
        foreign = source.replace("/agents/assistant/run", "https://foreign.invalid/run")
        self.assertEqual(verify_agent_ui_runtime_source({"page.tsx": foreign}, {"agent_ui": contract})["status"], "failed")

    def test_runtime_asset_validation_rejects_modified_adapter(self):
        """真实传输资产必须实际存在且未被替换，页面声明正确也不能掩盖 Mock 适配器。"""

        _, product, _ = fixture()
        h = self.h
        (h.workspace / "frontend").mkdir()
        (h.workspace / ".devagentstudio/plans/product-plan.json").write_text(json.dumps(product))
        inject_frontend_agent_ui_scaffold(h.workspace)
        self.assertEqual(validate_agent_ui_frontend_files(h.workspace), [])
        target = h.workspace / "frontend/src/apis/agentConversationRuntime.ts"
        target.write_text("export const fakeSuccess = true")
        self.assertTrue(validate_agent_ui_frontend_files(h.workspace))
