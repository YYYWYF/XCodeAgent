from __future__ import annotations

from copy import deepcopy
import unittest

from app.services.authorization_manifest import (
    compile_authorization_manifest,
    validate_authorization_manifest,
)
from app.services.authorization_deliverability import authorization_deliverability_report
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
                    "restrictedPages": [{"name": "人员", "targetPageId": "people", "description": "受控页面", "rationale": "内部资料", "sourceRefs": ["需求"], "defaultGrantedRoleIds": ["administrator"]}],
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
                {
                    "targetType": "action",
                    "pageId": "people",
                    "actionId": "edit_person",
                    "resourceKey": "people_edit_person",
                    "mode": "hidden",
                },
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
                "targetPageId": "people_edit_person",
                "description": "测试冲突。",
                "sourceRefs": ["测试"],
                "defaultGrantedRoleIds": ["administrator"],
            }
        )
        product["authorizationTargets"]["pageRules"].append({"ruleId": "page_collision", "pageId": "people_edit_person"})
        with self.assertRaisesRegex(ValueError, "跨类型或跨目标碰撞"):
            compile_authorization_manifest(requirement, product, contracts, pages)

    def test_deliverability_report_covers_closed_loop_and_default_endpoint_access(self) -> None:
        """4E 必须展示完整闭环，并允许受控页面调用未授权 Endpoint。"""

        requirement, product, contracts, pages = self._inputs()
        pages[0]["references"]["endpoint_dependencies"] = [
            {"endpoint_id": "person_api.list"},
            {"endpoint_id": "person_api.update"},
        ]
        manifest = compile_authorization_manifest(requirement, product, contracts, pages)
        technical_plan = {
            "artifact_type": "technical-plan",
            "authorization_manifest": manifest,
            "api_contracts": contracts,
            "pages": pages,
        }
        page_contracts = build_page_implementation_contracts(
            technical_plan,
            product,
            {"confirmation_status": "skipped", "pages": []},
        )

        report = authorization_deliverability_report(
            manifest,
            requirement,
            product,
            contracts,
            pages,
            page_contracts,
        )

        self.assertTrue(report["passed"])
        self.assertTrue(all(check["status"] == "pass" for check in report["checks"]))
        closure = next(check for check in report["checks"] if check["id"] == "action_endpoint_resource_closure")
        self.assertIn(
            "受控 Page people 调用 Endpoint person_api.list：无 Endpoint 授权绑定，按当前契约默认可访问。",
            [detail["message"] for detail in closure["details"]],
        )

    def test_deliverability_report_rejects_invalid_endpoint_and_missing_admin_system_resource(self) -> None:
        """4E 必须把 Endpoint 目录和初始管理员授权分别标记为阻断项。"""

        requirement, product, contracts, pages = self._inputs()
        manifest = compile_authorization_manifest(requirement, product, contracts, pages)
        manifest["bindings"]["endpoints"].append(
            {"endpointId": "person_api.missing", "operationResourceKeys": ["people_edit_person"]}
        )
        for grant in manifest["defaultRoleAuthorization"]["roleResourceGrants"]:
            if grant["roleSeedKey"] == "administrator":
                grant["resourceKeys"].remove("system_authorization_management")
        report = authorization_deliverability_report(
            manifest,
            requirement,
            product,
            contracts,
            pages,
            [],
        )

        statuses = {check["id"]: check["status"] for check in report["checks"]}
        self.assertFalse(report["passed"])
        self.assertEqual(statuses["endpoint_bindings"], "fail")
        self.assertEqual(statuses["initial_admin_system_resource"], "fail")

    def test_deliverability_report_marks_each_reference_and_closure_break_as_blocking(self) -> None:
        """4E 必须逐项定位资源、页面、操作、闭环和 mixed-control 问题。"""

        requirement, product, contracts, pages = self._inputs()
        manifest = compile_authorization_manifest(requirement, product, contracts, pages)

        def report_for(candidate_manifest: dict, candidate_pages: list[dict]) -> dict:
            """为独立篡改场景生成 4E 报告，避免场景之间相互影响。"""

            return authorization_deliverability_report(
                candidate_manifest,
                requirement,
                product,
                contracts,
                candidate_pages,
                [],
            )

        missing_resource = deepcopy(manifest)
        missing_resource["bindings"]["pages"][0]["resourceKey"] = "missing_resource"
        self.assertEqual(
            next(check for check in report_for(missing_resource, pages)["checks"] if check["id"] == "resource_keys")["status"],
            "fail",
        )

        invalid_page = deepcopy(manifest)
        invalid_page["bindings"]["pages"][0]["pageId"] = "missing_page"
        self.assertEqual(
            next(check for check in report_for(invalid_page, pages)["checks"] if check["id"] == "page_bindings")["status"],
            "fail",
        )

        invalid_action = deepcopy(manifest)
        invalid_action["bindings"]["actions"][0]["actionId"] = "missing_action"
        self.assertEqual(
            next(check for check in report_for(invalid_action, pages)["checks"] if check["id"] == "action_bindings")["status"],
            "fail",
        )

        missing_closure = deepcopy(manifest)
        missing_closure["bindings"]["endpoints"][0]["operationResourceKeys"] = []
        self.assertEqual(
            next(check for check in report_for(missing_closure, pages)["checks"] if check["id"] == "action_endpoint_resource_closure")["status"],
            "fail",
        )

        mixed_pages = deepcopy(pages)
        mixed_pages[0]["references"]["action_implementations"].append(
            {"actionId": "unrestricted", "endpointId": "person_api.update"}
        )
        self.assertEqual(
            next(check for check in report_for(manifest, mixed_pages)["checks"] if check["id"] == "mixed_endpoint_control")["status"],
            "fail",
        )
