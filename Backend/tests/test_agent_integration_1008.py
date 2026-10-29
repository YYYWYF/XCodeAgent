"""旧分支功能在当前 Direct Runtime 合同上的集成回归。"""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from hashlib import sha256

from app.domain.application_lifecycle import ApplicationLifecycleStage, ApplicationLifecycleStatus
from app.graph.nodes.api_design import api_design_readiness_gate
from app.services.agent_settings_revision import execute_agent_settings_revision, parse_agent_settings_revision_request
from app.services.agent_ui_build_contract import project_agent_ui_build_contracts, apply_agent_ui_delivery_boundary
from app.services.application_lifecycle import create_application_lifecycle, write_application_lifecycle, start_workbench_execution, end_workbench_execution, load_application_lifecycle
from app.services.build_context_resolver import resolve_target_build_context
from app.services.execution_resource_scope import resolve_execution_resource_claims, resource_claim_keys
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec
from app.services.runtime_agent_settings import validate_technical_plan_agent_contracts, recompile_technical_plan_agent_settings
from app.services.unit_generation_requirement_targets import scoped_formal_targets
from app.services.build_scheduler import resolve_execution_slice
from app.services.runtime_agent_settings import synchronize_settings_markdown
from tests.test_agent_runtime_template_policy import _write_runtime
from app.observability.telemetry_models import TelemetryTarget


def fixture() -> tuple[dict, dict, dict]:
    """构造无 Java Gateway 的业务智能体与唯一产品入口。"""

    spec = create_requirement_spec("创建库存管理系统")
    page_id = spec["pages"][0]["pageId"]
    spec["agent_requirements"] = [{"agentId": "assistant", "name": "库存助手", "purpose": "解释库存状态。",
        "capabilities": ["解释库存"], "entryPageIds": [page_id], "interactionMode": "conversation", "boundaries": []}]
    product = create_product_plan(spec)
    product["confirmation_status"] = "confirmed"
    plan = {"artifact_type": "technical-plan", "confirmation_status": "confirmed", "architecture": {},
        "entities": [], "api_contracts": [], "pages": [{"pageId": page_id}],
        "topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
                     "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"},
        "agent_contracts": [{"agentId": "assistant", "name": "库存助手", "purpose": "解释库存状态。", "pageIds": [page_id],
            "agentSettings": {"prompt": {"systemPrompt": "请解释库存状态。", "persona": {"role": "库存助手", "tone": "简洁"},
                                          "constraints": [], "hiddenBusinessFact": "保留"},
                "model": {"source": "project", "generation": {"temperature": 0.2}}, "memory": {"isolation": "principal/thread"},
                "tools": {"enabled": False, "bindings": []}, "skills": {"enabled": False},
                "knowledge": {"enabled": False}, "context": {"trusted": True}}}]}
    return spec, product, plan


