from __future__ import annotations

import json
import unittest
from copy import deepcopy
from typing import Any
from unittest.mock import patch

from app.agents.main.product_planner import (
    _product_plan_json_example,
    plan_product_with_chat_model,
)
from app.graph.nodes.product_planning import _generate_valid_product_plan
from app.services.product_plan import create_product_plan, validate_product_plan
from app.services.requirement_spec import create_requirement_spec


class AgentCapabilityIdTests(unittest.TestCase):
    """验证能力增删重排及模型重试不会破坏智能体内的稳定唯一身份。"""

    def setUp(self) -> None:
        """构造使用顺序能力 ID 的有效当前产品规划。"""

        self.spec: dict[str, Any] = create_requirement_spec("创建订单管理应用")
        self.spec["agent_requirements"] = [{
            "agentId": "order_assistant",
            "name": "订单助手",
            "purpose": "查询和修改订单。",
            "capabilities": ["查询订单", "修改订单"],
            "entryPageIds": [],
            "interactionMode": "conversation",
            "boundaries": ["修改前必须明确确认。"],
        }]
        self.initial: dict[str, Any] = create_product_plan(
            self.spec,
            agent_plan=json.loads(_product_plan_json_example(self.spec)),
        )
        self.assertEqual(validate_product_plan(self.initial, self.spec), [])

    def _revised_spec(self) -> dict[str, Any]:
        """在旧能力之前插入新能力，保留原能力的业务名称。"""

        spec = deepcopy(self.spec)
        spec["agent_requirements"][0]["capabilities"] = [
            "填写结构化输入", "查询订单", "修改订单",
        ]
        return spec

    def _ids_by_name(self, plan: dict[str, Any]) -> dict[str, str]:
        """按能力名称提取稳定身份，便于核对重排前后的引用。"""

        return {
            capability["name"]: capability["capabilityId"]
            for capability in plan["agents"][0]["capabilities"]
        }

    def test_inserted_capability_avoids_later_existing_ids(self) -> None:
        """模型输出唯一的顺序 ID 时，合并必须避让后续旧能力的 ID。"""

        spec = self._revised_spec()
        model = json.loads(_product_plan_json_example(spec))
        revised = create_product_plan(spec, agent_plan=model, existing_plan=self.initial)

        self.assertEqual(validate_product_plan(revised, spec), [])
        ids = self._ids_by_name(revised)
        self.assertEqual(len(set(ids.values())), 3)
        for name, capability_id in self._ids_by_name(self.initial).items():
            self.assertEqual(ids[name], capability_id)
        self.assertEqual(revised["confirmation_status"], "pending_user_confirmation")
        repeated = create_product_plan(spec, agent_plan=model, existing_plan=revised)
        self.assertEqual(self._ids_by_name(repeated), ids)

    def test_default_ids_avoid_existing_ids_without_model_output(self) -> None:
        """没有模型 ID 时，中文能力的顺序后备 ID 也必须避让旧身份。"""

        spec = self._revised_spec()
        revised = create_product_plan(spec, existing_plan=self.initial)

        self.assertEqual(validate_product_plan(revised, spec), [])
        ids = self._ids_by_name(revised)
        self.assertEqual(len(set(ids.values())), 3)
        for name, capability_id in self._ids_by_name(self.initial).items():
            self.assertEqual(ids[name], capability_id)

    def test_collision_suffix_does_not_steal_later_model_id(self) -> None:
        """消歧后的 ID 也不得占用后续新能力已经提出的有效身份。"""

        spec = self._revised_spec()
        spec["agent_requirements"][0]["capabilities"].insert(1, "查看修改记录")
        model = json.loads(_product_plan_json_example(spec))
        capabilities = model["agents"][0]["capabilities"]
        capabilities[0]["capabilityId"] = "order_assistant_capability_1"
        capabilities[1]["capabilityId"] = "order_assistant_capability_1_2"

        revised = create_product_plan(spec, agent_plan=model, existing_plan=self.initial)

        self.assertEqual(validate_product_plan(revised, spec), [])
        ids = self._ids_by_name(revised)
        self.assertEqual(len(set(ids.values())), 4)
        self.assertEqual(ids["查看修改记录"], "order_assistant_capability_1_2")
        self.assertNotEqual(ids["填写结构化输入"], ids["查看修改记录"])

    def test_reordering_capabilities_preserves_existing_ids(self) -> None:
        """能力仅重排时必须保留身份，并采用本次模型更新的业务结果。"""

        spec = deepcopy(self.spec)
        spec["agent_requirements"][0]["capabilities"].reverse()
        model = json.loads(_product_plan_json_example(spec))
        model["agents"][0]["capabilities"][0]["expectedResult"] = "用户核对最新订单状态。"

        revised = create_product_plan(spec, agent_plan=model, existing_plan=self.initial)

        self.assertEqual(validate_product_plan(revised, spec), [])
        self.assertEqual(self._ids_by_name(revised), self._ids_by_name(self.initial))
        self.assertEqual(
            revised["agents"][0]["capabilities"][0]["expectedResult"],
            "用户核对最新订单状态。",
        )

    def test_validation_retry_preserves_unique_capability_ids(self) -> None:
        """其他校验触发下一轮模型修复后，能力身份仍唯一且保持原基准。"""

        spec = self._revised_spec()
        raw_model = _product_plan_json_example(spec)
        calls = 0

        def generate(*args: Any, **kwargs: Any) -> dict[str, Any]:
            """走真实解析归一化链路，并在首轮注入独立页面校验失败。"""

            nonlocal calls
            calls += 1
            candidate = plan_product_with_chat_model(*args, **kwargs)
            if calls == 1:
                candidate["pages"][0]["state_requirements"]["error"] = ""
            return candidate

        with (
            patch("app.agents.main.product_planner._invoke_product_planner", return_value=raw_model),
            patch("app.graph.nodes.product_planning.plan_product_with_chat_model", side_effect=generate),
        ):
            revised = _generate_valid_product_plan(
                spec, existing_plan=self.initial, user_feedback="增加结构化输入。",
            )

        self.assertEqual(calls, 2)
        self.assertEqual(validate_product_plan(revised, spec), [])
        ids = self._ids_by_name(revised)
        self.assertEqual(len(set(ids.values())), 3)
        for name, capability_id in self._ids_by_name(self.initial).items():
            self.assertEqual(ids[name], capability_id)


if __name__ == "__main__":
    unittest.main()
