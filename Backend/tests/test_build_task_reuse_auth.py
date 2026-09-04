"""T2.5B 当前 auth capability 的 provider 优先、精确 workspace 证据与缺项回归。"""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest

from app.services.authorization_frontend_projection import RESOURCES_RELATIVE_PATH, _render_resources
from app.services.authorization_resource_catalog import compile_frontend_resource_catalog, resource_catalog_fingerprint
from app.services.authorization_resource_inspection import inspect_authorization_resource_catalog
from app.services.build_task_reuse import resolve_reuse_facts
from app.services.build_task_reuse_contracts import ReuseFacts
from app.services.build_unit_skeleton import ensure_build_unit_skeleton
from app.services.planning_frozen import freeze_json
from app.services.unit_generation_requirements import resolve_generation_requirements
from tests.test_build_task_reuse import _inputs as _base_inputs, _plan, _task
from tests.test_unit_generation_requirements_auth import _auth_plan


UNIT = "frontend:auth-guard"


class BuildTaskReuseAuthTests(unittest.TestCase):
    """通过真实文件检查和既有职责服务验证三种结果，不创建生产 Task。"""

    def setUp(self) -> None:
        """构造包含系统、页面、操作资源的 R1/R2 及独立临时工作区。"""

        self.r1 = _auth_plan()
        self.r1["authorization_manifest"]["resources"].append({
            "resourceKey": "system_authorization_management", "type": "system",
            "targetResourceRef": "system:authorization_management",
        })
        self.r2 = deepcopy(self.r1)
        self.r2["authorization_manifest"]["resources"].append({
            "resourceKey": "users_export", "type": "operation", "targetResourceRef": "action:users:export",
        })
        self.catalog1 = compile_frontend_resource_catalog(self.r1["authorization_manifest"])
        self.catalog2 = compile_frontend_resource_catalog(self.r2["authorization_manifest"])
        self.cap1 = f"frontend.auth.resources:{resource_catalog_fingerprint(self.catalog1)}"
        self.cap2 = f"frontend.auth.resources:{resource_catalog_fingerprint(self.catalog2)}"
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name)
        self.path = self.workspace / RESOURCES_RELATIVE_PATH
        self.inputs = {
            **_base_inputs(), "formal_plan": self.r2,
            "unit_skeleton": ensure_build_unit_skeleton(self.r2, {}),
        }

    def _write_resources(self, catalog) -> bytes:
        """仅为测试写入既有 renderer 的真实完整投影，无需 routes.tsx。"""

        source = _render_resources(catalog.frontend_resources()).encode("utf-8")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(source)
        return source

    def _inspect(self, catalog=None) -> dict:
        """使用当前快照 revision 执行真实只读资源检查。"""

        return inspect_authorization_resource_catalog(
            self.workspace, catalog or self.catalog2, workspace_revision="snapshot-1",
        )

    def _requirements(self, facts: ReuseFacts):
        """把新事实送入既有职责缺项服务，验证复用或 deterministic 判定。"""

        return resolve_generation_requirements(
            required_unit_ids=[UNIT], build_execution_scope={"type": "page", "targetId": "orders"},
            unit_skeleton=self.inputs["unit_skeleton"], reuse_facts=facts, formal_target=self.r2,
        )

    def _assert_missing(self, facts: ReuseFacts) -> None:
        """当前 R 不被 provider/external 满足时，应明确保留唯一 deterministic 职责。"""

        self.assertEqual(facts.issues, ())
        self.assertNotIn(self.cap2, facts.reusable_capabilities_by_unit[UNIT])
        self.assertEqual(facts.external_capabilities, ())
        result = self._requirements(facts)
        self.assertEqual(result.generation_strategy_by_unit[UNIT], "deterministic")
        self.assertEqual(result.planning_unit_ids, (UNIT,))
        self.assertEqual([item.requirement_id for item in result.generation_requirements_by_unit[UNIT]], [self.cap2])

    def test_existing_r2_confirmed_task_is_provider_without_file(self) -> None:
        """confirmed 计划内的 pending R2 Task 已占据职责，无文件也无需再次规划。"""

        self.inputs["confirmed_plan"] = _plan(_task("current", unit_id=UNIT, provides=[self.cap2]))
        self.inputs["auth_resource_inspection"] = self._inspect()
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.issues, ())
        self.assertEqual(facts.reusable_capabilities_by_unit[UNIT][self.cap2], ("current",))
        self.assertEqual(facts.retained_task_ids_by_unit[UNIT], ("current",))
        self.assertEqual(facts.external_capabilities, ())
        self.assertEqual(self._requirements(facts).planning_unit_ids, ())
        self.assertFalse(self.path.exists())

    def test_existing_r2_failed_task_is_same_provider(self) -> None:
        """仅改变 Task execution status 不改变事实，即使 workspace 仍是 R1。"""

        self._write_resources(self.catalog1)
        self.inputs["auth_resource_inspection"] = self._inspect()
        results = []
        for status in ("pending", "failed", "completed", "running"):
            self.inputs["confirmed_plan"] = _plan(_task("current", unit_id=UNIT, status=status, provides=[self.cap2]))
            facts = resolve_reuse_facts(**self.inputs)
            self.assertEqual(facts.issues, ())
            self.assertEqual(facts.reusable_capabilities_by_unit[UNIT][self.cap2], ("current",))
            self.assertEqual(self._requirements(facts).generation_strategy_by_unit[UNIT], "reuse_only")
            results.append(facts)
        self.assertTrue(all(item == results[0] for item in results))

    def test_current_provider_takes_priority_over_workspace_evidence(self) -> None:
        """当前 provider 存在时，不重复发布 external，也不被旧 workspace 证据阻断。"""

        self._write_resources(self.catalog2)
        self.inputs["confirmed_plan"] = _plan(_task("current", unit_id=UNIT, provides=[self.cap2]))
        for evidence in (self._inspect(), {"status": "satisfied", "resource_catalog_fingerprint": "old"}):
            self.inputs["auth_resource_inspection"] = evidence
            facts = resolve_reuse_facts(**self.inputs)
            self.assertEqual(facts.issues, ())
            self.assertEqual(facts.external_capabilities, ())
            self.assertEqual(facts.reusable_capabilities_by_unit[UNIT][self.cap2], ("current",))

    def test_workspace_already_r2_provides_external_without_task(self) -> None:
        """完整 workspace 投影可满足当前 R2，且不要求路由文件或伪造 provider Task。"""

        self._write_resources(self.catalog2)
        self.inputs["auth_resource_inspection"] = self._inspect()
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.issues, ())
        self.assertEqual(facts.retained_task_ids_by_unit[UNIT], ())
        self.assertEqual(facts.reusable_capabilities_by_unit[UNIT], {})
        capability, = facts.external_capabilities
        self.assertEqual((capability.unit_id, capability.capability_id), (UNIT, self.cap2))
        self.assertEqual(capability.source, "authorization_resource_catalog")
        self.assertEqual(capability.workspace_revision, "snapshot-1")
        self.assertEqual(capability.source_refs["path"], RESOURCES_RELATIVE_PATH.as_posix())
        self.assertEqual(capability.source_refs["content_sha256"], capability.source_refs["expected_projection_sha256"])
        self.assertEqual(self._requirements(facts).generation_strategy_by_unit[UNIT], "reuse_only")
        self.assertEqual(self._requirements(facts).planning_unit_ids, ())

    def test_workspace_r1_does_not_satisfy_expected_r2(self) -> None:
        """Scope 外新增操作也必须被完整投影覆盖，旧文件不可误判为 R2。"""

        self._write_resources(self.catalog1)
        self.inputs["auth_resource_inspection"] = self._inspect()
        self.assertEqual(self.inputs["auth_resource_inspection"]["status"], "mismatch")
        self._assert_missing(resolve_reuse_facts(**self.inputs))

    def test_no_file_is_missing_and_never_created(self) -> None:
        """文件不存在时留下 deterministic 缺项，检查器不能预写资源或目录。"""

        self.inputs["auth_resource_inspection"] = self._inspect()
        self.assertEqual(self.inputs["auth_resource_inspection"]["status"], "missing")
        self._assert_missing(resolve_reuse_facts(**self.inputs))
        self.assertEqual(list(self.workspace.iterdir()), [])

    def test_historical_r1_is_retained_while_r2_is_missing(self) -> None:
        """R1 provider 不覆盖 R2，历史任务及其 capability 均继续保留。"""

        self.inputs["confirmed_plan"] = _plan(_task("old", unit_id=UNIT, provides=[self.cap1]))
        self.inputs["auth_resource_inspection"] = self._inspect()
        original = deepcopy(self.inputs)
        facts = resolve_reuse_facts(**self.inputs)
        self._assert_missing(facts)
        self.assertEqual(facts.retained_task_ids_by_unit[UNIT], ("old",))
        self.assertEqual(facts.reusable_capabilities_by_unit[UNIT][self.cap1], ("old",))
        self.assertEqual(self.inputs, original)

    def test_historical_r1_and_workspace_r2_coexist(self) -> None:
        """workspace 的 R2 能力不会删除历史 R1 provider，也不向历史 Task 追加能力。"""

        self._write_resources(self.catalog2)
        self.inputs["confirmed_plan"] = _plan(_task("old", unit_id=UNIT, provides=[self.cap1]))
        self.inputs["auth_resource_inspection"] = self._inspect()
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.retained_task_ids_by_unit[UNIT], ("old",))
        self.assertEqual(facts.reusable_capabilities_by_unit[UNIT], {self.cap1: ("old",)})
        self.assertEqual(facts.external_capabilities[0].capability_id, self.cap2)
        self.assertEqual(self._requirements(facts).planning_unit_ids, ())

    def test_full_projection_rejects_missing_extra_changed_and_executable_content(self) -> None:
        """不能只搜索常量或比较子集；缺资源、额外语句、内容和格式漂移都不满足。"""

        source = self._write_resources(self.catalog2)
        for changed in (
            b"export const RESOURCES = {} as const;", source.replace(b"users_export", b"other_export"),
            source.replace(b'    USERS_EXPORT: "users_export",\n', b""),
            source + b"\nRESOURCES.PAGE.USERS = 'changed';\n", source + b"\n// extra\n",
            source.replace(b"  ", b"    "), source.replace(b"\n", b"\r\n"), b"\xff",
        ):
            with self.subTest(source=changed[:60]):
                self.path.write_bytes(changed)
                self.inputs["auth_resource_inspection"] = self._inspect()
                self.assertEqual(self.inputs["auth_resource_inspection"]["status"], "mismatch")
                self._assert_missing(resolve_reuse_facts(**self.inputs))
                self.assertEqual(self.path.read_bytes(), changed)

    def test_scanned_file_or_absent_evidence_does_not_prove_capability(self) -> None:
        """真实文件存在或扫描结果列出路径仍不够，必须传入同次完整检查证据。"""

        self._write_resources(self.catalog2)
        self.inputs["workspace_snapshot"]["high_value_files"] = [{"path": RESOURCES_RELATIVE_PATH.as_posix()}]
        self._assert_missing(resolve_reuse_facts(**self.inputs))

    def test_stale_or_incomplete_satisfied_evidence_is_rejected(self) -> None:
        """R、revision、路径或摘要不匹配时不能授予外部能力，且错误不可模型重试。"""

        self._write_resources(self.catalog2)
        evidence = self._inspect()
        for field in ("resource_catalog_fingerprint", "workspace_revision", "path", "content_sha256", "expected_projection_sha256"):
            with self.subTest(field=field):
                self.inputs["auth_resource_inspection"] = {**evidence, field: "stale"}
                facts = resolve_reuse_facts(**self.inputs)
                self.assertEqual(facts.external_capabilities, ())
                issue, = facts.issues
                self.assertEqual(issue.code, "AUTH_RESOURCE_EVIDENCE_INVALID")
                self.assertEqual(issue.unit_ids, (UNIT,))
                self.assertFalse(issue.retryable)
        self.inputs["auth_resource_inspection"] = {"status": "satisfied"}
        self.assertEqual(resolve_reuse_facts(**self.inputs).external_capabilities, ())

    def test_old_catalog_evidence_cannot_be_relabelled_r2(self) -> None:
        """即使两个源身份投影相同，旧 R 的证据也必须针对当前 R 重新检查。"""

        self._write_resources(self.catalog2)
        self.inputs["auth_resource_inspection"] = self._inspect()
        self.r2["authorization_manifest"]["resources"][-1]["targetResourceRef"] = "action:users:EXPORT"
        current = compile_frontend_resource_catalog(self.r2["authorization_manifest"])
        self.assertEqual(current.frontend_resources(), self.catalog2.frontend_resources())
        self.assertNotEqual(resource_catalog_fingerprint(current), resource_catalog_fingerprint(self.catalog2))
        self.assertEqual(resolve_reuse_facts(**self.inputs).issues[0].code, "AUTH_RESOURCE_EVIDENCE_INVALID")
        self.inputs["auth_resource_inspection"] = self._inspect(current)
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.issues, ())
        self.assertEqual(facts.external_capabilities[0].capability_id, f"frontend.auth.resources:{resource_catalog_fingerprint(current)}")

    def test_unconfirmed_formal_plan_cannot_prove_current_resource_identity(self) -> None:
        """未确认的正式源数据不能给 workspace 授予当前目录能力。"""

        self._write_resources(self.catalog2)
        self.inputs["auth_resource_inspection"] = self._inspect()
        self.r2["confirmation_status"] = "pending"
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(facts.external_capabilities, ())
        self.assertEqual(facts.issues[0].code, "AUTH_RESOURCE_INPUT_INVALID")

    def test_inspection_and_facts_are_read_only_frozen_and_serializable(self) -> None:
        """检查与事实计算保持文件内容和 mtime 不变，证据冻结并支持标准 JSON 往返。"""

        source = self._write_resources(self.catalog2)
        before = self.path.stat().st_mtime_ns
        self.inputs["auth_resource_inspection"] = self._inspect()
        original = deepcopy(self.inputs)
        facts = resolve_reuse_facts(**self.inputs)
        self.assertEqual(resolve_reuse_facts(**freeze_json(self.inputs)), facts)
        self.assertEqual(ReuseFacts.model_validate_json(facts.model_dump_json()), facts)
        self.assertEqual(self.inputs, original)
        self.assertEqual(self.path.read_bytes(), source)
        self.assertEqual(self.path.stat().st_mtime_ns, before)
        with self.assertRaises(TypeError):
            facts.external_capabilities[0].source_refs["path"] = "changed"

    def test_symlink_outside_workspace_cannot_prove_capability(self) -> None:
        """工作区外的同内容文件不能通过 resources.ts 符号链接充当本工作区能力。"""

        with tempfile.TemporaryDirectory() as outside:
            other = Path(outside) / "resources.ts"
            other.write_text(_render_resources(self.catalog2.frontend_resources()), encoding="utf-8")
            self.path.parent.mkdir(parents=True)
            self.path.symlink_to(other)
            self.inputs["auth_resource_inspection"] = self._inspect()
            self.assertEqual(self.inputs["auth_resource_inspection"]["status"], "unsafe_path")
            self._assert_missing(resolve_reuse_facts(**self.inputs))


if __name__ == "__main__":
    unittest.main()
