"""验证导航首页显式取消接口依赖后，技术规划不会重新推导读取绑定。"""

from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

from app.agents.main import planner
from app.config import Settings
from app.services.page_dependencies import normalize_page_dependencies
from app.services.page_implementation_contract import validate_page_implementation_contracts
from app.services.project_plan import create_technical_plan


class TechnicalPlanEmptyDependenciesTests(TestCase):
    """覆盖默认推导、首版生成和已有技术规划取消首页绑定的完整物化入口。"""

    def setUp(self) -> None:
        """准备纯导航首页、读取列表页及属于不同实体的两个 GET 接口。"""

        self.requirement = {
            "confirmed_product_plan": {
                "pages": [
                    {
                        "pageId": "home", "path": "/home", "information_items": [],
                        "navigation_targets": ["orders"],
                        "actions": [{
                            "actionId": "open_orders", "name": "打开订单",
                            "behavior": {"type": "navigation", "targetPageId": "orders"},
                        }],
                    },
                    {"pageId": "orders", "path": "/orders", "actions": []},
                ],
                "authorizationTargets": {"pageRules": [], "operationRules": []},
            },
        }
        self.candidate = {
            "architecture": {"frontend": "React 前端", "backend": "Java8 Springboot", "data": "MySQL8 Redis"},
            "entities": [
                {"id": entity, "name": name, "fields": []}
                for entity, name in (("Order", "订单"), ("Card", "门禁卡"))
            ],
            "api_contracts": [
                {
                    "id": f"{entity.lower()}_api", "name": name,
                    "entity_ids": [entity], "schemas": {}, "base_path": f"/api/{entity.lower()}",
                    "endpoints": [{
                        "id": endpoint, "name": name, "method": "GET", "path": f"/api/{entity.lower()}",
                    }],
                }
                for entity, name, endpoint in (("Order", "查询订单", "orders.list"), ("Card", "查询门禁卡", "cards.query"))
            ],
            "pages": [
                {"pageId": "home", "references": {"endpoint_dependencies": [], "action_implementations": []}},
                {"pageId": "orders", "references": {
                    "endpoint_dependencies": [{
                        "endpoint_id": "orders.list", "usage": "read", "trigger": "进入订单列表",
                        "required_for_initial_load": True,
                    }],
                    "action_implementations": [],
                }},
            ],
        }

    def test_explicit_empty_dependencies_override_defaults_and_root_references(self) -> None:
        """嵌套空数组优先于根字段，生成阶段的同源根字段空数组也不触发默认读取。"""

        for references in (
            {"endpoint_dependencies": []},
            {"endpoint_dependencies": [{"endpoint_id": "cards.query"}],
             "references": {"endpoint_dependencies": []}},
        ):
            for technical in (False, True):
                with self.subTest(references=references, technical=technical):
                    page = {"pageId": "home", **deepcopy(references)}
                    before = deepcopy(page)
                    normalized = normalize_page_dependencies(
                        [page], self.candidate["api_contracts"],
                        include_action_implementations=technical,
                    )
                    self.assertEqual(normalized[0]["references"]["endpoint_dependencies"], [])
                    self.assertEqual(page, before)

    def test_missing_dependencies_still_use_default_reads(self) -> None:
        """真正未声明依赖时继续按当前默认规则推导读取接口。"""

        normalized = normalize_page_dependencies(
            [{"pageId": "home", "references": {}}], self.candidate["api_contracts"],
            include_action_implementations=True,
        )
        self.assertEqual(
            [item["endpoint_id"] for item in normalized[0]["references"]["endpoint_dependencies"]],
            ["orders.list", "cards.query"],
        )

    def test_generation_and_revision_keep_empty_home_without_changing_other_contracts(self) -> None:
        """首版模型结果及删除已有绑定后的修订结果均保持首页为空并保留其他页面和接口。"""

        baseline_candidate = deepcopy(self.candidate)
        baseline_candidate["pages"][0]["references"]["endpoint_dependencies"] = [
            {"endpoint_id": "orders.list"}, {"endpoint_id": "cards.query"},
        ]
        baseline = create_technical_plan(self.requirement, agent_plan=baseline_candidate)
        before = deepcopy(baseline)
        settings = Settings(model_base_url="http://unused", model_api_key="unused", model_name="test")
        for existing in (None, baseline):
            with (
                self.subTest(revision=existing is not None),
                patch.object(planner.Settings, "from_env", return_value=settings),
                patch.object(planner, "_invoke_live_chat_model", return_value=json.dumps(self.candidate)),
                patch.object(planner, "edit_technical_plan_with_agent", return_value=deepcopy(self.candidate)),
            ):
                result = planner.plan_project_with_chat_model(self.requirement, existing_plan=existing)
                self.assertEqual(result["pages"][0]["references"]["endpoint_dependencies"], [])
                self.assertEqual(result["pages"][1], baseline["pages"][1])
                self.assertEqual(result["api_contracts"], baseline["api_contracts"])
                self.assertEqual(validate_page_implementation_contracts(
                    result, self.requirement["confirmed_product_plan"],
                ), [])
        self.assertEqual(baseline, before)
