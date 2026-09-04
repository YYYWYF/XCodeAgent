"""T2.2 confirmed 职责、精确 identity、冲突归因与冻结事实回归。"""

from copy import deepcopy
import tempfile
import unittest

from pydantic import ValidationError

from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.planning_frozen import freeze_json
from app.workspace.task_documents import load_confirmed_build_task_plan, write_build_task_plan_json


def _task(task_id: str, *, unit_id="frontend:api-client", status="pending", provides=()) -> dict:
    """构造保留执行状态、显式职责与描述的正式任务，不以描述作为 identity。"""

    return {
        "id": task_id, "unit_id": unit_id, "owner": "frontend", "status": status,
        "description": "实现查询接口", "dependencies": [],
        "provides_capabilities": [unit_id],
        "deliverables": [{"kind": "frontend.shared_capability", "provides": list(provides)}],
    }


def _owner(task_id: str, contract_id: str, endpoint_id: str, **kwargs) -> dict:
    """以现有平台业务检查的正式复合身份声明 Endpoint 实现 owner。"""

    return {
        **_task(task_id, **kwargs),
        "business_acceptance_checks": [{
            "kind": "frontend.api_contract",
            "expected": {"endpoints": [{"api_contract_id": contract_id, "endpoint_id": endpoint_id}]},
        }],
    }


def _plan(*tasks: dict) -> dict:
    """构造通过 T2.1 基线门槛的正式计划，任务状态与计划确认状态独立。"""

    ids = [task["id"] for task in tasks]
    return {
        "schema_version": "build-dag.v3", "status": "ready", "confirmation_status": "confirmed",
        "task_registry": {task["id"]: task for task in tasks},
        "task_graph": {"nodes": ids, "edges": [], "topological_order": ids, "validation": {"is_valid": True}},
    }


def _inputs() -> dict:
    """提供真实 Unit 骨架生成器和完整正式 API 目录，当前 Scope 仅包含 orders。"""

    formal = {
        "confirmation_status": "confirmed", "version": "1.0.0",
        "frontend_pages": [{"pageId": "users"}, {"pageId": "orders"}],
        "api_contracts": [
            {"id": "users-api", "endpoints": [{"id": "users.list"}]},
            {"id": "orders-api", "endpoints": [{"id": "orders.list"}]},
        ],
    }
    return {
        "confirmed_plan": None, "formal_plan": formal,
        "unit_skeleton": ensure_build_unit_skeleton(formal, {}),
        "build_context": {
            "target": {"type": "page", "id": "orders"},
            "required_unit_ids": ["frontend:api-client", "frontend:shell", "page:orders"],
        },
        "workspace_snapshot": {"workspace_revision": "snapshot-1"},
    }


