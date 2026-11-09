"""验证独立片段预算、反馈隔离及外层不得放大模型尝试次数。"""

from collections import Counter
from unittest import TestCase
from unittest.mock import Mock, patch

from app.agents.main import planner
from app.agents.main.technical_plan_fragment_retry import PlanningFragmentFailed, generate_fragment
from app.agents.main.technical_plan_generation import generate_technical_plan_sections
from app.graph.nodes import planning
from app.services.run_workers import bind_run_workers
from app.services.workspace_process_registry import workspace_process_registry
from tests.test_technical_plan_modular_generation import modular_fixture, fragment_outputs
from tests.test_technical_plan_contract_batches import batch_fixture, BatchModel, batch_context


class TechnicalPlanFragmentRetryTests(TestCase):
    """只替换模型响应，执行生产片段解析、局部校验、拼接与外层错误收口。"""

    def _generate(self, requirement, invoke, *, progress=None):
        """共用真实外层生成入口，全量校验在专项 Workflow 回归中验证。"""

        with patch.object(planner, "_invoke_prompt_with_chat_model", side_effect=invoke), patch.object(
            planning, "_project_plan_validation_errors", return_value=[],
        ), patch.object(planning, "_planning_token_callback"):
            return planning._generate_valid_technical_plan({}, requirement, None, generation_fragments=progress)

    def test_every_catalog_contract_and_page_gets_three_independent_attempts(self):
        """所有片段都前两次格式错误、第三次成功，不共享三次总预算或重复已成功片段。"""

        requirement, catalog, contracts, pages = modular_fixture()
        outputs = fragment_outputs(catalog, contracts, pages)
        responses = iter([value for output in outputs for value in ('{"broken":', '{}', output)])
        prompts = []

        def invoke(prompt, **kwargs):
            """记录三次尝试的真实上下文，不用统一成功假结果跳过片段校验。"""

            prompts.append(prompt)
            return next(responses)

        plan, errors, _ = self._generate(requirement, invoke)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(plan["api_contracts"], contracts)
        self.assertEqual(plan["pages"], pages)
        self.assertEqual(len(prompts), len(outputs) * 3)
        for index in range(0, len(prompts), 3):
            self.assertNotIn("当前片段第", prompts[index])
            self.assertIn("当前片段第 1 次", prompts[index + 1])
            self.assertIn("当前片段第 2 次", prompts[index + 2])
            self.assertNotIn("当前片段第 1 次", prompts[index + 2])
            self.assertTrue(all(requirement["planning_adjustment_request"] in prompt for prompt in prompts[index:index + 3]))

    def test_schema_plan_field_groups_and_endpoint_batches_have_independent_budgets(self):
        """大 Contract 的目录、各字段组和各接口批次分别三次后成功，邻居不受影响。"""

        requirement, catalog, contract, page, schema_plan = batch_fixture(fields=70)
        model = BatchModel(catalog, contract, page, schema_plan)
        calls = Counter()

        def invoke(prompt, **kwargs):
            """按实际 Schema 或接口组身份注入两次格式错误，随后返回当前组真实内容。"""

            stage = prompt.splitlines()[0]
            if "contract_schema_fields" in stage:
                context = batch_context(prompt)
                identity = (stage, context["schema"]["name"], tuple(context["fields"]))
            elif "contract_endpoints" in stage:
                identity = (stage, tuple(e["id"] for e in batch_context(prompt)["endpoints"]))
            elif "contract_schema_plan" in stage:
                identity = (stage,)
            else:
                return model(prompt)
            calls[identity] += 1
            return '{"broken":' if calls[identity] < 3 else model(prompt)

        plan, errors, _ = self._generate(requirement, invoke)
        self.assertIsNotNone(plan, errors)
        self.assertEqual(plan["api_contracts"], [contract])
        self.assertGreater(len(calls), 3)
        self.assertEqual(set(calls.values()), {3})
        self.assertEqual(sum(map(len, model.field_groups)), 70)
        self.assertEqual(sum(map(len, model.endpoint_groups)), len(contract["endpoints"]))

    def test_exhaustion_after_another_module_retried_does_not_replay_outer_loop(self):
        """前一模块第三次成功后，后一模块仍有三次；耗尽即停，不开始页面或重放外层。"""

        requirement, catalog, contracts, pages = modular_fixture()
        outputs = fragment_outputs(catalog, contracts, pages)
        catalog_count = len(outputs) - len(contracts) - len(pages)
        queue = iter(outputs[:catalog_count] + ['{"broken":'] * 2 + [outputs[catalog_count]] + ['{"broken":'] * 3)
        calls = []

        def invoke(prompt, **kwargs):
            """响应队列耗尽将直接使测试失败，暴露任何额外自动调用。"""

            calls.append(prompt)
            return next(queue)

        progress = {}
        plan, errors, _ = self._generate(requirement, invoke, progress=progress)
        self.assertIsNone(plan)
        self.assertIn("contract:customers_api", errors[0])
        self.assertIn("已尝试 3 次", errors[0])
        self.assertEqual(len(calls), catalog_count + 6)
        self.assertEqual(set(progress["completed"]), {"catalog", "contract:orders_api"})
        remaining = iter(outputs[catalog_count + 1:])
        resumed = generate_technical_plan_sections(requirement, invoke=lambda prompt: next(remaining), fragments=progress)
        self.assertEqual(resumed["pages"], pages)

    def test_only_latest_validation_feedback_is_used(self):
        """当前片段每次只收到最新诊断，成功后其他片段没有这份错误。"""

        prompts, cache = [], {}
        responses = iter(['{"error":"第一次专属诊断"}', '{"error":"第二次专属诊断"}', '{"ok":true}'])

        def invoke(prompt):
            """收集提示词并返回连续两种校验问题。"""

            prompts.append(prompt)
            return next(responses)

        def validate(value):
            """模拟业务校验，以验证诊断来自本次错误而非累计历史。"""

            if "error" in value:
                raise ValueError(value["error"])
            return value

        save = Mock()
        generate_fragment(cache, "page:first", "阶段\n原始业务要求", invoke, validate, save)
        self.assertIn("第一次专属诊断", prompts[1])
        self.assertNotIn("第一次专属诊断", prompts[2])
        self.assertIn("第二次专属诊断", prompts[2])
        neighbor = Mock(return_value='{"ok":true}')
        generate_fragment(cache, "page:next", "阶段\n原始业务要求", neighbor, validate, save)
        neighbor.assert_called_once_with("阶段\n原始业务要求")

    def test_storage_and_model_boundary_errors_do_not_retry_generation(self):
        """保存拒绝或模型配置失败都不反复生成，未保存候选不能进入成功缓存。"""

        for phase in ("save", "model"):
            with self.subTest(phase=phase):
                invoke = Mock(return_value='{"ok":true}')
                save = Mock()
                if phase == "save":
                    save.side_effect = ValueError("输入已变化")
                else:
                    invoke.side_effect = ValueError("模型配置错误")
                cache = {}
                with self.assertRaises(PlanningFragmentFailed) as caught:
                    generate_fragment(cache, "page:test", "阶段\n原始业务要求", invoke, dict, save)
                self.assertEqual(caught.exception.attempts, 1)
                self.assertEqual(invoke.call_count, 1)
                self.assertEqual(cache, {})

    def test_cancel_after_invalid_output_prevents_next_attempt(self):
        """模型输出错误同时收到停止时，局部重试也必须在下一请求前退出。"""

        run_id = "fragment-retry-cancellation"
        calls = []

        def invoke(prompt):
            """首轮无效输出后模拟用户停止，确认不会发出第二轮模型请求。"""

            calls.append(prompt)
            workspace_process_registry.cancel_run(run_id)
            return '{"broken":'

        try:
            with bind_run_workers(run_id, ""), self.assertRaisesRegex(RuntimeError, "当前运行已停止"):
                generate_fragment({}, "page:test", "阶段\n要求", invoke, dict, Mock())
        finally:
            workspace_process_registry.allow_run(run_id)
        self.assertEqual(len(calls), 1)
