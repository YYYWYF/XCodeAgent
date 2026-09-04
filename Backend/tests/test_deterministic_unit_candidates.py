"""T2.5C auth 确定性候选身份、职责门禁、无模型副作用和单文件边界回归。"""

from contextlib import ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.services.authorization_frontend_projection import _render_resources
from app.services.authorization_resource_catalog import (
    ResourceCatalog, compile_frontend_resource_catalog, resource_catalog_fingerprint,
)
from app.services.authorization_resource_inspection import inspect_authorization_resource_catalog
from app.services.build_task_planner import build_task_candidate_contract_errors
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.deterministic_unit_candidates import build_auth_guard_candidate
from app.services.unit_generation_requirements import resolve_generation_requirements
from app.services.unit_generation_requirements_contracts import GenerationRequirementsError
from tests.test_build_task_reuse import _plan, _task
from tests.test_unit_generation_requirements import _inputs
from tests.test_unit_generation_requirements_auth import _auth_plan


UNIT = "frontend:auth-guard"
RESOURCE_PATH = "frontend/src/constants/resources.ts"


class DeterministicAuthCandidateTests(unittest.TestCase):
    """从真实 T2.3/T2.5B 结果构造候选，避免用手写缺项掩盖复用门禁。"""

    def setUp(self) -> None:
        """为每个测试提供完整正式源数据和当前 auth 缺项。"""

        self.plan = _auth_plan()
        self.catalog = compile_frontend_resource_catalog(self.plan["authorization_manifest"])
        self.fingerprint = resource_catalog_fingerprint(self.catalog)
        self.capability = f"frontend.auth.resources:{self.fingerprint}"
        self.inputs = _inputs(formal_plan=self.plan)
        self.inputs["required_unit_ids"] = [UNIT]

    def _kwargs(self) -> dict:
        """将当前职责结果连同同版本 catalog/fingerprint 传入 builder。"""

        return {
            "unit_id": UNIT, "resource_catalog": self.catalog, "fingerprint": self.fingerprint,
            "generation_requirements": resolve_generation_requirements(**self.inputs),
        }

    def test_deterministic_output_and_no_input_mutation(self) -> None:
        """相同输入得到相同完整候选，返回结果可独立修改而不污染下次构造或输入。"""

        kwargs = self._kwargs()
        before = kwargs["generation_requirements"].model_dump_json()
        first = build_auth_guard_candidate(**kwargs)
        second = build_auth_guard_candidate(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(json.dumps(first, ensure_ascii=False), json.dumps(second, ensure_ascii=False))
        first["tasks"][0]["source_refs"]["paths"].append("extra.ts")
        self.assertEqual(build_auth_guard_candidate(**kwargs), second)
        self.assertEqual(kwargs["generation_requirements"].model_dump_json(), before)

    def test_same_r_keeps_task_identity_across_order_and_description_changes(self) -> None:
        """目录顺序或任务需求文案变化不能创造同 R 的新 Task。"""

        kwargs = self._kwargs()
        first = build_auth_guard_candidate(**kwargs)
        changed = kwargs["generation_requirements"].model_dump(mode="json")
        changed["generation_requirements_by_unit"][UNIT][0]["description"] = "完全不同的说明"
        kwargs.update(
            resource_catalog=ResourceCatalog(tuple(reversed(self.catalog.resources))),
            generation_requirements=changed,
        )
        second = build_auth_guard_candidate(**kwargs)
        self.assertEqual(first, second)
        self.assertEqual(first["tasks"][0]["id"], f"frontend-auth-resources-{self.fingerprint}")

    def test_new_r_creates_new_identity_and_keeps_old_provider(self) -> None:
        """新增资源产生新的完整 fingerprint Task；历史 R1 只保留，不混入新候选。"""

        first = build_auth_guard_candidate(**self._kwargs())
        old_task = first["tasks"][0]
        self.plan["authorization_manifest"]["resources"].append({
            "resourceKey": "users_export", "type": "operation", "targetResourceRef": "action:users:export",
        })
        self.catalog = compile_frontend_resource_catalog(self.plan["authorization_manifest"])
        self.fingerprint = resource_catalog_fingerprint(self.catalog)
        self.inputs = _inputs(old_task, formal_plan=self.plan)
        self.inputs["required_unit_ids"] = [UNIT]
        second = build_auth_guard_candidate(**self._kwargs())
        self.assertNotEqual(old_task["id"], second["tasks"][0]["id"])
        self.assertNotEqual(old_task["provides_capabilities"], second["tasks"][0]["provides_capabilities"])
        self.assertEqual(len(second["tasks"]), 1)
        self.assertEqual(self.inputs["reuse_facts"].retained_task_ids_by_unit[UNIT], (old_task["id"],))
        self.assertEqual(second["tasks"][0]["dependencies"], [])

    def test_task_owner_strategy_executor_and_provider_contract(self) -> None:
        """使用当前前端 Task/交付物契约登记 provider，确定性 executor 字段保持精确。"""

        candidate = build_auth_guard_candidate(**self._kwargs())
        task, = candidate["tasks"]
        self.assertEqual(set(candidate), {"tasks"})
        self.assertEqual(task["owner"], "frontend")
        self.assertEqual(task["unit_id"], UNIT)
        self.assertEqual(task["task_type"], "frontend.code")
        self.assertEqual(task["execution_strategy"], "deterministic")
        self.assertEqual(task["platform_executor"], "authorization.frontend_resources")
        self.assertEqual(task["provides_capabilities"], [self.capability])
        self.assertEqual(task["deliverables"][0]["provides"], [self.capability])
        self.assertEqual(task["source_refs"]["resource_catalog_fingerprint"], self.fingerprint)
        self.assertEqual(task["source_refs"], self._kwargs()["generation_requirements"].generation_requirements_by_unit[UNIT][0].model_dump()["source_refs"])
        self.assertEqual(build_task_candidate_contract_errors(candidate), [])
        # 将候选作为以后已确认的 Task 登记，验证当前 ReuseFacts 能识别实际提供的能力。
        facts = resolve_reuse_facts(
            confirmed_plan=_plan(task), unit_skeleton=self.inputs["unit_skeleton"],
            build_context={}, workspace_snapshot={}, formal_plan=self.plan,
        )
        self.assertEqual(facts.issues, ())
        self.assertEqual(facts.reusable_capabilities_by_unit[UNIT][self.capability], (task["id"],))

    def test_only_resources_file_is_owned(self) -> None:
        """所有可写范围和交付物仅指向 resources.ts，没有 routes.tsx 或目录级授权。"""

        candidate = build_auth_guard_candidate(**self._kwargs())
        task = candidate["tasks"][0]
        self.assertEqual(task["target_files"], [RESOURCE_PATH])
        self.assertEqual(task["allowed_paths"], [RESOURCE_PATH])
        self.assertEqual(task["deliverables"][0]["paths"], [RESOURCE_PATH])
        self.assertEqual(task["source_refs"]["paths"], [RESOURCE_PATH])
        self.assertNotIn("routes.tsx", json.dumps(candidate))

    def test_confirmed_provider_skips_candidate_for_every_execution_status(self) -> None:
        """confirmed 当前 provider 在 pending/failed/completed 时都使 builder 返回 None。"""

        for status in ("pending", "failed", "completed"):
            with self.subTest(status=status):
                self.inputs = _inputs(_task("current", unit_id=UNIT, status=status, provides=[self.capability]), formal_plan=self.plan)
                self.inputs["required_unit_ids"] = [UNIT]
                self.assertIsNone(build_auth_guard_candidate(**self._kwargs()))

    def test_workspace_satisfied_skips_candidate(self) -> None:
        """真实完整 workspace R 经 T2.5B/T2.3 判定满足后，不为留痕制造 Task。"""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / RESOURCE_PATH
            path.parent.mkdir(parents=True)
            path.write_text(_render_resources(self.catalog.frontend_resources()), encoding="utf-8")
            before = (path.read_bytes(), path.stat().st_mtime_ns)
            inspection = inspect_authorization_resource_catalog(directory, self.catalog, workspace_revision="snapshot")
            self.inputs["reuse_facts"] = resolve_reuse_facts(
                confirmed_plan=None, unit_skeleton=self.inputs["unit_skeleton"], build_context={},
                workspace_snapshot={"workspace_revision": "snapshot"}, formal_plan=self.plan,
                auth_resource_inspection=inspection,
            )
            self.assertIsNone(build_auth_guard_candidate(**self._kwargs()))
            self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), before)

    def test_empty_disabled_auth_requirements_produce_no_candidate(self) -> None:
        """权限关闭时即使 auth 骨架存在且策略为 deterministic，也不生成空候选。"""

        self.plan["authorization_manifest"]["enabled"] = False
        self.inputs = _inputs(formal_plan=self.plan)
        self.inputs["required_unit_ids"] = [UNIT]
        kwargs = {**self._kwargs(), "resource_catalog": None, "fingerprint": None}
        self.assertIsNone(build_auth_guard_candidate(**kwargs))

    def test_catalog_fingerprint_and_requirement_must_match(self) -> None:
        """错误摘要、旧 requirement 或 source refs 不得被自动改写为当前 R。"""

        kwargs = self._kwargs()
        with self.assertRaises(GenerationRequirementsError):
            build_auth_guard_candidate(**{**kwargs, "fingerprint": "0" * 64})
        for key, value in (
            ("capability_id", "frontend.auth.resources:old"),
            ("resource_catalog_fingerprint", "old"), ("kind", "frontend.page"),
            ("artifact", "pending-plan"), ("paths", [RESOURCE_PATH, "frontend/src/constants/routes.tsx"]),
        ):
            with self.subTest(key=key):
                payload = kwargs["generation_requirements"].model_dump(mode="json")
                payload["generation_requirements_by_unit"][UNIT][0]["source_refs"][key] = value
                with self.assertRaises(GenerationRequirementsError) as caught:
                    build_auth_guard_candidate(**{**kwargs, "generation_requirements": payload})
                self.assertFalse(caught.exception.issues[0].retryable)
        payload = kwargs["generation_requirements"].model_dump(mode="json")
        payload["generation_requirements_by_unit"][UNIT][0]["requirement_id"] = "frontend.auth.resources:old"
        with self.assertRaises(GenerationRequirementsError):
            build_auth_guard_candidate(**{**kwargs, "generation_requirements": payload})

    def test_wrong_unit_model_strategy_and_multiple_duties_are_rejected(self) -> None:
        """其他 Unit、模型策略及多条职责不能被静默收敛成一条 auth Task。"""

        kwargs = self._kwargs()
        with self.assertRaises(GenerationRequirementsError):
            build_auth_guard_candidate(**{**kwargs, "unit_id": "frontend:shell"})
        payload = kwargs["generation_requirements"].model_dump(mode="json")
        payload["generation_strategy_by_unit"][UNIT] = "model"
        with self.assertRaises(GenerationRequirementsError):
            build_auth_guard_candidate(**{**kwargs, "generation_requirements": payload})
        payload["generation_strategy_by_unit"][UNIT] = "deterministic"
        another = deepcopy(payload["generation_requirements_by_unit"][UNIT][0])
        another["requirement_id"] = "another-resource-duty"
        payload["generation_requirements_by_unit"][UNIT].append(another)
        with self.assertRaises(GenerationRequirementsError):
            build_auth_guard_candidate(**{**kwargs, "generation_requirements": payload})

    def test_invalid_catalog_is_not_silently_repaired(self) -> None:
        """即使绕过编译器直接构造 DTO，空目录或重复资源仍不能形成候选。"""

        kwargs = self._kwargs()
        for catalog in (None, ResourceCatalog(()), ResourceCatalog(self.catalog.resources * 2)):
            with self.subTest(catalog=catalog), self.assertRaises(GenerationRequirementsError):
                build_auth_guard_candidate(**{**kwargs, "resource_catalog": catalog})

    def test_builder_never_enters_model_attempt_worker_or_file_io(self) -> None:
        """封锁模型、Attempt 分配、Worker/信号量及文件写入后，纯 builder 仍可成功。"""

        kwargs = self._kwargs()
        before = kwargs["generation_requirements"].model_dump_json()
        boundaries = (
            "app.agents.model_factory.create_chat_model",
            "app.agents.main.task_preparer._invoke_live_main_agent",
            "app.services.unit_generation_contracts.AttemptIdentity.allocate",
            "concurrent.futures.ThreadPoolExecutor.submit",
            "asyncio.Semaphore.acquire", "threading.Semaphore.acquire",
            "pathlib.Path.write_text", "pathlib.Path.write_bytes", "builtins.open",
        )
        with ExitStack() as stack:
            guards = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in boundaries]
            candidate = build_auth_guard_candidate(**kwargs)
            self.assertEqual(len(candidate["tasks"]), 1)
            for guard in guards:
                guard.assert_not_called()
        self.assertEqual(kwargs["generation_requirements"].model_dump_json(), before)
        self.assertNotIn("attempt", json.dumps(candidate))


if __name__ == "__main__":
    unittest.main()
