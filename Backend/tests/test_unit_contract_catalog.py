"""T10.2 Frozen Contract Catalog 与 Unit allowlist 集成测试。"""

from __future__ import annotations

import json
from tempfile import TemporaryDirectory
import unittest

from app.services.dag_planning_orchestrator import DagPlanningError, plan_dag_sequential
from app.services.frozen_contract_store import FrozenContractStore
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import UnitGenerationAttemptResult, UnitGenerationPolicy
from tests.dag_planning_baseline_fixtures import build_context, execution_scope, project_plan
from tests.dag_planning_orchestrator_fixtures import model_tasks, planning_inputs
from tests.planning_run_fixtures import AT
from tests.test_unit_generation_contracts import _policy_payload
from tests.test_unit_generation_orchestrator import _settings


def _built_context(inputs, unit_id: str, *, planning_run_id: str = "catalog-run"):
    """从完整冻结输入创建一次 Run、Store 和指定 Unit Context。"""

    requirements = inputs.requirements()
    run = inputs.create_run(
        requirements,
        planning_run_id=planning_run_id,
        workflow_run_id="workflow-run",
        thread_id="thread",
        at=AT,
    )
    store = FrozenContractStore.create(
        planning_run_id=planning_run_id,
        formal_inputs=inputs.formal_contract_inputs,
    )
    return store, inputs.unit_context(
        run,
        requirements,
        unit_id,
        frozen_contract_store=store,
    )


class UnitContractCatalogTests(unittest.TestCase):
    """验证 Page/Endpoint catalog 严格按当前 Unit 的正式绑定收窄。"""

    def test_page_allowlist_contains_only_current_page_contracts(self) -> None:
        """Page 获得自身页面、接口、实体、权限和共享架构引用。"""

        plan = project_plan(authorization=True)
        scope = execution_scope(name="orders")
        context = build_context(plan, scope)
        inputs = planning_inputs(
            plan=plan,
            scope=scope,
            context=context,
            required=context["required_unit_ids"],
        )
        store, unit_context = _built_context(inputs, "page:orders")

        catalog = unit_context.contract_catalog
        self.assertEqual(
            {entry.kind for entry in catalog},
            {"technical_plan", "page_contract", "api_contract", "entity_binding", "authorization_slice"},
        )
        self.assertTrue(all(store.get(entry.ref_id) is not None for entry in catalog))
        self.assertTrue(all(entry.selectors for entry in catalog))
        catalog_sources = [plain_json(store[entry.ref_id].source) for entry in catalog]
        self.assertIn({"artifact": "runtime-page-contracts", "page_id": "orders"}, catalog_sources)
        self.assertNotIn({"artifact": "runtime-page-contracts", "page_id": "customers"}, catalog_sources)
        self.assertNotIn("content", unit_context.model_dump(mode="json")["contract_catalog"][0])

    def test_endpoint_allowlist_excludes_page_and_other_endpoint_contracts(self) -> None:
        """Endpoint Unit 仅获得精确 API/实体/权限 fragment，不获得 Page 私有合同。"""

        plan = project_plan(authorization=True)
        scope = execution_scope(target_type="endpoint", name="orders")
        context = build_context(plan, scope)
        inputs = planning_inputs(
            plan=plan,
            scope=scope,
            context=context,
            required=context["required_unit_ids"],
        )
        store, unit_context = _built_context(
            inputs,
            "backend:endpoint:orders-api:orders.list",
        )

        contracts = [store[entry.ref_id] for entry in unit_context.contract_catalog]
        self.assertNotIn("page_contract", {contract.kind for contract in contracts})
        api_sources = [plain_json(contract.source) for contract in contracts if contract.kind == "api_contract"]
        self.assertEqual(
            api_sources,
            [{"artifact": "technical-plan.json", "api_contract_id": "orders-api"}],
        )
        entity_sources = [plain_json(contract.source) for contract in contracts if contract.kind == "entity_binding"]
        self.assertEqual(
            entity_sources,
            [{"artifact": "entities/Order.json", "entity_id": "Order"}],
        )
        api_entry = next(entry for entry in unit_context.contract_catalog if entry.kind == "api_contract")
        self.assertIn("/endpoints/0", api_entry.selectors)

    def test_unauthorized_other_page_ref_is_absent(self) -> None:
        """同一 application 输入含两页绑定时，Page catalog 仍不泄漏另一页私有 ref。"""

        plan = project_plan()
        scope = {"type": "application", "targetId": "application"}
        required = ["page:orders", "page:customers"]
        inputs = planning_inputs(
            plan=plan,
            scope=scope,
            required=required,
            context={"scope": scope, "required_unit_ids": required, "target": scope},
        )
        store, unit_context = _built_context(inputs, "page:orders")
        private_customer_ref = next(
            contract.ref_id
            for contract in store.contracts.values()
            if contract.kind == "page_contract" and contract.content.get("pageId") == "customers"
        )

        self.assertNotIn(private_customer_ref, {entry.ref_id for entry in unit_context.contract_catalog})
        self.assertIn(
            "page:customers",
            {source_ref.unit_id for source_ref in inputs.formal_source_refs},
        )


