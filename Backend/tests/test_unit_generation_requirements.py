"""T2.3 Scope 职责差集、共享增量、空需求与非生成 Unit 策略回归。"""

from copy import deepcopy
import unittest

from pydantic import ValidationError

from app.services.build_context_resolver import resolve_target_build_context
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_task_reuse_contracts import ExternalCapability
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.planning_frozen import freeze_json
from app.services.unit_generation_requirements import (
    GenerationRequirementsError, UnitGenerationRequirements, resolve_generation_requirements,
)
from tests.entity_design_test_utils import confirm_entity_designs
from tests.test_build_task_reuse import _owner, _plan, _task


def _formal_plan(source_type: str = "database") -> dict:
    """构造可通过现有定向上下文解析的完整正式计划，不用模型猜测目标或数据源。"""

    return confirm_entity_designs({
        "version": "1.0.0", "confirmation_status": "confirmed",
        "frontend_pages": [{"pageId": page} for page in ("orders", "users")],
        "pages": [{"pageId": page, "path": f"/{page}"} for page in ("orders", "users")],
        "page_implementation_contracts": [{
            "schema_version": "page-implementation-contract.v1", "pageId": page,
            "uiDesignRef": {"path": f".xcodeagent/ui-design/pages/{page}/index.tsx"},
            "requiredEndpointIds": [f"{page}.list"],
        } for page in ("orders", "users")],
        "entities": [{"id": name, "name": name, "fields": []} for name in ("Order", "User")],
        "api_contracts": [{
            "id": f"{page}-api", "entity_ids": [entity],
            "endpoints": [{"id": f"{page}.list", "method": "GET", "path": f"/{page}"}],
        } for page, entity in (("orders", "Order"), ("users", "User"))],
    }, source_type=source_type)


def _inputs(*tasks: dict, source_type: str = "database", formal_plan: dict | None = None) -> dict:
    """连接真实 BuildContext、Unit Skeleton、ReuseFacts，再提供既有平台 shell 证据。"""

    plan = formal_plan or _formal_plan(source_type)
    skeleton = ensure_build_unit_skeleton(plan, {})
    context = resolve_target_build_context(plan, target_type="page", target_id="orders")
    facts = resolve_reuse_facts(
        confirmed_plan=_plan(*tasks) if tasks else None, unit_skeleton=skeleton,
        build_context=context, workspace_snapshot={"workspace_revision": "snapshot-1"}, formal_plan=plan,
    )
    # 此处直接使用上游事实 DTO；真实模板检查与证据生成已由 T2.2 workspace 回归覆盖。
    facts = facts.model_copy(update={"external_capabilities": [ExternalCapability(
        unit_id="frontend:shell", capability_id="frontend.shell.ready",
        source="template_generation_readiness", workspace_revision="snapshot-1",
        source_refs={"manifest_path": ".xcodeagent/template-generation-manifest.json"},
    )]})
    return {
        "required_unit_ids": context["required_unit_ids"],
        "build_execution_scope": {"type": "page", "targetId": "orders"},
        "unit_skeleton": skeleton, "reuse_facts": facts, "formal_target": plan,
    }


