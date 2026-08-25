from __future__ import annotations

import unittest

from app.services.authorization_manifest import (
    compile_authorization_manifest,
    validate_authorization_manifest,
)
from app.services.page_implementation_contract import build_page_implementation_contracts
from app.services.product_plan import create_product_plan
from app.services.requirement_spec import create_requirement_spec


class AuthorizationManifestTests(unittest.TestCase):
    """验证固定资源目录只由确认产物确定性编译。"""

    def _inputs(self) -> tuple[dict, dict, list[dict], list[dict]]:
        """构造同时含页面、操作和数据规则的最小已确认输入。"""

        requirement = create_requirement_spec(
            "人员权限管理",
            agent_spec={
                "entities": [{"id": "Person", "name": "人员", "description": "人员资料"}],
                "pages": [{"pageId": "people", "name": "人员", "path": "/people", "module_id": "people", "description": "人员列表"}],
                "authorization_requirements": {
                    "enabled": True,
                    "unauthorizedBehavior": {"unauthorizedPage": "show_forbidden", "unauthorizedOperation": "disable"},
                    "restrictedPages": [{"name": "人员", "description": "受控页面", "rationale": "内部资料", "sourceRefs": ["需求"]}],
                    "restrictedOperations": [{"name": "编辑人员", "description": "受控操作", "rationale": "修改资料", "sourceRefs": ["需求"]}],
                    "dataRules": [{"name": "本人资料", "description": "仅本人", "ruleDescription": "仅能访问本人资料", "rationale": "隐私", "sourceRefs": ["需求"]}],
                },
            },
        )
        product = create_product_plan(
            requirement,
            agent_plan={"pages": [{"pageId": "people", "actions": [{"actionId": "edit-person", "name": "编辑人员", "description": "编辑", "requiresConfirmation": False, "behavior": {"type": "business", "expectedResult": "已保存"}}]}]},
        )
        contracts = [{"id": "person_api", "entity_ids": ["Person"], "endpoints": [{"id": "person_api.list"}, {"id": "person_api.update"}]}]
        pages = [{"pageId": "people", "references": {"action_implementations": [{"actionId": "edit-person", "endpointId": "person_api.update"}]}}]
        return requirement, product, contracts, pages

    def test_compiles_fixed_resources_and_action_level_endpoint_binding(self) -> None:
        """资源键、系统目录和 sequence 统一 action 资源必须稳定。"""

        requirement, product, contracts, pages = self._inputs()
        data_rule_id = requirement["authorization_requirements"]["dataRules"][0]["ruleId"]
        binding = [{"ruleId": data_rule_id, "entityIds": ["Person"], "endpointIds": ["person_api.list"]}]
        manifest = compile_authorization_manifest(requirement, product, contracts, pages, binding)

        self.assertIn("system.authorization.page", {item["resourceKey"] for item in manifest["resources"]})
        self.assertIn("business.page.people", {item["resourceKey"] for item in manifest["resources"]})
        self.assertIn("business.operation.edit-person", {item["resourceKey"] for item in manifest["resources"]})
        endpoint = next(item for item in manifest["bindings"]["endpoints"] if item["endpointId"] == "person_api.update")
        self.assertEqual(endpoint["requiredOperationResourceKeys"], ["business.operation.edit-person"])
        technical_plan = {
            "artifact_type": "technical-plan",
            "authorization_manifest": manifest,
            "api_contracts": contracts,
            "pages": pages,
        }
        page_contract = build_page_implementation_contracts(technical_plan, product, {"confirmation_status": "skipped", "pages": []})[0]
        self.assertEqual(
            page_contract["permissionBindings"],
            [
                {"targetType": "page", "targetId": "people", "resourceKey": "business.page.people"},
                {"targetType": "action", "targetId": "edit-person", "resourceKey": "business.operation.edit-person"},
            ],
        )
        self.assertEqual(validate_authorization_manifest(manifest, requirement, product, contracts, pages), [])
        self.assertEqual(manifest, compile_authorization_manifest(requirement, product, contracts, pages, binding))

    def test_rejects_data_binding_outside_endpoint_entity_scope(self) -> None:
        """数据规则不能绑定到未覆盖目标实体的接口。"""

        requirement, product, contracts, pages = self._inputs()
        data_rule_id = requirement["authorization_requirements"]["dataRules"][0]["ruleId"]
        manifest = compile_authorization_manifest(requirement, product, contracts, pages, [{"ruleId": data_rule_id, "entityIds": ["Other"], "endpointIds": ["person_api.list"]}])

        self.assertTrue(any("未覆盖绑定实体" in error for error in validate_authorization_manifest(manifest, requirement, product, contracts, pages)))
