"""旧聊天稿退出当前生成与确认，不删除用户保存的源文件。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.graph.nodes.ui_confirmation import _product_plan_hash, _verified_ui_designs_for_confirmation
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.ui_design_generator import generate_adjusted_page_react_code
from app.services.ui_design_manifest import build_ui_page_manifest


class UiDesignTemplateAdaptationTests(unittest.TestCase):
    """单文件生成、调整与正式确认均不能恢复旧固定聊天组件。"""

    def setUp(self) -> None:
        """构造产品事实和已退出当前合同的固定组件源码。"""

        self.product = {
            "pages": [{
                "pageId": "chat", "name": "咨询对话页",
                "information_items": [
                    {"itemId": "chat_history", "label": "对话记录"},
                    {"itemId": "chat_input", "label": "问题输入区"},
                    {"itemId": "chat_status", "label": "助手处理状态"},
                ],
                "actions": [{"actionId": "send_question"}, {"actionId": "agent_interact"}],
            }],
            "agents": [{
                "agentId": "assistant", "name": "咨询助手", "purpose": "回答咨询问题。",
                "pageActionBindings": [{
                    "pageId": "chat", "actionIds": ["agent_interact"],
                    "surface": {"type": "standalone_page", "enabled": True, "contextItemIds": []},
                }],
            }],
        }
        self.page = project_ui_design_pages(self.product)[0]
        self.code = "import { AgentConversationTemplate } from '@xcodeagent/agent-ui-design'; export default AgentConversationTemplate"

    def test_template_adaptation_rejects_missing_product_bindings(self) -> None:
        """Agent 页面在调用单文件模型前就明确要求包消费工程。"""

        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=SimpleNamespace(ui_design_max_retries=0),
        ), patch("app.services.ui_design_generator._create_ui_design_model"), patch(
            "app.services.ui_design_generator._invoke_ui_design_model",
            return_value=SimpleNamespace(content=self.code),
        ):
            with self.assertRaises(ValueError) as failure:
                generate_adjusted_page_react_code(
                    self.page, "Chat", "", self.code, "适配当前产品页面", allow_missing=False,
                )
        self.assertIn("多文件设计工程", str(failure.exception))

    def test_explicit_adjustment_retains_its_missing_binding_policy(self) -> None:
        """明确调整也不能授权单文件模型复活旧 Agent 模板。"""

        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=SimpleNamespace(ui_design_max_retries=0),
        ), patch("app.services.ui_design_generator._create_ui_design_model"), patch(
            "app.services.ui_design_generator._invoke_ui_design_model",
            return_value=SimpleNamespace(content=self.code),
        ), patch("app.services.ui_design_generator.validate_tsx", return_value=(True, "")):
            with self.assertRaisesRegex(ValueError, "多文件设计工程"):
                generate_adjusted_page_react_code(
                    self.page, "Chat", "", self.code, "删除已指定的视觉区域",
                )

    def test_failed_confirmation_clears_the_page_confirmed_status(self) -> None:
        """最终复验发现缺项时回显生成失败，不能在错误提示下继续展示已确认页面。"""

        with TemporaryDirectory() as workspace:
            state = {"workspace": str(Path(workspace)), "product_plan": self.product}
            manifest = {
                "product_plan_sha256": _product_plan_hash(state),
                "pages": [build_ui_page_manifest(
                    self.page, page_key="Chat", code=self.code, status="confirmed",
                )],
            }
            verified, errors = _verified_ui_designs_for_confirmation(state, manifest)
        self.assertTrue(errors)
        self.assertEqual(verified["pages"][0]["status"], "generation_failed")
        self.assertIn("重新生成或审阅", verified["pages"][0]["error"])
        self.assertEqual(verified["confirmation_status"], "pending_user_confirmation")
