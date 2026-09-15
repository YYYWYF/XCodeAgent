"""第二阶段：Endpoint Design 来源集合替代实体绑定的 DAG 职责回归。"""

import unittest

from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.frozen_contract_manifest_index import endpoint_api_design_source_types
from app.services.frozen_contract_store import FrozenContract
from app.services.unit_generation_requirement_targets import endpoint_source_types
from app.services.unit_generation_requirements import (
    GenerationRequirementsError,
    resolve_generation_requirements,
)


def _plan() -> dict:
    """构造保留 entity_ids 业务语义、但完全没有 Entity binding 的 TechnicalPlan。"""

    return {
        "version": "2.0.0",
        "confirmation_status": "confirmed",
        "entities": [
            {"id": "Order", "name": "Order", "fields": []},
            {"id": "LegacyOrder", "name": "LegacyOrder", "fields": []},
        ],
        "page_implementation_contracts": [],
        "api_contracts": [{
            "id": "orders-api",
            "entity_ids": ["Order", "LegacyOrder"],
            "endpoints": [{"id": "orders.list", "method": "GET", "path": "/orders"}],
        }],
    }


def _design(*source_types: str) -> dict:
    """构造当前 Endpoint API Design 的 fieldMappings 来源快照。"""

    mappings = []
    for index, source_type in enumerate(source_types):
        mappings.append({
            "mappingType": "source_mapping",
            "endpointField": {"side": "response", "location": "response_body", "path": f"items[{index}]"},
            "sourceFields": [{
                "sourceType": source_type,
                "sourceId": f"source-{index}",
                "path": f"items[{index}]",
            }],
        })
    if not mappings:
        mappings.append({
            "mappingType": "business_description",
            "endpointField": {"side": "response", "location": "response_body", "path": "items"},
            "businessDescription": "由当前 Endpoint 业务规则生成。",
        })
    return {
        "schemaVersion": "endpoint-field-mapping.v3",
        "artifactType": "endpoint-field-mapping",
        "status": "confirmed",
        "confirmationStatus": "confirmed",
        "apiContractId": "orders-api",
        "endpointId": "orders.list",
        "artifactRevision": "a" * 32,
        "endpointContract": {"id": "orders.list"},
        "fieldMappings": mappings,
        "sourceSnapshots": [{"sourceType": source_type} for source_type in source_types],
        "basedOn": [{"artifactKey": "technical-plan", "sha256": "b" * 64}],
    }


def _reuse_facts() -> ReuseFacts:
    """提供没有历史职责或外部能力的最小 ReuseFacts。"""

    return ReuseFacts(
        retained_task_ids_by_unit={},
        reusable_capabilities_by_unit={},
        retained_endpoint_owners=[],
        external_capabilities=[],
        issues=[],
    )


def _requirements(design: dict) -> tuple[dict, object]:
    """运行单 Endpoint Backend Scope 的职责编译。"""

    plan = _plan()
    skeleton = ensure_build_unit_skeleton(plan, {})
    result = resolve_generation_requirements(
        required_unit_ids=["backend:bootstrap", "backend:endpoint:orders-api:orders.list"],
        build_execution_scope={
            "type": "endpoint",
            "targetId": "orders.list",
            "apiContractId": "orders-api",
        },
        unit_skeleton=skeleton,
        reuse_facts=_reuse_facts(),
        formal_target=plan,
        endpoint_designs=[design],
    )
    return result.generation_requirements_by_unit, result


