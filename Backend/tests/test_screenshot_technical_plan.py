"""Screenshot TechnicalPlan output is bounded without changing text planning."""

from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from app.agents import screenshot_technical_plan
from app.graph.nodes import planning


class ScreenshotTechnicalPlanTests(unittest.TestCase):
    def test_generates_each_page_and_merges_unique_endpoint_references(self) -> None:
        pages = [{"pageId": f"page_{number}", "actions": []} for number in range(3)]
        requirement = {"confirmed_product_plan": {"pages": pages}}
        requested_pages: list[str] = []

        def invoke(scoped_requirement, **_kwargs):
            page_id = scoped_requirement["confirmed_product_plan"]["pages"][0]["pageId"]
            requested_pages.append(page_id)
            return json.dumps({
                "architecture": {"frontend": "React", "backend": "Java8 Springboot", "data": "MySQL8 Redis"},
                "entities": [{"id": "Account", "fields": [{"name": page_id, "type": "text"}]}],
                "api_contracts": [{
                    "id": "account_api",
                    "entity_ids": ["Account"],
                    "schemas": {"Output": {"type": "object", "properties": {
                        "extra": {"type": "string", "entity_field_ref": f"Account.{page_id}_extra"},
                    }}},
                    "endpoints": [{"id": "account_api.list", "response_schema_ref": "Output"}],
                }, {
                    "id": "unrelated_api",
                    "entity_ids": ["Account"],
                    "schemas": {"Output": {"type": "object"}},
                    "endpoints": [{"id": "unrelated_api.list", "response_schema_ref": "Output"}],
                }],
                "pages": [{
                    "pageId": page_id,
                    "references": {
                        "endpoint_dependencies": [{"endpoint_id": "account_api.list"}],
                        "action_implementations": [{"actionId": "list", "endpointId": "account_api.list"}],
                    },
                }],
            })

        with (
            patch.object(screenshot_technical_plan.Settings, "from_env", return_value=object()),
            patch.object(screenshot_technical_plan, "_invoke_live_chat_model", side_effect=invoke),
            patch.object(screenshot_technical_plan, "create_technical_plan", side_effect=lambda _spec, agent_plan: agent_plan),
        ):
            result = screenshot_technical_plan.plan_screenshot_technical_plan_with_chat_model(requirement)

        self.assertEqual(requested_pages, [page["pageId"] for page in pages])
        self.assertEqual(len(result["entities"]), 1)
        self.assertEqual(len(result["entities"][0]["fields"]), 6)
        self.assertEqual(len(result["api_contracts"]), 3)
        endpoint_ids = [contract["endpoints"][0]["id"] for contract in result["api_contracts"]]
        self.assertEqual(len(set(endpoint_ids)), 3)
        for page, endpoint_id in zip(result["pages"], endpoint_ids):
            self.assertEqual(page["references"]["endpoint_dependencies"][0]["endpoint_id"], endpoint_id)
            self.assertEqual(page["references"]["action_implementations"][0]["endpointId"], endpoint_id)

    def test_truncated_page_does_not_become_a_partial_plan(self) -> None:
        requirement = {"confirmed_product_plan": {"pages": [{"pageId": "usage"}]}}
        with (
            patch.object(screenshot_technical_plan.Settings, "from_env", return_value=object()),
            patch.object(screenshot_technical_plan, "_invoke_live_chat_model", return_value='{"architecture": {'),
        ):
            with self.assertRaisesRegex(ValueError, "usage.*无法恢复"):
                screenshot_technical_plan.plan_screenshot_technical_plan_with_chat_model(requirement)

    def test_closes_field_references_added_during_canonical_normalization(self) -> None:
        requirement = {"confirmed_product_plan": {"pages": [{"pageId": "usage"}]}}
        fragment = {
            "architecture": {},
            "entities": [{"id": "Usage", "fields": []}],
            "api_contracts": [],
            "pages": [{"pageId": "usage", "references": {}}],
        }

        def normalize(_spec, *, agent_plan):
            agent_plan["api_contracts"] = [{
                "id": "usage_api", "schemas": {"Output": {"type": "object", "properties": {
                    "requestCount": {"type": "integer", "entity_field_ref": "Usage.request_count"},
                }}},
            }]
            return agent_plan

        with (
            patch.object(screenshot_technical_plan.Settings, "from_env", return_value=object()),
            patch.object(screenshot_technical_plan, "_invoke_live_chat_model", return_value=json.dumps(fragment)),
            patch.object(screenshot_technical_plan, "create_technical_plan", side_effect=normalize),
        ):
            result = screenshot_technical_plan.plan_screenshot_technical_plan_with_chat_model(requirement)

        self.assertIn(
            "request_count",
            [field["name"] for field in result["entities"][0]["fields"]],
        )

    def test_truncated_complex_page_splits_actions_and_keeps_all_bindings(self) -> None:
        actions = [{"actionId": f"action_{number}"} for number in range(4)]
        requirement = {"confirmed_product_plan": {"pages": [{"pageId": "complex", "actions": actions}]}}
        scope_sizes: list[int] = []

        def invoke(scoped_requirement, **_kwargs):
            scoped_actions = scoped_requirement["confirmed_product_plan"]["pages"][0]["actions"]
            scope_sizes.append(len(scoped_actions))
            if len(scoped_actions) > 2:
                return '{"architecture": {'
            return json.dumps({
                "architecture": {},
                "entities": [{"id": "Record", "fields": []}],
                "api_contracts": [{
                    "id": "records_api", "entity_ids": ["Record"],
                    "schemas": {"Output": {"type": "object"}},
                    "endpoints": [{"id": "records_api.execute", "response_schema_ref": "Output"}],
                }],
                "pages": [{"pageId": "complex", "references": {
                    "endpoint_dependencies": [{"endpoint_id": "records_api.execute"}],
                    "action_implementations": [
                        {"actionId": action["actionId"], "endpointId": "records_api.execute"}
                        for action in scoped_actions
                    ],
                }}],
            })

        with (
            patch.object(screenshot_technical_plan.Settings, "from_env", return_value=object()),
            patch.object(screenshot_technical_plan, "_invoke_live_chat_model", side_effect=invoke),
            patch.object(screenshot_technical_plan, "create_technical_plan", side_effect=lambda _spec, agent_plan: agent_plan),
        ):
            result = screenshot_technical_plan.plan_screenshot_technical_plan_with_chat_model(requirement)

        self.assertEqual(scope_sizes, [4, 2, 2])
        self.assertEqual(len(result["pages"]), 1)
        self.assertEqual(
            {item["actionId"] for item in result["pages"][0]["references"]["action_implementations"]},
            {action["actionId"] for action in actions},
        )
        self.assertEqual(len(result["api_contracts"]), 2)

    def test_revision_fragments_coalesce_shared_contract_without_duplicate_endpoints(self) -> None:
        fragments = [
            {
                "architecture": {}, "entities": [{"id": "Account", "fields": []}],
                "api_contracts": [{
                    "id": "account_api", "entity_ids": ["Account"],
                    "schemas": {"Output": {"type": "object"}},
                    "endpoints": [{"id": endpoint_id}],
                }],
                "pages": [{"pageId": page_id, "references": {}}],
            }
            for page_id, endpoint_id in (("overview", "account_api.list"), ("profile", "account_api.update"))
        ]

        merged = screenshot_technical_plan._merge_fragments(fragments)

        self.assertEqual(len(merged["api_contracts"]), 1)
        self.assertEqual(
            {endpoint["id"] for endpoint in merged["api_contracts"][0]["endpoints"]},
            {"account_api.list", "account_api.update"},
        )

    def test_text_input_keeps_existing_technical_planner(self) -> None:
        state = {"workflow_scope": "application_planning", "requirement_input": {"mode": "text"}}
        candidate = {"artifact_type": "technical-plan"}
        with (
            patch.object(planning, "plan_project_with_chat_model", return_value=candidate) as original,
            patch.object(planning, "plan_screenshot_technical_plan_with_chat_model") as screenshot,
            patch.object(planning, "apply_project_plan_feedback", side_effect=lambda plan, _feedback: plan),
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda _state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", return_value=[]),
        ):
            result, errors, _ = planning._generate_valid_technical_plan(state, {}, None)

        self.assertIs(result, candidate)
        self.assertEqual(errors, [])
        original.assert_called_once()
        screenshot.assert_not_called()

    def test_screenshot_input_uses_bounded_technical_planner(self) -> None:
        state = {"workflow_scope": "application_planning", "requirement_input": {"mode": "screenshot"}}
        candidate = {"artifact_type": "technical-plan"}
        with (
            patch.object(planning, "plan_project_with_chat_model") as original,
            patch.object(planning, "plan_screenshot_technical_plan_with_chat_model", return_value=candidate) as screenshot,
            patch.object(planning, "apply_project_plan_feedback", side_effect=lambda plan, _feedback: plan),
            patch.object(planning, "_attach_technical_plan_contracts", side_effect=lambda _state, plan: plan),
            patch.object(planning, "_project_plan_validation_errors", return_value=[]),
        ):
            result, errors, _ = planning._generate_valid_technical_plan(state, {}, None)

        self.assertIs(result, candidate)
        self.assertEqual(errors, [])
        screenshot.assert_called_once()
        original.assert_not_called()


if __name__ == "__main__":
    unittest.main()