class BuildTaskReuseTests(unittest.TestCase):
    def setUp(self) -> None:
        """为每个用例独立创建正式输入，避免互相污染。"""

        self.inputs = _inputs()

    def test_pending_historical_task_is_retained(self) -> None:
        """confirmed DAG 中 pending Task 已占据职责，不能因未执行而再次规划。"""

        self.inputs["confirmed_plan"] = _plan(_task("adapter", provides=["frontend.response-entity-adapter"]))
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.retained_task_ids_by_unit["frontend:api-client"], ("adapter",))
        self.assertEqual(facts.reusable_capabilities_by_unit["frontend:api-client"], {"frontend.response-entity-adapter": ("adapter",)})
        self.assertEqual(facts.issues, ())

    def test_accepts_confirmed_loader_output_without_using_skeleton_tasks(self) -> None:
        """联用 T2.1 正式 loader；骨架携带的较新任务不能被当作 confirmed baseline。"""

        with tempfile.TemporaryDirectory() as workspace:
            write_build_task_plan_json({"workspace": workspace}, _plan(_task("formal", status="failed")))
            self.inputs["confirmed_plan"] = load_confirmed_build_task_plan(workspace)
            self.inputs["unit_skeleton"]["task_registry"] = {"newer": _task("newer")}
            self.inputs["unit_skeleton"]["build_units"]["frontend:api-client"]["task_ids"] = ["newer"]
            facts = resolve_reuse_facts(**self.inputs)

        self.assertEqual(facts.retained_task_ids_by_unit["frontend:api-client"], ("formal",))
        self.assertEqual(facts.issues, ())

    def test_failed_historical_task_has_same_facts_as_completed(self) -> None:
        """仅改变执行状态不改变任何 reuse 事实，包括 Endpoint owner。"""

        snapshots = []
        for status in ("pending", "failed", "completed", "running", "already_satisfied"):
            self.inputs["confirmed_plan"] = _plan(_owner("users", "users-api", "users.list", status=status, provides=["users.api"]))
            snapshots.append(resolve_reuse_facts(**self.inputs).model_dump(mode="json"))
        self.assertTrue(all(facts == snapshots[0] for facts in snapshots))
        self.assertEqual(snapshots[0]["retained_task_ids_by_unit"]["frontend:api-client"], ["users"])

    def test_shared_unit_retains_existing_responsibilities_without_claiming_missing_ones(self) -> None:
        """共享 Unit 同时保有 adapter/users 职责，orders 缺项仍未被任何事实覆盖。"""

        self.inputs["confirmed_plan"] = _plan(
            _task("adapter", provides=["frontend.response-entity-adapter"]),
            _owner("users", "users-api", "users.list", provides=["users.api"]),
        )
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.retained_task_ids_by_unit["frontend:api-client"], ("adapter", "users"))
        self.assertEqual(set(facts.reusable_capabilities_by_unit["frontend:api-client"]), {"frontend.response-entity-adapter", "users.api"})
        self.assertNotIn("orders.api", facts.reusable_capabilities_by_unit["frontend:api-client"])
        self.assertEqual([owner.endpoint_id for owner in facts.retained_endpoint_owners], ["users.list"])
        self.assertEqual(set(facts.model_dump()), {
            "retained_task_ids_by_unit", "reusable_capabilities_by_unit", "retained_endpoint_owners", "external_capabilities", "issues",
        })

    def test_endpoint_owner_uses_full_formal_identity_outside_current_scope(self) -> None:
        """保留跨 Scope Endpoint owner，不从当前目标、Task 名或路径替换其正式身份。"""

        task = _owner("unrelated-task-name", "users-api", "users.list", unit_id="page:users")
        task["target_files"] = ["frontend/src/apis/orders.ts"]
        self.inputs["confirmed_plan"] = _plan(task)
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.retained_endpoint_owners[0].model_dump(), {
            "api_contract_id": "users-api", "endpoint_id": "users.list",
            "owner_task_id": "unrelated-task-name", "owner_unit_id": "page:users",
        })
        self.assertEqual(facts.issues, ())

    def test_duplicate_confirmed_endpoint_owners_are_platform_issue(self) -> None:
        """冲突保留所有证据，归于平台基线且不分配任何模型 retry Unit。"""

        self.inputs["confirmed_plan"] = _plan(
            _owner("owner-b", "users-api", "users.list", unit_id="page:users", status="failed"),
            _owner("owner-a", "users-api", "users.list"),
        )
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(len(facts.retained_endpoint_owners), 2)
        self.assertEqual(len(facts.issues), 1)
        issue = facts.issues[0]
        self.assertEqual((issue.code, issue.level, issue.category), ("CONFIRMED_ENDPOINT_OWNER_CONFLICT", "pre_generation", "platform"))
        self.assertEqual(issue.task_ids, ("owner-a", "owner-b"))
        self.assertEqual(issue.unit_ids, ("frontend:api-client", "page:users"))
        self.assertFalse(issue.retryable)
        self.assertEqual(issue.retry_unit_ids, ())

    def test_repair_and_page_usage_do_not_create_another_owner(self) -> None:
        """修复继承和页面调用不是实现职责；同 Task 重复引用也不会制造两个 owner。"""

        owner = _owner("owner", "users-api", "users.list")
        owner["business_acceptance_checks"] *= 2
        repair = {**deepcopy(owner), "id": "repair", "kind": "repair"}
        usage = _owner("usage", "users-api", "users.list", unit_id="page:users")
        usage["business_acceptance_checks"][0]["kind"] = "frontend.page_endpoint_usage"
        self.inputs["confirmed_plan"] = _plan(owner, repair, usage)
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual([record.owner_task_id for record in facts.retained_endpoint_owners], ["owner"])
        self.assertEqual(facts.retained_task_ids_by_unit["frontend:api-client"], ("owner", "repair"))
        self.assertEqual(facts.issues, ())

    def test_endpoint_identity_is_case_sensitive_and_contract_qualified(self) -> None:
        """同名 Endpoint 的合同不同或正式大小写不同，不得折叠为相同 owner。"""

        self.inputs["formal_plan"]["api_contracts"] = [
            {"id": "Api", "endpoints": [{"id": "list"}, {"id": "List"}]},
            {"id": "api", "endpoints": [{"id": "list"}]},
        ]
        self.inputs["confirmed_plan"] = _plan(
            _owner("upper", "Api", "List"), _owner("lower", "Api", "list"), _owner("other", "api", "list"),
        )
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(len(facts.retained_endpoint_owners), 3)
        self.assertEqual(facts.issues, ())

    def test_missing_or_unknown_endpoint_identity_is_not_inferred(self) -> None:
        """缺少合同 ID 或指向未知正式目标时报告基线问题，不从相似描述猜测。"""

        for contract_id, endpoint_id in (("", "users.list"), ("users-api", "missing")):
            with self.subTest(identity=(contract_id, endpoint_id)):
                self.inputs["confirmed_plan"] = _plan(_owner("users", contract_id, endpoint_id))
                facts = resolve_reuse_facts(**self.inputs)
                self.assertEqual(facts.retained_endpoint_owners, ())
                self.assertEqual(len(facts.issues), 1)
                self.assertEqual(facts.issues[0].category, "platform")

    def test_similar_descriptions_do_not_merge_or_invent_reuse(self) -> None:
        """描述相同的两个任务分别保留；没有显式 capability 就不凭描述推断 adapter。"""

        self.inputs["confirmed_plan"] = _plan(_task("first"), _task("second"))
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.retained_task_ids_by_unit["frontend:api-client"], ("first", "second"))
        self.assertEqual(facts.reusable_capabilities_by_unit["frontend:api-client"], {})
        self.assertEqual(facts.retained_endpoint_owners, ())

    def test_resource_capability_versions_remain_distinct(self) -> None:
        """权限资源能力按完整 fingerprint 身份保留，R1 不隐式满足 R2。"""

        self.inputs["confirmed_plan"] = _plan(_task("resources-r1", unit_id="frontend:auth-guard", provides=["frontend.auth.resources:R1"]))
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.reusable_capabilities_by_unit["frontend:auth-guard"], {"frontend.auth.resources:R1": ("resources-r1",)})
        self.assertNotIn("frontend.auth.resources:R2", facts.reusable_capabilities_by_unit["frontend:auth-guard"])

    def test_pending_or_failed_plan_is_rejected_but_absent_baseline_is_valid(self) -> None:
        """计划级确认与失败门禁不同于任务执行状态，None 表示合法空基线。"""

        self.assertEqual(resolve_reuse_facts(**self.inputs).issues, ())
        for changes in ({"confirmation_status": "pending"}, {"status": "failed"}):
            with self.subTest(changes=changes):
                self.inputs["confirmed_plan"] = {**_plan(_task("task")), **changes}
                facts = resolve_reuse_facts(**self.inputs)
                self.assertTrue(all(not ids for ids in facts.retained_task_ids_by_unit.values()))
                self.assertEqual(facts.issues[0].code, "CONFIRMED_BASELINE_INVALID")

    def test_baseline_identity_errors_are_explicit_and_never_repaired(self) -> None:
        """registry 身份不能被自动补齐，骨架缺失 Unit 也不能使有效历史 Task 消失。"""

        self.inputs["confirmed_plan"] = _plan(_task("bad-id"), _task("old", unit_id="page:removed"))
        self.inputs["confirmed_plan"]["task_registry"]["bad-id"]["id"] = "different"
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual({issue.code for issue in facts.issues}, {"CONFIRMED_TASK_IDENTITY_INVALID", "CONFIRMED_UNIT_MISSING"})
        self.assertEqual(facts.retained_task_ids_by_unit["page:removed"], ("old",))

    def test_results_are_deterministic_frozen_and_do_not_modify_inputs(self) -> None:
        """输入字典及数组换序不改变结果，输出深层只读并可标准 JSON 往返。"""

        self.inputs["confirmed_plan"] = _plan(
            _task("z", provides=["cap.z", "cap.a"]), _owner("a", "users-api", "users.list"),
        )
        original = deepcopy(self.inputs)
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(self.inputs, original)
        reordered = deepcopy(self.inputs)
        reordered["confirmed_plan"]["task_registry"] = dict(reversed(list(reordered["confirmed_plan"]["task_registry"].items())))
        reordered["confirmed_plan"]["task_registry"]["z"]["deliverables"][0]["provides"].reverse()
        reordered["formal_plan"]["api_contracts"].reverse()
        self.assertEqual(resolve_reuse_facts(**reordered).model_dump_json(), facts.model_dump_json())
        self.assertEqual(resolve_reuse_facts(**freeze_json(self.inputs)), facts)
        self.assertEqual(ReuseFacts.model_validate_json(facts.model_dump_json()), facts)
        with self.assertRaises(TypeError):
            facts.reusable_capabilities_by_unit["frontend:api-client"]["cap.z"] = ()
        with self.assertRaises(ValidationError):
            facts.model_copy(update={"planning_unit_ids": []})
        self.inputs["confirmed_plan"]["task_registry"]["z"]["deliverables"].clear()
        self.assertEqual(facts.reusable_capabilities_by_unit["frontend:api-client"]["cap.z"], ("z",))


if __name__ == "__main__":
    unittest.main()
