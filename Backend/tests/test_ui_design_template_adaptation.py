"""固定聊天模板适配必须覆盖全部产品事实，失败不能冒充已确认设计稿。"""

from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.graph.nodes.ui_confirmation import _product_plan_hash, _verified_ui_designs_for_confirmation
from app.services.ui_design_agent_surfaces import project_ui_design_pages
from app.services.ui_design_agent_template import build_agent_ui_template_source_contract
from app.services.ui_design_generator import generate_adjusted_page_react_code
from app.services.ui_design_manifest import build_ui_page_manifest


class UiDesignTemplateAdaptationTests(unittest.TestCase):
    """复现独立聊天组件只绑定 Agent 入口，却遗漏原有发送操作及三个信息项。"""

    def setUp(self) -> None:
        """构造已确认产品事实和缺少页面产品标记的真实固定组件源码。"""

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
        self.code = build_agent_ui_template_source_contract(self.page)

    def test_template_adaptation_rejects_missing_product_bindings(self) -> None:
        """选模板沿真实产品校验拒绝漏项，错误保留发送操作与信息项的稳定标识。"""

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
        self.assertIn("send_question", str(failure.exception))
        self.assertIn("information_items", str(failure.exception))

    def test_explicit_adjustment_retains_its_missing_binding_policy(self) -> None:
        """用户明确调整设计时继续使用原宽松策略，模板适配不改变该入口语义。"""

        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=SimpleNamespace(ui_design_max_retries=0),
        ), patch("app.services.ui_design_generator._create_ui_design_model"), patch(
            "app.services.ui_design_generator._invoke_ui_design_model",
            return_value=SimpleNamespace(content=self.code),
        ), patch("app.services.ui_design_generator.validate_tsx", return_value=(True, "")):
            result = generate_adjusted_page_react_code(
                self.page, "Chat", "", self.code, "删除已指定的视觉区域",
            )
        self.assertEqual(result, self.code)

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
        self.assertIn("send_question", verified["pages"][0]["error"])
        self.assertEqual(verified["confirmation_status"], "pending_user_confirmation")
