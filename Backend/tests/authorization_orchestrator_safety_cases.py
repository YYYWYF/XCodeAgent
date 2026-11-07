"""共享真实编排夹具的安全边界用例，按输入与持久保护集中维护。"""

from copy import deepcopy
from unittest.mock import patch

from app.services import builtin_authorization_orchestrator as orchestration
from app.services.authorization_workspace_projector import RUNTIME, read_record
from app.services.builtin_authorization_state import save_state
from app.services.builtin_authorization_task_store import read_task
from app.services.authorization_manifest import _with_fingerprint
from tests.authorization_seed_fixture import save_plan


class AuthorizationOrchestratorSafetyCases:
    """由生产传输夹具类混入执行，不单独生成第二套业务替身。"""

    def test_seed_child_loss_never_becomes_first_submission(self) -> None:
        """Seed 子对象被清空也不能通过 by-key 404 重发 POST。"""

        self.begin()
        self.assertTrue(self.tick())
        self.assertTrue(read_task(self.root).seedStateExpected)
        record = read_record(self.root, "state.json")
        record.pop("seed")
        save_state(self.root, record)
        self.accepted = False
        self.assertFalse(self.tick())
        self.assert_calls("GET", "POST")
        self.assertEqual(read_task(self.root).status, "needs_attention")

    def test_ready_snapshot_never_overrides_operation_facts(self) -> None:
        """编排成功快照不能覆盖已丢失的权威 Seed 核验事实。"""

        self.begin()
        self.tick()
        self.tick()
        record = read_record(self.root, "state.json")
        record["seed"]["ready"] = False
        save_state(self.root, record)
        view = orchestration.get_builtin_authorization_task(str(self.root))
        self.assertFalse(view["seedReady"])
        self.assertEqual(view["status"], "needs_attention")

    def test_inputs_changed_before_and_during_get(self) -> None:
        """管理员变化阻断下一次副作用，迟到回执不能写入成功。"""

        self.begin()
        self.tick()
        before = deepcopy(read_record(self.root, "state.json"))
        self.before_response = lambda: self.application["authorization"].update(initialAdministratorSubjects=["changed"])
        self.assertFalse(self.tick())
        self.assertEqual(read_record(self.root, "state.json"), before)
        self.assertEqual(read_task(self.root).failureCategory, "inputs_changed")
        self.assertFalse(orchestration.get_builtin_authorization_task(str(self.root))["seedReady"])

    def test_corrupt_missing_and_symlink_states(self) -> None:
        """状态损坏、丢失及软链接不能退化为首次提交。"""

        self.begin()
        self.tick()
        path = self.root / RUNTIME / "state.json"
        path.unlink()
        self.assertFalse(self.tick())
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)
        task_path = self.root / RUNTIME / "orchestration.json"
        task_path.write_text("{}")
        self.assertEqual(orchestration.get_builtin_authorization_task(str(self.root))["status"], "needs_attention")
        task_path.unlink()
        target = self.root / "other.json"
        target.write_text("{}")
        task_path.symlink_to(target)
        self.assertEqual(orchestration.ensure_builtin_authorization_task(str(self.root), source="test")["status"], "needs_attention")

    def test_input_drift_variants_block_before_network(self) -> None:
        """开关、绑定、DDL、Manifest 及 DAG 漂移均不能使用旧任务提交。"""

        self.begin()
        with patch.object(orchestration, "_inputs", side_effect=ValueError("changed")):
            self.assertFalse(self.tick())
        self.assertEqual(self.calls, [])
        application, plan, ddl = deepcopy(self.application), deepcopy(self.plan), self.ddl.read_text()

        def change_manifest():
            """以有效的新正式 Manifest 测试输入漂移，而非仅破坏指纹。"""

            self.plan["authorization_manifest"]["resources"][0]["name"] = "新名称"
            self.plan["authorization_manifest"] = _with_fingerprint(self.plan["authorization_manifest"])
            save_plan(self.root, self.plan)

        for change in (lambda: self.application["authorization"].update(enabled=False),
                       lambda: self.application["managedDatabase"].update(datasourceId=3),
                       lambda: self.ddl.write_text(self.ddl.read_text().replace("BIGINT", "INT")),
                       lambda: self.application["authorization"].update(initialAdministratorSubjects=["other"]),
                       change_manifest, lambda: self.orchestrator_dag.return_value.update(revision="changed")):
            with self.subTest(change=change):
                self.begin()
                change()
                self.assertFalse(self.tick())
                self.assertEqual(self.calls, [])
                # 恢复测试输入不恢复执行身份，下一轮重新建立测试任务。
                self.application.clear()
                self.application.update(deepcopy(application))
                self.plan.clear()
                self.plan.update(deepcopy(plan))
                save_plan(self.root, self.plan)
                self.ddl.write_text(ddl)
                self.orchestrator_dag.return_value = {"status": "ready"}

    def test_post_inflight_input_change_preserves_submitting_guard(self) -> None:
        """POST 已发出后换库，迟到成功不能覆盖 submitting 原证据。"""

        self.begin(fresh=True)
        original = self.schema_respond

        def respond(request):
            """在受理边界后模拟权威配置切换。"""

            response = original(request)
            if request.url.path.endswith("/batch"):
                self.application["managedDatabase"]["datasourceId"] = 3
            return response

        self.schema_respond = respond
        self.assertFalse(self.tick())
        self.assertEqual(read_record(self.root, "state.json")["status"], "submitting")
        self.assertEqual(self.schema_posts, 1)
        self.assertFalse(self.tick())
        self.assertEqual(self.schema_posts, 1)

    def test_contract_mismatch_reports_attention_without_posts(self) -> None:
        """空描述保留合同错误，不为贯通旅程而填造数据。"""

        self.plan["authorization_manifest"]["resources"][0]["description"] = ""
        self.plan["authorization_manifest"] = _with_fingerprint(self.plan["authorization_manifest"])
        save_plan(self.root, self.plan)
        self.begin()
        self.assertFalse(self.tick())
        self.assertEqual(read_task(self.root).failureCategory, "seed_saas_contract_incompatible")
        self.assertEqual(self.calls, [])

    def test_invalid_seed_success_never_becomes_ready(self) -> None:
        """指纹、verified、表名和数量任一错误均不能产生 ready。"""

        self.begin()
        self.tick()
        before = read_record(self.root, "state.json")
        for change in ({"verified": False}, {"requestFingerprint": "sha256:" + "0" * 64},
                       {"tableNames": ["wrong"] * 4}, {"createdCount": 0}):
            with self.subTest(change=change):
                save_state(self.root, before)
                self.begin()
                self.detail_overrides = change
                self.assertFalse(self.tick())
                self.assertNotEqual(read_task(self.root).status, "ready")
        self.assertEqual(sum(method == "POST" for method, _ in self.calls), 1)

    def test_malformed_authority_never_replays(self) -> None:
        """对象格式正确但原状态字段损坏仍必须 fail closed。"""

        self.begin()
        record = read_record(self.root, "state.json")
        record["status"] = "unrecognized"
        save_state(self.root, record)
        self.assertFalse(self.tick())
        self.assertEqual(self.calls, [])
