"""多文件设计的真实交互必须进入清单，并在模型生成前完成上游检查。"""

from copy import deepcopy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.graph.nodes.planning import _technical_planning_requirement_spec
from app.services.agent_ui_design_confirmation import verify_agent_design_page
from app.services.agent_ui_design_workflow import generate_agent_project
from app.services.page_implementation_contract import build_page_implementation_contracts
from app.services.ui_design_agent_surfaces import project_agent_design_pages
from app.services.ui_design_generation_pool import UiDesignGenerationTask
from app.services.ui_design_interactions import agent_project_page_bindings, require_ui_interaction_effects
from tests.test_agent_ui_design_generator import formal_facts


class AgentUiDesignInteractionTests(unittest.TestCase):
    """隔离模型与进程，保留真实源码解析、清单生成和页面合同编译。"""

    def setUp(self) -> None:
        """准备首页本地开关动作及独立 Agent 页，源码效果与产品描述刻意不同。"""
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.requirement, self.product = formal_facts()
        self.product["pages"][0]["actions"] += [
            {"actionId": "open_panel", "behavior": {"type": "interface", "expectedResult": "产品预期打开"}},
            {"actionId": "close_panel", "behavior": {"type": "interface", "expectedResult": "产品预期关闭"}},
        ]
        self.product["pages"][0]["information_items"] = [{"itemId": "welcome"}]
        self.pages = project_agent_design_pages(self.requirement, self.product)
        self.project = SimpleNamespace(workspace=self.workspace, project_dir=self.workspace / "consumer",
            identity="environment", source_digest="source", build_digest="build")
        source = self.project.project_dir / "src/pages/page-0.tsx"
        source.parent.mkdir(parents=True)
        self.code = '''export default function Page({onOpenAgentPanel, onCloseAgentPanel}) {
          return <main><p data-information-item-id="welcome" data-control-id="welcome">欢迎</p>
            <button data-action-id="open_panel" data-control-id="open" data-ui-effect="展开当前共享浮窗"
              onClick={onOpenAgentPanel}>打开</button>
            <button data-action-id="close_panel" data-control-id="close" data-ui-effect="收起当前共享浮窗"
              onClick={onCloseAgentPanel}>关闭</button></main>
        }'''
        source.write_text(self.code, encoding="utf-8")
        self.state = {"workspace": str(self.workspace), "workflow_scope": "application_planning",
            "requirement_spec": self.requirement, "product_plan": self.product}

    def generate(self) -> dict:
        """运行实际清单生产者，仅替换外部生成、环境和预览副作用。"""
        members = tuple(UiDesignGenerationTask(workspace=str(self.workspace), project_id="test",
            project_dir=str(self.workspace), page_id=page["pageId"], spec_page=page,
            page_key=page["pageId"].title(), action="regenerate", requirement_spec=self.requirement,
            product_plan=self.product) for page in self.pages)
        from dataclasses import replace
        task = replace(members[0], members=members)
        design = SimpleNamespace(project=self.project, revision="revision", facts_digest="facts",
                                 affected_page_ids=("home", "chat"))
        owner = "app.services.agent_ui_design_workflow."
        with patch(owner + "design_environment", return_value=(SimpleNamespace(node_executable=Path("node")), Path("pnpm"))), \
                patch(owner + "retained_page_sources", return_value={}), \
                patch(owner + "retained_presentations", return_value={}), \
                patch(owner + "retained_agent_sources", return_value={}), \
                patch(owner + "generate_agent_ui_design", return_value=design), \
                patch(owner + "agent_ui_design_previews"):
            return {"confirmation_status": "pending_user_confirmation",
                    "pages": generate_agent_project(task, None)["project_pages"]}

    def test_generated_source_effects_survive_manifest_and_contract_compilation(self) -> None:
        """首页开关从已构建源码投影，正式本地绑定不得被技术规划过滤掉。"""
        manifest = self.generate()
        home = manifest["pages"][0]
        actions = {item["actionId"]: item for item in home["bindings"]["actions"]}
        self.assertEqual(actions["open_panel"].get("uiEffect"), "展开当前共享浮窗")
        self.assertEqual(actions["close_panel"].get("uiEffect"), "收起当前共享浮窗")
        self.assertEqual(actions["open_panel"]["controlIds"], ["open"])
        self.assertEqual(home["bindings"]["information_items"][0]["controlIds"], ["welcome"])
        self.assertEqual(home["status"], "ready")
        self.assertNotIn("asset_confirmation", home)
        plan = {"artifact_type": "technical-plan", "pages": [
            {"pageId": page["pageId"], "references": {}} for page in self.pages]}
        contract = build_page_implementation_contracts(plan, self.product, manifest)[0]
        bindings = {item["actionId"]: item for item in contract["actionBindings"]}
        self.assertEqual(bindings["open_panel"]["localEffect"], "展开当前共享浮窗")
        self.assertEqual(bindings["close_panel"]["localEffect"], "收起当前共享浮窗")

    def test_confirmation_projects_current_source_without_changing_original(self) -> None:
        """显式确认读取当前源码，不能把缺失绑定的旧清单直接标记成功。"""
        home = self.generate()["pages"][0]
        for action in home["bindings"]["actions"]:
            action.pop("uiEffect", None)
        original = deepcopy(home)
        with patch("app.services.agent_ui_design_projection.current_project", return_value=(self.project, home["design_asset"])), \
                patch("app.services.agent_ui_design_confirmation.agent_design_evidence", return_value={"pageId": "home"}):
            confirmed = verify_agent_design_page(self.state, home)
        actions = {item["actionId"]: item for item in confirmed["bindings"]["actions"]}
        self.assertEqual(actions["open_panel"].get("uiEffect"), "展开当前共享浮窗")
        self.assertEqual(home, original)

    def test_missing_or_incomplete_source_cannot_supply_business_bindings(self) -> None:
        """源码缺失或没有真实效果时，不用产品预期伪造已设计交互。"""
        with self.assertRaises(FileNotFoundError):
            agent_project_page_bindings(self.workspace / "missing", self.pages[0], ["home", "chat"])
        source = self.project.project_dir / "src/pages/page-0.tsx"
        source.write_text(self.code.replace(' data-ui-effect="展开当前共享浮窗"', ''), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "open_panel"):
            agent_project_page_bindings(self.project.project_dir, self.pages[0], ["home", "chat"])

    def test_preflight_requires_sequence_ui_steps_and_preserves_explicit_skip(self) -> None:
        """组合动作逐个校验界面步骤；明确跳过 UI 不增加新的确认要求。"""
        manifest = self.generate()
        require_ui_interaction_effects(self.product, manifest)
        sequence = {"actionId": "save", "behavior": {"type": "sequence", "steps": [
            {"stepId": "submit", "type": "service"},
            {"stepId": "show", "type": "interface", "expectedResult": "展示结果"},
        ]}}
        self.product["pages"][0]["actions"].append(sequence)
        with self.assertRaisesRegex(ValueError, "home/save/show"):
            require_ui_interaction_effects(self.product, manifest)
        manifest["pages"][0]["bindings"]["actions"].append({
            "actionId": "save", "stepEffects": [{"stepId": "show", "uiEffect": "展示保存结果"}]})
        require_ui_interaction_effects(self.product, manifest)
        require_ui_interaction_effects(self.product, {"confirmation_status": "skipped"})

    def test_missing_ui_effect_is_rejected_before_technical_model(self) -> None:
        """上游缺失必须直接说明返回 UI 审阅，不能进入模型修复循环。"""
        config = self.workspace / ".devagentstudio/application.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"auth": {"enable": False}}), encoding="utf-8")
        manifest = self.generate()
        manifest["confirmation_status"] = "confirmed"
        for action in manifest["pages"][0]["bindings"]["actions"]:
            action.pop("uiEffect", None)
        state = {**self.state, "ui_designs": manifest}
        with patch("app.services.agent_ui_design_confirmation.require_confirmed_agent_designs"), \
                self.assertRaisesRegex(ValueError, "UI.*open_panel.*close_panel"):
            _technical_planning_requirement_spec(state, self.requirement)


if __name__ == "__main__":
    unittest.main()