class UnitContractCatalogRetryTests(unittest.IsolatedAsyncioTestCase):
    """验证 retry 复用 catalog，并让非法来源绑定在模型调用前致命失败。"""

    async def test_local_retry_uses_same_contract_catalog(self) -> None:
        """首次内容失败与第二次成功 Attempt 使用完全相同的非空 catalog。"""

        catalogs = []

        async def generate(job, **_kwargs):
            """首轮返回 owner 错误触发 Local retry，第二轮返回合法 Candidate。"""

            catalogs.append(job.context.contract_catalog)
            tasks = model_tasks(job)
            if job.identity.attempt_in_round == 1:
                tasks[0]["owner"] = "backend"
            return UnitGenerationAttemptResult(
                identity=job.identity,
                input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}),
                tasks=tasks,
            )

        with TemporaryDirectory() as directory:
            result = await plan_dag_sequential(
                planning_inputs(required=["page:a"]),
                workspace_state={"workspace": directory},
                planning_run_id="catalog-retry-run",
                workflow_run_id="workflow-run",
                thread_id="thread",
                policy=UnitGenerationPolicy(**_policy_payload()),
                settings=_settings(),
                generate_once=generate,
                now=lambda: AT,
            )

        self.assertEqual(len(catalogs), 2)
        self.assertTrue(catalogs[0])
        self.assertEqual(catalogs[0], catalogs[1])
        self.assertEqual(result.planning_run.unit_states["page:a"].total_attempts, 2)

    async def test_invalid_source_binding_is_fatal_before_model_dispatch(self) -> None:
        """当前 Unit 绑定不存在的 Store 来源时不返回部分 catalog，也不进入 Local retry。"""

        inputs = planning_inputs(required=["page:a"])
        refs = [item.model_dump(mode="json") for item in inputs.formal_source_refs]
        page_ref = next(item for item in refs if item["unit_id"] == "page:a" and item["kind"] == "page_contract")
        # ref 本身真实存在，但属于另一 Page；这类错绑也必须 fail closed。
        page_ref["source"] = {"artifact": "runtime-page-contracts", "page_id": "b"}
        invalid = inputs.model_copy(update={"formal_source_refs": refs})
        calls = []

        async def generate(job, **_kwargs):
            """记录意外模型调用；来源绑定失败时本函数不应执行。"""

            calls.append(job)
            raise AssertionError("invalid source binding 不得进入模型 dispatch")

        with TemporaryDirectory() as directory, self.assertRaises(DagPlanningError) as caught:
            await plan_dag_sequential(
                invalid,
                workspace_state={"workspace": directory},
                planning_run_id="catalog-invalid-run",
                workflow_run_id="workflow-run",
                thread_id="thread",
                policy=UnitGenerationPolicy(**_policy_payload()),
                settings=_settings(),
                generate_once=generate,
                now=lambda: AT,
            )

        self.assertEqual(calls, [])
        self.assertIsNone(caught.exception.snapshot)
        self.assertEqual(caught.exception.issues[0].code, "UNIT_CONTRACT_SOURCE_BINDING_INVALID")
        self.assertFalse(caught.exception.issues[0].retryable)


if __name__ == "__main__":
    unittest.main()
