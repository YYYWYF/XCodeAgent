from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.agents.design_conversation import (
    DesignConversationDecision,
    classify_design_conversation,
    enforce_product_conversation_capabilities,
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
            "requirement": [
                "增加供应商管理模块",
                "增加订单详情页",
                "去掉图片详情页",
                "管理员可以查看所有用户数据",
                "增加审核员角色",
            ],
            "product_behavior": [
                "订单支持批量归档",
                "审批完成后显示成功提示",
                "用户可以从列表跳到详情",
                "列表增加导出操作",
            ],
            "ui": [
                "登录页改成左右布局",
                "首页改成左侧导航",
                "卡片圆角再大一点",
                "订单列表换成两列",
                "增加暗色主题",
            ],
        }
        for level, requests in cases.items():
            for request in requests:
                with self.subTest(level=level, request=request):
                    decision = self._fallback(request)
                    expected_intent = "ui_change" if level == "ui" else "requirement_change"
                    self.assertEqual(decision.intent, expected_intent)
                    self.assertEqual(decision.change_level, level)

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

    def test_out_of_scope_and_mixed_requests_are_fully_blocked(self) -> None:
        """技术、开发、测试和混合请求必须整体越界且没有 Graph target。"""

        cases = {
            "planning": [
                "接口增加 status 参数",
                "把 SQLite 换成 PostgreSQL",
                "给数据库增加索引",
                "修改 Redis 缓存策略",
                "增加订单筛选，并把接口增加 status 参数",
            ],
            "development": ["修改 OrderPage.tsx"],
            "test": ["帮我跑一下测试"],
        }
        for phase, requests in cases.items():
            for request in requests:
                with self.subTest(phase=phase, request=request):
                    decision = self._fallback(request)
                    self.assertEqual(decision.intent, "out_of_scope")
                    self.assertEqual(decision.suggested_phase, phase)
                    self.assertIsNone(
                        resolve_design_target(
                            decision,
                            requirement_spec={"confirmation_status": "confirmed"},
                            product_plan={"confirmation_status": "confirmed"},
                        )
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

        decision = DesignConversationDecision(
            intent="requirement_change",
            change_level="ui",
            reason="异常组合",
        )
        self.assertIsNone(
            resolve_design_target(
                decision,
                requirement_spec={"confirmation_status": "confirmed"},
                product_plan={"confirmation_status": "confirmed"},
            )
        )

    def test_capability_gate_blocks_mixed_request_even_if_model_misclassifies_it(self) -> None:
        """模型漏掉 API 子请求时，确定性门禁仍必须整体拒绝混合输入。"""

        decision = DesignConversationDecision(
            intent="requirement_change",
            change_level="product_behavior",
            reason="模型只识别了筛选行为",
        )
        guarded = enforce_product_conversation_capabilities(
            decision,
            "增加订单筛选，并把接口增加 status 参数",
        )
        self.assertEqual(guarded.intent, "out_of_scope")
        self.assertEqual(guarded.suggested_phase, "planning")
        self.assertEqual(guarded.affected_page_ids, [])
        self.assertIn("不会只执行其中一部分", guarded.response)

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
        self.assertEqual(decision.suggested_phase, "planning")


if __name__ == "__main__":
    unittest.main()
