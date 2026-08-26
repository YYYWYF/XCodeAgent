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
        """构造同时含页面和操作规则的最小已确认输入。"""

        requirement = create_requirement_spec(
            "人员权限管理",
            agent_spec={
                "entities": [{"id": "Person", "name": "人员", "description": "人员资料"}],
                "pages": [{"pageId": "people", "name": "人员", "path": "/people", "module_id": "people", "description": "人员列表"}],
                "authorization_requirements": {
                    "enabled": True,
                    "initialAdminRoleId": "administrator",
                    "restrictedPages": [{"name": "人员", "description": "受控页面", "rationale": "内部资料", "sourceRefs": ["需求"], "defaultGrantedRoleIds": ["administrator"]}],
                    "restrictedOperations": [{"name": "编辑人员", "description": "受控操作", "rationale": "修改资料", "sourceRefs": ["需求"], "defaultGrantedRoleIds": ["editor"]}],
                },
                "user_roles": [
                    {"id": "administrator", "name": "系统管理员", "description": "维护权限。", "isSystemRole": True, "isInitialAdminRole": True},
                    {"id": "editor", "name": "编辑者", "description": "编辑人员。"},
                ],
            },
        )
        product = create_product_plan(
            requirement,
            agent_plan={"pages": [{"pageId": "people", "actions": [{"actionId": "edit_person", "name": "编辑人员", "description": "编辑", "requiresConfirmation": False, "behavior": {"type": "business", "expectedResult": "已保存"}}]}]},
        )
        contracts = [{"id": "person_api", "entity_ids": ["Person"], "endpoints": [{"id": "person_api.list"}, {"id": "person_api.update"}]}]
        pages = [{"pageId": "people", "references": {"action_implementations": [{"actionId": "edit_person", "endpointId": "person_api.update"}]}}]
        return requirement, product, contracts, pages

    def test_compiles_fixed_resources_and_action_level_endpoint_binding(self) -> None:
        """资源键、系统目录和 sequence 统一 action 资源必须稳定。"""

        requirement, product, contracts, pages = self._inputs()
        manifest = compile_authorization_manifest(requirement, product, contracts, pages)

        self.assertIn("system_authorization_management", {item["resourceKey"] for item in manifest["resources"]})
        self.assertIn("people", {item["resourceKey"] for item in manifest["resources"]})
        self.assertIn("people_edit_person", {item["resourceKey"] for item in manifest["resources"]})
        endpoint = next(item for item in manifest["bindings"]["endpoints"] if item["endpointId"] == "person_api.update")
        self.assertEqual(endpoint["operationResourceKeys"], ["people_edit_person"])
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
                {"targetType": "page", "pageId": "people", "resourceKey": "people"},
                {"targetType": "action", "pageId": "people", "actionId": "edit_person", "resourceKey": "people_edit_person"},
            ],
        )
        self.assertEqual(validate_authorization_manifest(manifest, requirement, product, contracts, pages), [])
        self.assertEqual(manifest, compile_authorization_manifest(requirement, product, contracts, pages))

    def test_rejects_data_permissions_and_mixed_endpoint_control(self) -> None:
        """数据权限和受控/未受控复用 Endpoint 必须阻止 TechnicalPlan。"""

        requirement, product, contracts, pages = self._inputs()
        requirement["authorization_requirements"]["dataRules"] = []
        with self.assertRaisesRegex(ValueError, "DATA_AUTHORIZATION_NOT_SUPPORTED"):
            compile_authorization_manifest(requirement, product, contracts, pages)

        requirement, product, contracts, pages = self._inputs()
        pages[0]["references"]["action_implementations"].append({"actionId": "unrestricted", "endpointId": "person_api.update"})
        with self.assertRaisesRegex(ValueError, "ENDPOINT_AUTHORIZATION_MIXED_CONTROL"):
            compile_authorization_manifest(requirement, product, contracts, pages)

    def test_multiple_operation_resources_use_any_of_and_key_collision_is_rejected(self) -> None:
        """多个操作资源可聚合到同一 Endpoint，跨类型同键必须停止编译。"""

        requirement, product, contracts, pages = self._inputs()
        requirement["authorization_requirements"]["restrictedOperations"].append(
            {
                "ruleId": "operate_person",
                "name": "操作人员",
                "description": "执行另一项受控业务能力。",
                "sourceRefs": ["测试"],
                "defaultGrantedRoleIds": ["administrator"],
            }
        )
        product["authorizationTargets"]["operationRules"].append(
            {
                "ruleId": "operate_person",
                "pageId": "people",
                "actionId": "operate_person",
            }
        )
        pages[0]["references"]["action_implementations"].append(
            {"actionId": "operate_person", "endpointId": "person_api.update"}
        )
        manifest = compile_authorization_manifest(requirement, product, contracts, pages)
        endpoint = next(item for item in manifest["bindings"]["endpoints"] if item["endpointId"] == "person_api.update")
        self.assertEqual(endpoint["operationResourceKeys"], ["people_edit_person", "people_operate_person"])

        requirement["authorization_requirements"]["restrictedPages"].append(
            {
                "ruleId": "page_collision",
                "name": "冲突页",
                "description": "测试冲突。",
                "sourceRefs": ["测试"],
                "defaultGrantedRoleIds": ["administrator"],
            }
        )
        product["authorizationTargets"]["pageRules"].append({"ruleId": "page_collision", "pageId": "people_edit_person"})
        with self.assertRaisesRegex(ValueError, "跨类型或跨目标碰撞"):
            compile_authorization_manifest(requirement, product, contracts, pages)
