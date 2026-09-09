from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.design_conversation import (
    DesignConversationDecision,
    classify_design_conversation,
    is_natural_language_confirmation,
    product_conversation_response,
    resolve_design_target,
)


class ProductConversationPolicyTests(unittest.TestCase):
    """验证产品自由输入的语义分类和确定性能力门禁。"""

    def _fallback(self, request: str) -> DesignConversationDecision:
        """强制模型调用失败，读取不依赖外部服务的保守分类结果。"""

        with patch(
            "app.agents.design_conversation.router.create_chat_model",
            side_effect=RuntimeError("model unavailable"),
        ):
            return classify_design_conversation(
                request,
                requirement_spec={
                    "confirmation_status": "confirmed",
                    "user_roles": [
                        {"id": "admin", "name": "管理员"},
                        {"id": "member", "name": "普通用户"},
                    ],
                },
                product_plan={
                    "confirmation_status": "confirmed",
                    "pages": [
                        {
                            "pageId": "orders",
                            "name": "订单页",
                            "actions": [{"actionId": "archive", "name": "归档"}],
                        }
                    ],
                },
                ui_designs={"confirmation_status": "confirmed"},
                settings=SimpleNamespace(),
            )

    def test_fallback_corpus_respects_product_semantic_levels(self) -> None:
        """需求、产品行为和 UI 语料必须映射到三种受限语义组合。"""

        cases = {
            "增加供应商管理模块": ("requirement_change", "requirement"),
            "订单支持批量归档": ("requirement_change", "product_behavior"),
            "首页改成左侧导航": ("ui_change", "ui"),
        }
        for request, expected in cases.items():
            with self.subTest(request=request):
                decision = self._fallback(request)
                self.assertEqual((decision.intent, decision.change_level), expected)

    def test_read_only_questions_never_resolve_to_mutation_target(self) -> None:
        """产品事实问答只能回复，不能进入任何正式产物节点。"""

        for request in ("当前有哪些角色？", "有哪些页面？", "订单页有什么操作？"):
            with self.subTest(request=request):
                decision = self._fallback(request)
                self.assertEqual(decision.intent, "read_only")
                self.assertIsNone(
                    resolve_design_target(
                        decision,
                        requirement_spec={"confirmation_status": "confirmed"},
                        product_plan={"confirmation_status": "confirmed"},
                    )
                )
                self.assertTrue(product_conversation_response(decision))

    def test_out_of_scope_uses_coordinator_boundary_response(self) -> None:
        """越界结果优先展示 Coordinator 给出的具体拒绝和阶段指引。"""

        decision = DesignConversationDecision(
            intent="out_of_scope",
            change_level="none",
            reason="目标是另一个工程的代码修复",
            response=(
                "这个请求不属于当前产品设计阶段，而且目标是另一个工程。"
                "请切换到对应工程的开发阶段处理；当前应用的需求和 UI 不会发生变化。"
            ),
            suggested_phase="development",
        )

        self.assertEqual(product_conversation_response(decision), decision.response)

    def test_natural_language_confirmation_only_matches_review_claims(self) -> None:
        """自由文本确认只用于阻止误报，不影响对确认控件的真实修改需求。"""

        for request in ("我确认了", "那我确认了，你继续规划吧", "没问题，继续吧"):
            with self.subTest(request=request):
                self.assertTrue(is_natural_language_confirmation(request))
        for request in ("把确认按钮改成紫色", "修改确认页面布局"):
            with self.subTest(request=request):
                self.assertFalse(is_natural_language_confirmation(request))

    def test_policy_only_grants_three_whitelisted_semantic_pairs(self) -> None:
        """只有三种产品语义组合可以获得正式节点权限。"""

        cases = {
            ("requirement_change", "requirement"): "requirements",
            ("requirement_change", "product_behavior"): "product_planning",
            ("ui_change", "ui"): "ui_confirmation",
        }
        for semantic, target in cases.items():
            with self.subTest(semantic=semantic):
                decision = DesignConversationDecision(
                    intent=semantic[0],
                    change_level=semantic[1],
                    reason="白名单授权测试",
                )
                self.assertEqual(
                    resolve_design_target(
                        decision,
                        requirement_spec={"confirmation_status": "confirmed"},
                        product_plan={"confirmation_status": "confirmed"},
                    ),
                    target,
                )

    def test_policy_guards_unconfirmed_upstream_artifacts(self) -> None:
        """确定性 Policy 必须把正式修改拉回最早未确认的上游。"""

        decision = DesignConversationDecision(
            intent="ui_change",
            change_level="ui",
            reason="只调整布局",
        )
        self.assertEqual(
            resolve_design_target(
                decision,
                requirement_spec={"confirmation_status": "pending_user_confirmation"},
                product_plan={"confirmation_status": "confirmed"},
            ),
            "requirements",
        )
        self.assertEqual(
            resolve_design_target(
                decision,
                requirement_spec={"confirmation_status": "confirmed"},
                product_plan={"confirmation_status": "pending_user_confirmation"},
            ),
            "product_planning",
        )

    def test_policy_rejects_invalid_semantic_combinations(self) -> None:
        """模型即使给出合法枚举的错误组合也不能获得正式节点权限。"""

        invalid = (
            ("requirement_change", "ui"),
            ("chat", "requirement"),
            ("out_of_scope", "product_behavior"),
        )
        for intent, level in invalid:
            with self.subTest(intent=intent, level=level):
                decision = DesignConversationDecision(
                    intent=intent,
                    change_level=level,
                    reason="异常组合",
                )
                self.assertIsNone(
                    resolve_design_target(
                        decision,
                        requirement_spec={"confirmation_status": "confirmed"},
                        product_plan={"confirmation_status": "confirmed"},
                    )
                )

    def test_unknown_page_ids_are_removed_from_valid_model_output(self) -> None:
        """Coordinator 只能回传当前 ProductPlan 中存在的 pageId。"""

        model = SimpleNamespace(
            invoke=lambda _: SimpleNamespace(
                content=json.dumps(
                    {
                        "intent": "ui_change",
                        "change_level": "ui",
                        "reason": "调整订单页布局",
                        "affected_page_ids": ["orders", "unknown"],
                        "response": "",
                        "suggested_phase": "none",
                        "clarification_question": "",
                    },
                    ensure_ascii=False,
                )
            )
        )
        with patch(
            "app.agents.design_conversation.router.create_chat_model",
            return_value=model,
        ):
            decision = classify_design_conversation(
                "订单页改成两列",
                requirement_spec={"confirmation_status": "confirmed"},
                product_plan={
                    "confirmation_status": "confirmed",
                    "pages": [{"pageId": "orders", "name": "订单页"}],
                },
                ui_designs={"confirmation_status": "confirmed"},
                settings=SimpleNamespace(),
            )
        self.assertEqual(decision.affected_page_ids, ["orders"])

    def test_model_output_with_technical_node_defaults_to_out_of_scope(self) -> None:
        """旧 Graph target 或技术节点输出必须直接降级为零写入结果。"""

        model = SimpleNamespace(
            invoke=lambda _: SimpleNamespace(
                content='{"target":"technical_planning","reason":"修改 API"}'
            )
        )
        with patch(
            "app.agents.design_conversation.router.create_chat_model",
            return_value=model,
        ):
            decision = classify_design_conversation(
                "增加订单筛选",
                requirement_spec={"confirmation_status": "confirmed"},
                product_plan={"confirmation_status": "confirmed"},
                ui_designs={"confirmation_status": "confirmed"},
                settings=SimpleNamespace(),
            )
        self.assertEqual(decision.intent, "out_of_scope")
        self.assertEqual(decision.suggested_phase, "none")


if __name__ == "__main__":
    unittest.main()