class UnitGenerationRequirementTargetTests(unittest.TestCase):
    def _endpoint_kinds(self, *source_types: str) -> set[str]:
        """返回指定物理来源下生成的 Endpoint responsibility kind 集合。"""

        requirements, _ = _requirements(_design(*source_types))
        return {
            item.source_refs["kind"]
            for item in requirements["backend:endpoint:orders-api:orders.list"]
        }

    def test_source_types_are_derived_from_endpoint_field_mappings(self) -> None:
        """来源集合只来自当前 Endpoint Design 的 sourceFields，不读取实体绑定。"""

        endpoints = {("orders-api", "orders.list"): {"id": "orders.list"}}
        design = _design("external_api")
        design["sourceSnapshots"] = [{"sourceType": "database"}]
        self.assertEqual(
            endpoint_source_types([design], endpoints),
            {("orders-api", "orders.list"): frozenset({"external_api"})},
        )

    def test_frozen_manifest_uses_the_same_field_mapping_authority(self) -> None:
        """Frozen Manifest 也不能从 sourceSnapshots 偷换物理来源集合。"""

        design = _design("external_api")
        design["sourceSnapshots"] = [{"sourceType": "database"}]
        contract = FrozenContract(
            ref_id="frozen-endpoint-design",
            kind="endpoint_api_design",
            content=design,
            source={"artifact": "confirmed-endpoint-api-design"},
        )
        self.assertEqual(endpoint_api_design_source_types(contract), {"external_api"})

    def test_backend_requirements_are_endpoint_level_and_have_no_entity_target(self) -> None:
        """同一 Endpoint 无论关联多少 entity_ids，都只按物理来源集合生成职责。"""

        self.assertNotIn("entity_detail_plans", _plan())
        requirements, result = _requirements(_design("database", "external_api"))
        endpoint_requirements = requirements["backend:endpoint:orders-api:orders.list"]
        ids = {item.requirement_id for item in endpoint_requirements}
        self.assertEqual(len(endpoint_requirements), 5)
        self.assertEqual(
            ids,
            {
                "backend.endpoint.objects:orders-api:orders.list",
                "backend.repository:orders-api:orders.list",
                "backend.upstream:orders-api:orders.list",
                "backend.application_service:orders-api:orders.list",
                "backend.endpoint_controller:orders-api:orders.list",
            },
        )
        self.assertNotIn("entity_id", result.model_dump_json())
        self.assertNotIn("external_api_mapping", result.model_dump_json())
        self.assertTrue(all(
            item.source_refs["target_id"] == "orders.list"
            for item in endpoint_requirements
        ))
        self.assertTrue(all(
            item.source_refs["artifact"] == "endpoint-api-design"
            and item.source_refs["data_source_types"] == ("database", "external_api")
            for item in endpoint_requirements
        ))
        self.assertEqual(
            {item.source_refs["kind"] for item in endpoint_requirements},
            {
                "backend.objects",
                "backend.repository",
                "backend.upstream",
                "backend.application_service",
                "backend.endpoint_controller",
            },
        )
        self.assertEqual(
            requirements["backend:bootstrap"][0].source_refs["data_source_type"],
            "database",
        )
        self.assertEqual(
            {item.source_refs["data_source_type"] for item in requirements["backend:bootstrap"]},
            {"database", "external_api"},
        )

    def test_database_endpoint_has_repository_but_no_upstream(self) -> None:
        """纯数据库 Endpoint 只生成 repository 来源分支。"""

        self.assertEqual(
            self._endpoint_kinds("database"),
            {
                "backend.objects",
                "backend.repository",
                "backend.application_service",
                "backend.endpoint_controller",
            },
        )

    def test_external_api_endpoint_has_upstream_but_no_repository(self) -> None:
        """纯外部 API Endpoint 只生成 upstream 来源分支。"""

        self.assertEqual(
            self._endpoint_kinds("external_api"),
            {
                "backend.objects",
                "backend.upstream",
                "backend.application_service",
                "backend.endpoint_controller",
            },
        )

    def test_empty_physical_source_set_keeps_source_independent_backend_duties(self) -> None:
        """纯业务说明 Endpoint 仍生成对象、服务和控制器，但不生成来源分支。"""

        requirements, result = _requirements(_design())
        endpoint_requirements = requirements["backend:endpoint:orders-api:orders.list"]
        self.assertEqual(
            {item.source_refs["kind"] for item in endpoint_requirements},
            {
                "backend.objects",
                "backend.application_service",
                "backend.endpoint_controller",
            },
        )
        self.assertTrue(all(
            item.source_refs["data_source_types"] == ()
            for item in endpoint_requirements
        ))
        self.assertEqual(requirements["backend:bootstrap"], ())
        self.assertEqual(
            result.planning_unit_ids,
            ("backend:endpoint:orders-api:orders.list",),
        )

    def test_entity_detail_plan_changes_do_not_change_endpoint_requirements(self) -> None:
        """修改旧实体绑定只能改变 TechnicalPlan 业务语义，不能改变 DAG 职责结果。"""

        design = _design("external_api")
        first, _ = _requirements(design)
        altered_plan = _plan()
        altered_plan["entity_detail_plans"] = [{
            "entity_id": "Order", "status": "confirmed", "data_source_type": "database",
        }]
        altered_plan["api_contracts"][0]["entity_ids"] = ["CompletelyDifferentEntity"]
        skeleton = ensure_build_unit_skeleton(altered_plan, {})
        second_result = resolve_generation_requirements(
            required_unit_ids=["backend:bootstrap", "backend:endpoint:orders-api:orders.list"],
            build_execution_scope={
                "type": "endpoint", "targetId": "orders.list", "apiContractId": "orders-api",
            },
            unit_skeleton=skeleton,
            reuse_facts=_reuse_facts(),
            formal_target=altered_plan,
            endpoint_designs=[design],
        )
        self.assertEqual(
            first["backend:endpoint:orders-api:orders.list"],
            second_result.generation_requirements_by_unit["backend:endpoint:orders-api:orders.list"],
        )

    def test_backend_scope_without_endpoint_design_fails_closed(self) -> None:
        """缺少 Endpoint Design 时直接阻断，不回退到 entity_detail_plans。"""

        plan = _plan()
        with self.assertRaises(GenerationRequirementsError) as error:
            resolve_generation_requirements(
                required_unit_ids=["backend:endpoint:orders-api:orders.list"],
                build_execution_scope={
                    "type": "endpoint", "targetId": "orders.list", "apiContractId": "orders-api",
                },
                unit_skeleton=ensure_build_unit_skeleton(plan, {}),
                reuse_facts=_reuse_facts(),
                formal_target=plan,
            )
        self.assertEqual(error.exception.issues[0].code, "GENERATION_ENDPOINT_API_DESIGN_MISSING")


if __name__ == "__main__":
    unittest.main()