class UnitGenerationRequirementsTests(unittest.TestCase):
    def test_first_time_page_computes_scoped_responsibilities(self) -> None:
        """首次页面只规划本 Scope 的缺项，保留 required 与 planning 的区别。"""

        inputs = _inputs()
        inputs["required_unit_ids"] += ["application:root", "app:integration"]
        result = resolve_generation_requirements(**inputs)
        by_unit = result.generation_requirements_by_unit
        self.assertEqual([item.requirement_id for item in by_unit["page:orders"]], ["frontend.page:orders"])
        self.assertEqual([item.requirement_id for item in by_unit["frontend:api-client"]], [
            "frontend.api_module:orders-api:orders.list", "frontend.response-entity-adapter",
        ])
        self.assertEqual(len(by_unit["backend:endpoint:orders-api:orders.list"]), 4)
        self.assertEqual(set(result.planning_unit_ids), {
            "page:orders", "frontend:api-client", "backend:bootstrap", "backend:endpoint:orders-api:orders.list",
        })
        self.assertNotEqual(set(result.planning_unit_ids), set(inputs["required_unit_ids"]))
        self.assertNotIn("users.list", result.model_dump_json())

    def test_fully_reused_page_has_no_generation_requirement(self) -> None:
        """精确页面 capability 已登记时为 reuse_only，即使历史任务尚未执行成功。"""

        for status in ("pending", "failed", "completed"):
            with self.subTest(status=status):
                inputs = _inputs(_task("page-old", unit_id="page:orders", status=status, provides=["frontend.page:orders"]))
                inputs["required_unit_ids"] = ["page:orders"]
                result = resolve_generation_requirements(**inputs)
                self.assertEqual(result.generation_requirements_by_unit["page:orders"], ())
                self.assertEqual(result.generation_strategy_by_unit["page:orders"], "reuse_only")
                self.assertEqual(result.planning_unit_ids, ())

    def test_shared_api_client_appends_only_missing_endpoint(self) -> None:
        """已有 adapter/users API 的共享 Unit 仍可 retain + generate orders API。"""

        inputs = _inputs(
            _task("adapter", provides=["frontend.response-entity-adapter"]),
            _owner("users", "users-api", "users.list"),
        )
        inputs["required_unit_ids"] = ["frontend:api-client"]
        before_facts = inputs["reuse_facts"].model_dump_json()
        result = resolve_generation_requirements(**inputs)
        self.assertEqual([item.requirement_id for item in result.generation_requirements_by_unit["frontend:api-client"]], ["frontend.api_module:orders-api:orders.list"])
        self.assertEqual(result.generation_strategy_by_unit["frontend:api-client"], "model")
        self.assertEqual(result.planning_unit_ids, ("frontend:api-client",))
        self.assertEqual(inputs["reuse_facts"].retained_task_ids_by_unit["frontend:api-client"], ("adapter", "users"))
        self.assertEqual(inputs["reuse_facts"].model_dump_json(), before_facts)
        self.assertNotIn("replacement", result.model_dump_json())

    def test_shared_api_client_can_be_fully_reused_by_formal_owner(self) -> None:
        """正式接口 owner 即使属于其他 Unit，也可证明接口职责已登记。"""

        inputs = _inputs(
            _task("adapter", provides=["frontend.response-entity-adapter"]),
            _owner("orders-api-owner", "orders-api", "orders.list", unit_id="page:users"),
        )
        inputs["required_unit_ids"] = ["frontend:api-client"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["frontend:api-client"], "reuse_only")
        self.assertEqual(result.planning_unit_ids, ())

    def test_existing_tasks_do_not_prove_adapter_capability(self) -> None:
        """不能因 Unit 有普通任务就省略 adapter；描述相似也不提供精确能力。"""

        task = _task("similar", provides=["some-other-adapter"])
        task["description"] = "提供统一 ResponseEntity 传输适配器"
        inputs = _inputs(task, _owner("orders", "orders-api", "orders.list"))
        inputs["required_unit_ids"] = ["frontend:api-client"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual([item.requirement_id for item in result.generation_requirements_by_unit["frontend:api-client"]], ["frontend.response-entity-adapter"])

    def test_structural_units_never_generate(self) -> None:
        """结构 Unit 不受骨架 task_ids 或状态影响，始终保持 structural_only。"""

        inputs = _inputs()
        inputs["required_unit_ids"] = ["application:root", "app:integration"]
        inputs["unit_skeleton"]["build_units"]["app:integration"]["task_ids"] = ["untrusted"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(set(result.generation_strategy_by_unit.values()), {"structural_only"})
        self.assertTrue(all(not requirements for requirements in result.generation_requirements_by_unit.values()))
        self.assertEqual(result.planning_unit_ids, ())

    def test_shell_is_always_prerequisite_only(self) -> None:
        """shell 只消费平台前置能力，不产生模型或 deterministic 需求。"""

        inputs = _inputs()
        inputs["required_unit_ids"] = ["frontend:shell"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["frontend:shell"], "prerequisite_only")
        self.assertEqual(result.generation_requirements_by_unit["frontend:shell"], ())
        self.assertEqual(result.planning_unit_ids, ())

    def test_missing_shell_evidence_fails_instead_of_creating_repair(self) -> None:
        """缺失模板证据必须前置失败，不能靠生成 shell Task 修复。"""

        inputs = _inputs()
        inputs["reuse_facts"] = inputs["reuse_facts"].model_copy(update={"external_capabilities": []})
        with self.assertRaises(GenerationRequirementsError) as error:
            resolve_generation_requirements(**inputs)
        self.assertEqual(error.exception.issues[0].code, "SHELL_PREREQUISITE_MISSING")
        self.assertFalse(error.exception.issues[0].retryable)

    def test_model_unit_with_no_applicable_duties_is_not_planned(self) -> None:
        """Scope 无真实接口时 API client 需求为空，model 策略本身不触发 planning。"""

        plan = _formal_plan()
        plan["page_implementation_contracts"][0]["requiredEndpointIds"] = []
        inputs = _inputs(formal_plan=plan)
        inputs["required_unit_ids"] = ["frontend:api-client"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(result.generation_strategy_by_unit["frontend:api-client"], "model")
        self.assertEqual(result.generation_requirements_by_unit["frontend:api-client"], ())
        self.assertEqual(result.planning_unit_ids, ())

    def test_static_scope_has_no_real_api_adapter_or_backend_duty(self) -> None:
        """静态数据沿用正式源类型，仅生成静态接口职责和页面职责。"""

        result = resolve_generation_requirements(**_inputs(source_type="static"))
        self.assertEqual(set(result.planning_unit_ids), {"frontend:data:static", "page:orders"})
        self.assertEqual([item.requirement_id for item in result.generation_requirements_by_unit["frontend:data:static"]], ["frontend.static_data_module:orders-api:orders.list"])
        self.assertNotIn("response-entity-adapter", result.model_dump_json())

    def test_backend_bootstrap_adds_missing_source_capability(self) -> None:
        """共享 bootstrap 的 database 已满足时，external_api 仍作为本轮增量。"""

        plan = confirm_entity_designs(_formal_plan(), source_type="external_api", entity_ids=["User"])
        inputs = _inputs(_task("bootstrap-db", unit_id="backend:bootstrap", provides=["backend.bootstrap:database"]), formal_plan=plan)
        inputs["required_unit_ids"] = ["backend:bootstrap"]
        inputs["build_execution_scope"] = {"type": "application", "targetId": "application"}
        result = resolve_generation_requirements(**inputs)
        self.assertEqual([item.requirement_id for item in result.generation_requirements_by_unit["backend:bootstrap"]], ["backend.bootstrap:external_api"])

    def test_reuse_issues_block_before_generation_decisions(self) -> None:
        """confirmed owner 冲突原样传递，不转换成可重试模型缺项。"""

        inputs = _inputs(_owner("a", "orders-api", "orders.list"), _owner("b", "orders-api", "orders.list"))
        with self.assertRaises(GenerationRequirementsError) as error:
            resolve_generation_requirements(**inputs)
        self.assertEqual(error.exception.issues, inputs["reuse_facts"].issues)
        self.assertEqual(error.exception.issues[0].category, "platform")

    def test_endpoint_scope_uses_exact_contract_and_omits_pages(self) -> None:
        """独立 Endpoint Scope 只为指定正式复合目标计算职责，不夹带其他页面或接口。"""

        inputs = _inputs()
        context = resolve_target_build_context(inputs["formal_target"], target_type="endpoint", target_id="users.list", api_contract_id="users-api")
        inputs["build_execution_scope"] = {"type": "endpoint", "targetId": "users.list", "apiContractId": "users-api"}
        inputs["required_unit_ids"] = context["required_unit_ids"]
        result = resolve_generation_requirements(**inputs)
        self.assertEqual(set(result.planning_unit_ids), {"backend:bootstrap", "backend:endpoint:users-api:users.list"})
        self.assertNotIn("orders.list", result.model_dump_json())
        self.assertNotIn("frontend.page", result.model_dump_json())

    def test_missing_source_type_and_inconsistent_unit_are_explicit_failures(self) -> None:
        """数据源类型不能按默认值补齐，错配的 Unit 节点也不能参与职责计算。"""

        inputs = _inputs()
        inputs["formal_target"]["entity_detail_plans"][0].pop("data_source_type")
        with self.assertRaises(GenerationRequirementsError) as error:
            resolve_generation_requirements(**inputs)
        self.assertEqual(error.exception.issues[0].code, "GENERATION_ENTITY_BINDING_MISSING")
        inputs = _inputs()
        inputs["unit_skeleton"]["build_units"]["page:orders"]["id"] = "page:users"
        with self.assertRaises(GenerationRequirementsError) as error:
            resolve_generation_requirements(**inputs)
        self.assertEqual(error.exception.issues[0].code, "GENERATION_UNIT_IDENTITY_INVALID")

    def test_unknown_and_out_of_scope_units_fail_explicitly(self) -> None:
        """缺失骨架或不属于 Scope 的正式 Unit 不能获得隐式生成职责。"""

        for unit_id, code in (("missing", "REQUIRED_UNIT_MISSING"), ("page:users", "GENERATION_UNIT_OUTSIDE_SCOPE")):
            with self.subTest(unit_id=unit_id):
                inputs = _inputs()
                inputs["required_unit_ids"] = [unit_id]
                with self.assertRaises(GenerationRequirementsError) as error:
                    resolve_generation_requirements(**inputs)
                self.assertEqual(error.exception.issues[0].code, code)

    def test_output_is_deterministic_frozen_and_does_not_mutate_inputs(self) -> None:
        """换序不改变结果，深层职责冻结，并通过 DTO 校验阻止空 Unit 进入 planning。"""

        inputs = _inputs()
        before_plan = deepcopy(inputs["formal_target"])
        before_facts = inputs["reuse_facts"].model_dump_json()
        result = resolve_generation_requirements(**inputs)
        reordered = {**inputs, "required_unit_ids": list(reversed(inputs["required_unit_ids"]))}
        self.assertEqual(resolve_generation_requirements(**reordered).model_dump_json(), result.model_dump_json())
        frozen = {key: value if key == "reuse_facts" else freeze_json(value) for key, value in inputs.items()}
        self.assertEqual(resolve_generation_requirements(**frozen), result)
        self.assertEqual(UnitGenerationRequirements.model_validate_json(result.model_dump_json()), result)
        self.assertEqual(inputs["formal_target"], before_plan)
        self.assertEqual(inputs["reuse_facts"].model_dump_json(), before_facts)
        with self.assertRaises(TypeError):
            result.generation_requirements_by_unit["page:orders"][0].source_refs["capability_id"] = "changed"
        with self.assertRaises(ValidationError):
            result.model_copy(update={"planning_unit_ids": ["frontend:shell"]})
        self.assertEqual(set(result.model_dump()), {"generation_requirements_by_unit", "planning_unit_ids", "generation_strategy_by_unit"})


if __name__ == "__main__":
    unittest.main()
