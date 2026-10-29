"""技术规划自动修复持续保留用户原始修改要求的回归测试。"""

from copy import deepcopy
from unittest import TestCase
from unittest.mock import patch

from app.agents.main.planner import (
    _technical_action_binding_repair_prompt,
    _technical_contract_repair_prompt,
    _technical_planning_prompt,
)
from app.agents.main.technical_plan_language import repair_technical_plan_chinese_prose
from app.graph.nodes import planning


class TechnicalPlanRepairFeedbackTests(TestCase):
    """验证生成、定向修复、确认复验和语言修正不会丢失用户要求。"""

    def setUp(self) -> None:
        """准备多条原始要求及独立的系统校验错误。"""

        self.request = "将订单分页默认值改为8。\n保留所有其他接口，不要新增导出接口。"
        self.requirement = {
            "confirmed_product_plan": {"pages": []},
            "planning_adjustment_request": self.request,
        }
        self.plan = {
            "artifact_type": "technical-plan",
            "architecture": {}, "entities": [], "api_contracts": [], "pages": [],
        }

    def test_generation_and_every_repair_preserve_original_request(self) -> None:
        """首次生成及连续两轮修复必须保留原文，系统反馈只保留本轮诊断。"""

        original = deepcopy(self.requirement)
        with (
            patch.object(planning, "plan_project_with_chat_model", return_value=deepcopy(self.plan)) as generate,
            patch.object(planning, "_repair_technical_plan_candidate", side_effect=lambda *args: deepcopy(self.plan)) as repair,
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", side_effect=[["第一轮 Schema 错误"], ["第二轮绑定错误"], []]),
        ):
            plan, errors, failed = planning._generate_valid_technical_plan({}, self.requirement, None)

        self.assertIsNotNone(plan)
        self.assertEqual(errors, [])
        self.assertIsNone(failed)
        inputs = [generate.call_args.args[0], *[call.args[0] for call in repair.call_args_list]]
        self.assertEqual(len(inputs), 3)
        for requirement in inputs:
            self.assertEqual(requirement["planning_adjustment_request"], self.request)
        self.assertIn("第一轮 Schema 错误", inputs[1]["technical_plan_validation_feedback"])
        self.assertNotIn("返回完整 TechnicalPlan", inputs[1]["technical_plan_validation_feedback"])
        self.assertIn("第二轮绑定错误", inputs[2]["technical_plan_validation_feedback"])
        self.assertNotIn("第一轮 Schema 错误", inputs[2]["technical_plan_validation_feedback"])
        self.assertEqual(self.requirement, original)

    def test_parse_failure_keeps_request_when_full_generation_repeats(self) -> None:
        """没有可用候选的解析失败重生成，也必须同时携带原始要求与错误。"""

        with (
            patch.object(planning, "plan_project_with_chat_model", side_effect=[ValueError("JSON 根对象不完整"), deepcopy(self.plan)]) as generate,
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", return_value=[]),
        ):
            plan, errors, _ = planning._generate_valid_technical_plan({}, self.requirement, None)

        self.assertIsNotNone(plan)
        self.assertEqual(errors, [])
        for call in generate.call_args_list:
            self.assertEqual(call.args[0]["planning_adjustment_request"], self.request)
        self.assertIn("JSON 根对象不完整", generate.call_args.args[0]["technical_plan_validation_feedback"])

    def test_failed_candidate_reentry_keeps_original_request(self) -> None:
        """已有失败候选从错误反馈开始修复时，也不能覆盖原始要求。"""

        with (
            patch.object(planning, "_repair_technical_plan_candidate", return_value=deepcopy(self.plan)) as repair,
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", side_effect=[["候选 Schema 错误"], []]),
        ):
            plan, errors, _ = planning._generate_valid_technical_plan(
                {}, self.requirement, deepcopy(self.plan), initial_errors=["候选 Schema 错误"],
            )

        self.assertIsNotNone(plan)
        self.assertEqual(errors, [])
        self.assertEqual(repair.call_args.args[0]["planning_adjustment_request"], self.request)
        self.assertIn("候选 Schema 错误", repair.call_args.args[0]["technical_plan_validation_feedback"])

    def test_confirmation_validation_repair_reads_business_request(self) -> None:
        """确认复验修复读取当前修改或已冻结要求，不把确认动作当成修改意见。"""

        cases = [
            ({"action": "revise", "request": self.request}, "过期要求"),
            ({"action": "confirm"}, self.request),
        ]
        for interaction, frozen_request in cases:
            with self.subTest(action=interaction["action"]):
                state = {
                    "requirement_spec": {}, "request": "确认当前版本",
                    "design_change_generation_request": frozen_request,
                    "application_planning_interaction": interaction,
                }
                original = deepcopy(state)
                with (
                    patch.object(planning, "_technical_planning_requirement_spec", return_value={"confirmed_product_plan": {"pages": []}}),
                    patch.object(planning, "_repair_technical_plan_candidate", side_effect=lambda *args: deepcopy(self.plan)) as repair,
                    patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
                    patch.object(planning, "_project_plan_validation_errors", side_effect=[["后续错误"], []]),
                ):
                    _, errors = planning._repair_technical_plan_validation_errors(
                        state, deepcopy(self.plan), ["初始错误"], repair_attempts=3,
                    )
                self.assertEqual(errors, [])
                self.assertEqual(repair.call_count, 2)
                for call in repair.call_args_list:
                    self.assertEqual(call.args[0]["planning_adjustment_request"], self.request)
                self.assertIn("初始错误", repair.call_args_list[0].args[0]["technical_plan_validation_feedback"])
                self.assertIn("后续错误", repair.call_args_list[1].args[0]["technical_plan_validation_feedback"])
                self.assertEqual(state, original)

    def test_all_generation_and_scoped_repair_prompts_include_both_inputs(self) -> None:
        """完整生成、Contract 修复及动作绑定修复都显式区分原始意见和系统诊断。"""

        requirement = {**self.requirement, "technical_plan_validation_feedback": "必须修复本轮响应 Schema 引用。"}
        prompts = [
            _technical_planning_prompt(requirement, self.plan),
            _technical_contract_repair_prompt(requirement, self.plan, ["Schema 引用错误"], []),
            _technical_action_binding_repair_prompt(requirement, self.plan, []),
        ]
        for prompt in prompts:
            with self.subTest(prompt=prompt[:60]):
                self.assertIn(self.request, prompt)
                self.assertIn(requirement["technical_plan_validation_feedback"], prompt)
                self.assertIn("planning_adjustment_request", prompt)
                self.assertIn("technical_plan_validation_feedback", prompt)

    def test_language_repair_also_preserves_request_context(self) -> None:
        """中文说明修复收到同一份原始要求，仍只翻译指定字段。"""

        prompts = []

        def translate(prompt: str) -> str:
            """记录实际翻译提示词并返回指定字段的中文翻译。"""

            prompts.append(prompt)
            return '{"translations": {"architecture.frontend": "前端使用 React。"}}'

        repaired = repair_technical_plan_chinese_prose(
            {"architecture": {"frontend": "React frontend"}},
            invoke_model=translate, planning_context=self.requirement,
        )
        self.assertIn(self.request, prompts[0])
        self.assertEqual(repaired["architecture"]["frontend"], "前端使用 React。")

    def test_exhausted_repair_budget_keeps_request_and_failed_candidate(self) -> None:
        """三轮耗尽后保留失败候选，所有修复仍收到完整原文。"""

        with (
            patch.object(planning, "plan_project_with_chat_model", return_value=deepcopy(self.plan)) as generate,
            patch.object(planning, "_repair_technical_plan_candidate", side_effect=lambda *args: deepcopy(self.plan)) as repair,
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", return_value=["持续校验失败"]),
        ):
            plan, errors, failed = planning._generate_valid_technical_plan({}, self.requirement, None)

        self.assertIsNone(plan)
        self.assertEqual(errors, ["持续校验失败"])
        self.assertIsNotNone(failed)
        self.assertEqual(generate.call_count + repair.call_count, 3)
        for call in [*generate.call_args_list, *repair.call_args_list]:
            self.assertEqual(call.args[0]["planning_adjustment_request"], self.request)

    def test_scoped_repair_fallback_preserves_request_in_full_plan_prompt(self) -> None:
        """动作绑定定向修复拒绝或失败后，完整修复仍使用同一份原始意见。"""

        requirement = {**self.requirement, "technical_plan_validation_feedback": "修复缺失业务动作绑定。"}
        for response in [
            {"status": "requires_full_repair", "reason": "no_suitable_endpoint", "bindings": []},
            ValueError("定向修复返回非法 JSON"),
        ]:
            with self.subTest(response=response):
                with (
                    patch.object(planning, "technical_action_binding_issues", return_value=[{"pageId": "orders", "actionId": "save"}]),
                    patch.object(planning, "technical_action_binding_issue_messages", return_value=["绑定缺失"]),
                    patch.object(planning, "repair_technical_plan_action_bindings_with_chat_model", side_effect=[response]) as scoped,
                    patch.object(planning, "validate_technical_action_binding_patch", return_value=[]),
                    patch.object(planning, "_technical_plan_contract_validation_errors", return_value=[]),
                    patch.object(planning, "plan_project_with_chat_model", return_value=deepcopy(self.plan)) as full,
                ):
                    planning._repair_technical_plan_candidate(requirement, self.plan, ["绑定缺失"])

                self.assertEqual(scoped.call_args.args[0]["planning_adjustment_request"], self.request)
                prompt = _technical_planning_prompt(full.call_args.args[0], self.plan)
                self.assertIn(self.request, prompt)
                self.assertIn(requirement["technical_plan_validation_feedback"], prompt)