class AgentIntegration1008Tests(unittest.TestCase):
    """验证真实作用域、资源释放、配置正式确认与 Mock 交付边界。"""

    def test_agent_session_telemetry_accepts_current_target_identity(self):
        """前端新会话的 Agent 身份必须由后端当前埋点合同接受。"""

        self.assertEqual(TelemetryTarget.model_validate({"type": "agent", "id": "assistant"}).type, "agent")

    def test_scheduler_keeps_agent_and_tools_without_descendant_pages(self):
        """真实调度切片不能把新增 Agent scope 当作 application 全量执行。"""

        graph = {"nodes": ["agent:assistant", "page:home", "backend:endpoint:api:list"], "edges": [
            {"from": "backend:endpoint:api:list", "to": "agent:assistant", "type": "depends_on"},
            {"from": "agent:assistant", "to": "page:home", "type": "depends_on"}]}
        tasks = [{"id": "agent", "unit_id": "agent:assistant"}, {"id": "page", "unit_id": "page:home"},
                 {"id": "tool", "unit_id": "backend:endpoint:api:list"}]
        result = resolve_execution_slice(build_task_plan={"unit_graph": graph}, tasks=tasks,
            build_execution_scope={"type": "agent", "targetId": "assistant"})
        self.assertEqual(set(result["task_ids"]), {"agent", "tool"})

    def test_markdown_sync_preserves_hidden_prompt_facts(self):
        """Markdown 配置编辑不能删除隐藏 Prompt 字段，也不能改其他模块。"""

        _, product, plan = fixture()
        candidate = deepcopy(plan)
        candidate["agent_contracts"][0]["agentSettings"]["prompt"]["systemPrompt"] = "请先核对库存范围。"
        candidate["agent_contracts"][0]["agentSettings"]["prompt"].pop("hiddenBusinessFact")
        with patch("app.agents.main.document_sync._invoke_sync_model", return_value=candidate):
            result = synchronize_settings_markdown(plan, "用户编辑后的 Markdown", product, "assistant",
                generated_markdown_sha256=sha256(b"original").hexdigest())
        prompt = result["agent_contracts"][0]["agentSettings"]["prompt"]
        self.assertEqual(prompt["hiddenBusinessFact"], "保留")
        self.assertEqual(prompt["systemPrompt"], "请先核对库存范围。")

    def test_agent_scope_does_not_generate_pages_or_frontend_modules(self):
        """独立 Agent 开发只带入所选 Agent 与 Tool，不能扩大为绑定页面。"""

        _, _, plan = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_runtime(root)
            context = resolve_target_build_context(plan, target_type="agent", target_id="assistant",
                project_plan_path=root / ".devagentstudio/plans/technical-plan.json")
        self.assertEqual(context["required_unit_ids"], ["agent:assistant"])
        self.assertEqual(scoped_formal_targets(plan, {"type": "agent", "targetId": "assistant"}), ({}, {}))
        with self.assertRaisesRegex(ValueError, "不包含智能体"):
            resolve_target_build_context(plan, target_type="agent", target_id="missing")

    def test_agent_gate_checks_tools_and_continues_without_application_expansion(self):
        """无 Tool 的 Agent 直接越过字段映射门禁，仍保留原 Agent 范围。"""

        _, _, plan = fixture()
        with tempfile.TemporaryDirectory() as directory:
            result = api_design_readiness_gate({"workspace": directory, "project_plan": plan,
                "build_execution_scope": {"type": "agent", "targetId": "assistant"}})
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["clarification"], {})

    def test_page_and_agent_share_agent_claim_and_end_releases_it(self):
        """页面与独立 Agent 互斥，结束目标后必须释放 Agent 及页面依赖锁。"""

        _, _, plan = fixture()
        scope = {"type": "agent", "targetId": "assistant"}
        claims = resolve_execution_resource_claims(plan, scope)
        self.assertIn("agent:assistant", resource_claim_keys(claims))
        page_claims = resolve_execution_resource_claims(plan, {"type": "page", "targetId": plan["pages"][0]["pageId"]})
        self.assertIn("agent:assistant", resource_claim_keys(page_claims))
        with tempfile.TemporaryDirectory() as directory:
            lifecycle = create_application_lifecycle(application_id="sample", application_name="样例", initialization_thread_id="planning")
            lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
            lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
            write_application_lifecycle(directory, lifecycle)
            started = start_workbench_execution(directory, scope="agent", target_id="assistant", page_id=None,
                thread_id="thread", run_id="run", phase="api_design_readiness_gate", resource_claims=claims)
            self.assertEqual(started.resource_locks.agents["assistant"].run_id, "run")
            end_workbench_execution(directory, run_id="run")
            self.assertFalse(load_application_lifecycle(directory).resource_locks.agents)

    def test_product_contract_rejects_omitted_agent_and_wrong_entry_page(self):
        """技术规划不能静默漏掉已确认产品智能体，也不能自行扩大页面入口。"""

        _, product, plan = fixture()
        self.assertFalse(validate_technical_plan_agent_contracts(plan, product))
        wrong = deepcopy(plan)
        wrong["agent_contracts"] = []
        self.assertTrue(validate_technical_plan_agent_contracts(wrong, product))
        wrong = deepcopy(plan)
        wrong["agent_contracts"][0]["purpose"] = "超出产品范围"
        self.assertTrue(validate_technical_plan_agent_contracts(wrong, product))

    def test_mock_contract_uses_current_runtime_and_cannot_claim_real_integration(self):
        """固定 UI Mock 无需虚构 Java Gateway，完成后仍明确阻断真实集成验收。"""

        _, product, plan = fixture()
        contracts = project_agent_ui_build_contracts(product, plan)
        contract = next(iter(contracts.values()))
        self.assertEqual(contract["runtimeAgentId"], "assistant")
        self.assertNotIn("gatewayEndpointId", contract)
        result = apply_agent_ui_delivery_boundary({"status": "completed"}, [{"source_refs": {"agent_ui": contract}}])
        self.assertEqual(result["delivery_boundary"]["real_integration"], "pending")

    def test_settings_revision_requires_confirmation_and_invalidates_only_its_plan(self):
        """草稿不写正式文件，确认后才更新 Prompt、失效原计划并释放修订租约。"""

        _, product, plan = fixture()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plans = root / ".devagentstudio/plans"
            plans.mkdir(parents=True)
            for name, value in [("technical-plan", plan), ("product-plan", product)]:
                (plans / f"{name}.json").write_text(json.dumps(value, ensure_ascii=False))
            lifecycle = create_application_lifecycle(application_id="sample", application_name="样例", initialization_thread_id="planning")
            lifecycle.initialization.stage = ApplicationLifecycleStage.READY_FOR_WORKBENCH
            lifecycle.initialization.status = ApplicationLifecycleStatus.COMPLETED
            write_application_lifecycle(root, lifecycle)
            identity = self._execute({"action": "get_agent_settings_revision", "workspaceRoot": directory, "agentId": "assistant"})
            (plans / "build-task-plan.json").write_text(json.dumps({"confirmation_status": "confirmed", "build_context": {
                "target": {"type": "agent", "id": "assistant"}, "contract_hash": identity["contractHash"]}}))
            prepared = self._execute({"action": "prepare_agent_settings_revision", "workspaceRoot": directory,
                "agentId": "assistant", "basedOnContractHash": identity["contractHash"],
                "basedOnTechnicalPlanSha256": identity["technicalPlanSha256"], "changedSections": ["model"],
                "settingsPatch": {"model": {"generation": {"temperature": 0.5}}}})
            self.assertEqual(json.loads((plans / "technical-plan.json").read_text()), plan)
            confirmed = self._execute({"action": "confirm_agent_settings_revision", "workspaceRoot": directory,
                "agentId": "assistant", "changeId": prepared["changeId"], "draftSha256": prepared["draftSha256"],
                "basedOnLifecycleRevision": prepared["basedOnLifecycleRevision"]})
            self.assertFalse(confirmed["active"])
            saved = json.loads((plans / "technical-plan.json").read_text())
            self.assertEqual(saved["agent_contracts"][0]["agentSettings"]["model"]["generation"]["temperature"], 0.5)
            self.assertEqual(saved["topology"], plan["topology"])
            self.assertEqual(json.loads((plans / "build-task-plan.json").read_text())["confirmation_status"], "stale")
            self.assertIsNone(load_application_lifecycle(root).active_formal_revision)

    def _execute(self, value):
        """以当前严格请求模型执行一次本地配置动作。"""

        return execute_agent_settings_revision(parse_agent_settings_revision_request(value), source_thread_id="thread", source_run_id="run")[0]
