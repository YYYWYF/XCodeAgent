"""当前 manifest 驱动资源单元准入、骨架更新和 Page capability 依赖的回归。"""

from copy import deepcopy
import tempfile
import unittest

from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.dag_planning_orchestrator import plan_dag_sequential
from app.services.planning_frozen import plain_json
from app.services.unit_generation_contracts import UnitGenerationAttemptResult, UnitGenerationPolicy
from tests.dag_planning_baseline_fixtures import build_context, execution_scope, project_plan, workspace_snapshot
from tests.dag_planning_orchestrator_fixtures import model_tasks, planning_inputs
from tests.test_unit_generation_contracts import _policy_payload
from tests.test_unit_generation_orchestrator import _settings


AUTH_UNIT = "frontend:auth-guard"


def _plan(binding_kind: str | None) -> dict:
    """构造不含 permissions 字段、仅依赖当前 manifest 的正式页面和操作资源绑定。"""

    plan = project_plan(authorization=True)
    bindings = plan["authorization_manifest"]["bindings"]
    bindings.update(pages=[], actions=[], endpoints=[])
    if binding_kind == "pages":
        bindings["pages"] = [{"pageId": "orders", "resourceKey": "orders"}]
    elif binding_kind == "actions":
        bindings["actions"] = [{"pageId": "orders", "actionId": "list", "resourceKey": "orders_list"}]
    elif binding_kind == "other_page":
        bindings["pages"] = [{"pageId": "customers", "resourceKey": "customers"}]
    return plan


class PageAuthResourcePlanningTests(unittest.TestCase):
    def test_context_and_graph_use_only_current_page_or_action_bindings(self) -> None:
        """页面绑定和仅操作绑定均准入；其他页面、无绑定及旧权限字段均不能误触发。"""

        for kind, expected in (("pages", True), ("actions", True), ("other_page", False), (None, False)):
            with self.subTest(binding_kind=kind):
                plan = _plan(kind)
                before = deepcopy(plan)
                context = build_context(plan, execution_scope())
                skeleton = ensure_build_unit_skeleton(plan, workspace_snapshot())
                edge = {"from": AUTH_UNIT, "to": "page:orders", "type": "depends_on"}
                self.assertEqual(AUTH_UNIT in context["required_unit_ids"], expected)
                self.assertEqual(edge in skeleton["unit_graph"]["edges"], expected)
                self.assertEqual(plan, before)

        plan = _plan(None)
        plan.pop("authorization_manifest")
        plan["pages"][0].update(permissions=["admin"], references={"permissions": ["admin"]})
        self.assertNotIn(AUTH_UNIT, build_context(plan, execution_scope())["required_unit_ids"])
        self.assertFalse(any(edge["from"] == AUTH_UNIT for edge in ensure_build_unit_skeleton(plan, {})["unit_graph"]["edges"]))

    def test_binding_changes_refresh_cached_skeleton_without_workspace_change(self) -> None:
        """只增加、转移或移除权限绑定也必须重建依赖边，不能复用旧骨架。"""

        snapshot = workspace_snapshot()
        plan = _plan(None)
        skeleton = ensure_build_unit_skeleton(plan, snapshot)
        for kind in ("pages", "other_page", None, "actions"):
            with self.subTest(binding_kind=kind):
                plan = _plan(kind)
                previous = deepcopy(skeleton)
                skeleton = ensure_build_unit_skeleton(plan, snapshot, skeleton)
                self.assertFalse(skeleton["unit_skeleton"]["reused"])
                expected = "customers" if kind == "other_page" else "orders"
                consumers = {edge["to"] for edge in skeleton["unit_graph"]["edges"] if edge["from"] == AUTH_UNIT}
                self.assertEqual(consumers, {f"page:{expected}"} if kind else set())
                self.assertNotEqual(skeleton["unit_skeleton"]["input_fingerprint"], previous["unit_skeleton"]["input_fingerprint"])
                self.assertTrue(ensure_build_unit_skeleton(plan, snapshot, skeleton)["unit_skeleton"]["reused"])


class PageAuthResourcePlanningIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_manifest_only_bindings_plan_complete_catalog_before_page(self) -> None:
        """真实准入、确定性生成和组装把完整目录 provider 连接到页面，不消耗 auth 模型预算。"""

        for kind in ("pages", "actions"):
            with self.subTest(binding_kind=kind), tempfile.TemporaryDirectory() as workspace:
                plan = _plan(kind)
                context = build_context(plan, execution_scope())
                inputs = planning_inputs(plan=plan, scope=execution_scope(), context=context, required=context["required_unit_ids"])
                calls = []

                async def generate(job, **kwargs):
                    """仅替代模型输出，保留真实职责校验、确定性 auth 生成和 Scope 组装。"""

                    calls.append(job.identity.unit_id)
                    return UnitGenerationAttemptResult(
                        identity=job.identity, input_fingerprint=job.context.input_fingerprint,
                        tasks=model_tasks(job), raw_response="", validation_issues=(), generation_metadata={},
                    )

                result = await plan_dag_sequential(
                    inputs, workspace_state={"workspace": workspace}, planning_run_id="manifest-only",
                    workflow_run_id="workflow", thread_id="thread", policy=UnitGenerationPolicy(**_policy_payload()),
                    settings=_settings(), generate_once=generate,
                )
                auth = result.planning_run.unit_states[AUTH_UNIT]
                self.assertEqual((auth.generation_status, auth.total_attempts), ("candidate_ready", 0))
                self.assertNotIn(AUTH_UNIT, calls)
                registry = plain_json(result.assembly.assembled_plan)["task_registry"]
                provider, = [task for task in registry.values() if task["unit_id"] == AUTH_UNIT]
                page, = [task for task in registry.values() if task["unit_id"] == "page:orders"]
                self.assertIn(provider["id"], page["dependencies"])
                self.assertEqual(provider["platform_executor"], "authorization.frontend_resources")
                # 页面选择只决定消费者，provider 身份必须覆盖 Scope 外的完整资源目录。
                larger = deepcopy(plan)
                larger["authorization_manifest"]["resources"].append({
                    "resourceKey": "customers_export", "type": "operation", "targetResourceRef": "action:customers:export",
                })
                larger_context = build_context(larger, execution_scope())
                larger_inputs = planning_inputs(plan=larger, scope=execution_scope(), context=larger_context,
                                               required=larger_context["required_unit_ids"])
                self.assertNotEqual(
                    inputs.requirements().generation_requirements_by_unit[AUTH_UNIT][0].requirement_id,
                    larger_inputs.requirements().generation_requirements_by_unit[AUTH_UNIT][0].requirement_id,
                )


if __name__ == "__main__":
    unittest.main()
