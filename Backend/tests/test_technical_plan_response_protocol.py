from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
import json
from typing import Any
import unittest
from unittest.mock import patch

from langchain_core.messages import AIMessage, AIMessageChunk

from app.agents.main import planner, technical_plan_response
from app.config import Settings


class FakeTechnicalPlanModel:
    """使用内存消息模拟模型调用，避免访问真实模型服务。"""

    def __init__(
        self, response: AIMessage, chunks: list[AIMessage | AIMessageChunk] | None = None
    ) -> None:
        """保存完整响应和可选流式片段。"""

        self.response = response
        self.chunks = chunks if chunks is not None else [response]

    def invoke(self, _prompt: str) -> AIMessage:
        """返回带原始结束原因及 token 用量的完整消息。"""

        return self.response

    def stream(self, _prompt: str) -> Iterator[AIMessage | AIMessageChunk]:
        """按既定顺序返回正文片段及空的末尾元数据片段。"""

        yield from self.chunks


class TechnicalPlanResponseProtocolTests(unittest.TestCase):
    """验证技术规划入口只消费完整且唯一的根级 JSON 对象。"""

    def _settings(self) -> Settings:
        """构造不读取真实服务配置的隔离模型设置。"""

        return Settings(
            model_base_url="http://model.invalid/v1",
            model_api_key="test-key",
            model_name="test-model",
        )

    def _assert_rejected_response(self, response_text: str) -> None:
        """确保协议失败发生在技术计划物化之前且错误指出 JSON 边界。"""

        with (
            patch.object(planner.Settings, "from_env", return_value=self._settings()),
            patch.object(planner, "_invoke_live_chat_model", return_value=response_text),
            patch.object(planner, "create_technical_plan") as create_plan,
        ):
            with self.assertRaisesRegex(ValueError, r"TechnicalPlan.*JSON|JSON.*TechnicalPlan"):
                planner.plan_project_with_chat_model({"confirmed_product_plan": {}})

        create_plan.assert_not_called()

    def _assert_accepted_response(
        self, response_text: str, expected_object: dict[str, Any]
    ) -> None:
        """检查合法 JSON 保留完整根对象并进入当前 TechnicalPlan 分支。"""

        requirement_spec: dict[str, Any] = {"confirmed_product_plan": {}}
        expected_plan: dict[str, Any] = {"artifact_type": "technical-plan"}
        with (
            patch.object(planner.Settings, "from_env", return_value=self._settings()),
            patch.object(planner, "_invoke_live_chat_model", return_value=response_text),
            patch.object(
                planner, "create_technical_plan", return_value=expected_plan
            ) as create_plan,
            patch.object(planner, "create_project_plan") as create_project,
        ):
            result = planner.plan_project_with_chat_model(requirement_spec)

        self.assertEqual(result, expected_plan)
        create_plan.assert_called_once_with(
            requirement_spec, agent_plan=expected_object, datasource_type=None
        )
        create_project.assert_not_called()

    def test_truncated_root_does_not_consume_complete_nested_architecture(self) -> None:
        """外层 JSON 截断时不得把内部完整 architecture 对象误当成计划。"""

        self._assert_rejected_response(
            '{"architecture":{"backend":"java","data":"mysql","frontend":"react"},'
            '"entities":[{"name":"unfinished'
        )

    def test_complete_single_root_object_is_accepted(self) -> None:
        """完整根对象允许首尾空白且保留字符串中的花括号。"""

        model_plan: dict[str, Any] = {
            "architecture": {"backend": "java", "description": "literal { braces }"},
            "entities": [],
            "api_contracts": [],
            "pages": [],
            "agent_contracts": [],
        }
        self._assert_accepted_response(
            "\n  " + json.dumps(model_plan) + "  \n", model_plan
        )

    def test_complete_json_fence_is_accepted(self) -> None:
        """仅含一个 JSON 代码围栏的完整对象可以进入技术规划校验。"""

        model_plan: dict[str, Any] = {"architecture": {}, "agent_contracts": []}
        self._assert_accepted_response(
            "\n```json\n" + json.dumps(model_plan) + "\n```\n", model_plan
        )

    def test_extra_text_or_multiple_root_objects_are_rejected(self) -> None:
        """根对象之外的说明、第二个对象或围栏外正文均属于协议错误。"""

        responses: dict[str, str] = {
            "second_object": '{"architecture":{}} {"agent_contracts":[]}',
            "trailing_text": '{"architecture":{}} extra explanation',
            "leading_text": 'Here is the plan: {"architecture":{}}',
            "fence_trailing_text": '```json\n{"architecture":{}}\n```\nextra explanation',
            "second_fence": '```json\n{}\n```\n```json\n{}\n```',
        }
        for name, response_text in responses.items():
            with self.subTest(name=name):
                self._assert_rejected_response(response_text)

    def test_non_object_or_incomplete_root_is_rejected(self) -> None:
        """数组、标量、缺失围栏终止符及不完整根对象均不能进入计划物化。"""

        responses: dict[str, str] = {
            "array": '[{"architecture":{}}]',
            "scalar": '"not a plan"',
            "empty": "",
            "incomplete_root": '{"architecture":{}',
            "incomplete_fence": '```json\n{"architecture":{}}',
        }
        for name, response_text in responses.items():
            with self.subTest(name=name):
                self._assert_rejected_response(response_text)

    def _assert_safe_length_diagnostics(self, log_output: list[str]) -> None:
        """校验结束原因和用量被保留，但正文及其他元数据不会进入日志。"""

        diagnostics = "\n".join(log_output)
        self.assertIn("technical_plan_model_response", diagnostics)
        self.assertIn("finish_reason=length", diagnostics)
        self.assertIn("output_tokens=8192", diagnostics)
        self.assertIn("configured_max_tokens=32768", diagnostics)
        self.assertIn("sha256=", diagnostics)
        self.assertIn("chars=", diagnostics)
        self.assertNotIn("PRIVATE_BODY_SENTINEL", diagnostics)
        self.assertNotIn("PRIVATE_METADATA_SENTINEL", diagnostics)

    def test_invoke_length_rejects_valid_json_and_logs_safe_metadata(self) -> None:
        """同步模型以 length 结束时必须拒绝合法 JSON，并记录安全诊断。"""

        response = AIMessage(
            content='{"private":"PRIVATE_BODY_SENTINEL"}',
            response_metadata={
                "finish_reason": "length",
                "token_usage": {"completion_tokens": 8192},
                "private": "PRIVATE_METADATA_SENTINEL",
            },
        )
        with (
            patch.object(
                technical_plan_response, "create_chat_model",
                return_value=FakeTechnicalPlanModel(response),
            ),
            self.assertLogs("uvicorn.error", level="INFO") as captured,
        ):
            with self.assertRaisesRegex(ValueError, "TechnicalPlan") as raised:
                technical_plan_response.invoke_technical_plan_model(
                    "test prompt", settings=self._settings()
                )

        self.assertNotIn("PRIVATE_BODY_SENTINEL", str(raised.exception))
        self._assert_safe_length_diagnostics(captured.output)

    def test_stream_length_in_empty_final_chunk_rejects_valid_json(self) -> None:
        """末尾空片段中的 length 和标准 usage_metadata 不得被流式累积丢弃。"""

        response_text = '{"private":"PRIVATE_BODY_SENTINEL"}'
        model = FakeTechnicalPlanModel(
            AIMessage(content=response_text),
            chunks=[
                AIMessageChunk(content=response_text),
                AIMessageChunk(
                    content="",
                    response_metadata={
                        "finish_reason": "length",
                        "private": "PRIVATE_METADATA_SENTINEL",
                    },
                    usage_metadata={"input_tokens": 1, "output_tokens": 8192, "total_tokens": 8193},
                ),
            ],
        )
        tokens: list[str] = []
        with (
            patch.object(technical_plan_response, "create_chat_model", return_value=model),
            self.assertLogs("uvicorn.error", level="INFO") as captured,
        ):
            with self.assertRaisesRegex(ValueError, "TechnicalPlan"):
                technical_plan_response.invoke_technical_plan_model(
                    "test prompt", settings=self._settings(), on_token=tokens.append
                )

        self.assertEqual("".join(tokens), response_text)
        self._assert_safe_length_diagnostics(captured.output)

    def test_stop_and_missing_metadata_accept_valid_json_in_both_modes(self) -> None:
        """正常结束或未提供元数据时，同步和流式调用都保留完整正文。"""

        response_text = '{"architecture":{},"agent_contracts":[]}'
        for streaming in (False, True):
            for metadata in ({"finish_reason": "stop"}, {}):
                with self.subTest(streaming=streaming, metadata=metadata):
                    response = AIMessage(content=response_text, response_metadata=metadata)
                    tokens: list[str] = []
                    with patch.object(
                        technical_plan_response, "create_chat_model",
                        return_value=FakeTechnicalPlanModel(response),
                    ):
                        result = technical_plan_response.invoke_technical_plan_model(
                            "test prompt", settings=self._settings(),
                            on_token=tokens.append if streaming else None,
                        )

                    self.assertEqual(result, response_text)
                    self.assertEqual(tokens, [response_text] if streaming else [])

    def test_technical_budget_is_isolated_from_global_settings(self) -> None:
        """技术规划使用独立默认或指定输出预算，且不得修改共享 Settings。"""

        for budget in (32768, 49152):
            with self.subTest(budget=budget):
                settings = replace(
                    self._settings(), default_max_tokens=8192, technical_plan_max_tokens=budget
                )
                with patch.object(
                    technical_plan_response, "create_chat_model",
                    return_value=FakeTechnicalPlanModel(AIMessage(content="{}")),
                ) as create_model:
                    technical_plan_response.invoke_technical_plan_model("test prompt", settings=settings)

                model_settings = create_model.call_args.args[0]
                self.assertEqual(model_settings.default_max_tokens, budget)
                self.assertEqual(settings.default_max_tokens, 8192)
                self.assertEqual(settings.technical_plan_max_tokens, budget)

    def test_only_technical_generation_uses_the_dedicated_model_boundary(self) -> None:
        """完整技术规划进入专用边界，普通 ProjectPlan 保留原模型调用与预算。"""

        for technical in (False, True):
            with self.subTest(technical=technical):
                spec: dict[str, Any] = {"confirmed_product_plan": {}} if technical else {}
                settings = replace(self._settings(), default_max_tokens=8192)
                with (
                    patch.object(planner, "_planning_prompt", return_value="test prompt"),
                    patch.object(planner, "invoke_technical_plan_model", return_value="{}") as invoke,
                    patch.object(
                        planner, "create_chat_model",
                        return_value=FakeTechnicalPlanModel(AIMessage(content="{}")),
                    ) as ordinary_model,
                ):
                    result = planner._invoke_live_chat_model(spec, settings=settings)
                self.assertEqual(result, "{}")
                if technical:
                    invoke.assert_called_once_with("test prompt", settings=settings, on_token=None)
                    ordinary_model.assert_not_called()
                else:
                    invoke.assert_not_called()
                    self.assertEqual(ordinary_model.call_args.args[0].default_max_tokens, 8192)

    def test_contract_repair_uses_dedicated_boundary_and_strict_root(self) -> None:
        """定向接口修复同样使用独立预算，且不得从截断根对象恢复接口片段。"""

        for response in ('{"api_contracts":[{"id":"task_api"}]}', '{"api_contracts":[{"id":"task_api"}]'):
            with self.subTest(response=response):
                with (
                    patch.object(planner.Settings, "from_env", return_value=self._settings()),
                    patch.object(planner, "_technical_contract_ids_for_errors", return_value=["task_api"]),
                    patch.object(planner, "_technical_contract_repair_prompt", return_value="repair prompt"),
                    patch.object(planner, "invoke_technical_plan_model", return_value=response) as invoke,
                    patch.object(planner, "create_chat_model") as ordinary_model,
                    patch.object(planner, "create_technical_plan", return_value={}) as materialize,
                ):
                    if response.endswith("}"):
                        planner.repair_technical_plan_api_contracts_with_chat_model(
                            {}, {"api_contracts": [{"id": "task_api"}]}, ["test error"]
                        )
                        materialize.assert_called_once()
                    else:
                        with self.assertRaisesRegex(ValueError, "TechnicalPlan.*JSON"):
                            planner.repair_technical_plan_api_contracts_with_chat_model(
                                {}, {"api_contracts": [{"id": "task_api"}]}, ["test error"]
                            )
                        materialize.assert_not_called()
                    invoke.assert_called_once()
                    ordinary_model.assert_not_called()


if __name__ == "__main__":
    unittest.main()
