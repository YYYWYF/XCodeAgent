"""T7.3 Confirm 的真实磁盘、实际 DAG 门禁与故障注入测试。"""

from copy import deepcopy
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from concurrent.futures import ThreadPoolExecutor
from app.domain.application_lifecycle import ApplicationLifecycle
from app.services.application_lifecycle import write_application_lifecycle

from app.services.build_task_plan_lifecycle import (
    abandon_pending_build_task_plan,
    confirm_pending_build_task_plan,
)
from app.services.dag_planning_inputs import _input_digest
from app.workspace.task_documents import (
    build_task_plan_draft_sha256, build_task_plan_json_path, build_task_plan_pending_json_path,
    build_task_plan_sha256, load_pending_build_task_plan, write_pending_build_task_plan_atomic,
)
from tests.dag_planning_baseline_fixtures import build_context, compiled_plan, execution_scope, project_plan
from tests.dag_planning_orchestrator_fixtures import planning_inputs


class ConfirmPromotionTests(unittest.TestCase):
    """按请求、草稿、基线、输入、DAG、提交、清理的顺序验证提升。"""

    def setUp(self):
        """用正式 compiler 构造真实 DAG，并为每例隔离 Formal/Pending 文件。"""

        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.state = {"workspace": directory.name}
        self.formal_path = build_task_plan_json_path(self.state)
        self.pending_path = build_task_plan_pending_json_path(self.state)
        self.plan = project_plan()
        self.scope = execution_scope()
        self.context = build_context(self.plan, self.scope)
        self.inputs = self._inputs()
        self.draft = compiled_plan(self.plan, self.scope)
        self.assertEqual(self.draft["status"], "ready")
        self._write_pending()

    def _inputs(self, baseline=None):
        """从当前正式合同与实际 reuse/skeleton 构造相同 Planning DTO。"""

        return planning_inputs(plan=self.plan, baseline=baseline, scope=self.scope,
                               context=self.context, required=self.context["required_unit_ids"])

    def _write_pending(self, run_id="run-1"):
        """使用 T7.2 writer 和 Planning 的真实输入指纹生成待确认身份。"""

        write_pending_build_task_plan_atomic(
            self.state, self.draft, planning_run_id=run_id,
            base_confirmed_plan_digest=_input_digest(self.inputs.base_confirmed_plan)
            if self.inputs.base_confirmed_plan is not None else None,
            input_fingerprint=_input_digest(self.inputs.model_dump(mode="json")),
            build_execution_scope=self.scope, created_at="2026-09-06T00:00:00Z",
        )
        identity = load_pending_build_task_plan(self.state)["draft_identity"]
        self.request = {key: identity[key] for key in ("planning_run_id", "draft_digest")}

    def _write_lifecycle(self) -> None:
        lifecycle = ApplicationLifecycle.model_validate(
            {
                "application": {"id": "app-confirm", "name": "Confirm 测试"},
                "updatedAt": "2026-09-08T00:00:00Z",
                "revision": 4,
                "initialization": {"stage": "ready_for_workbench", "status": "completed"},
                "activeRunId": "workflow-current",
                "activeExecutions": {
                    "workflow-current": {
                        "scope": "page",
                        "targetId": "orders",
                        "pageId": "orders",
                        "threadId": "thread-confirm",
                        "runId": "workflow-current",
                        "phase": "prepare_build_tasks",
                        "status": "awaiting_user",
                        "pendingInteraction": {
                            "id": "pending-dag",
                            "type": "task_plan_confirmation",
                            "basedOnRevision": 4,
                            "payload": {"mode": "build_task_plan_confirmation"},
                            "artifactRefs": [],
                            "createdAt": "2026-09-08T00:00:00Z",
                        },
                        "startedAt": "2026-09-08T00:00:00Z",
                        "updatedAt": "2026-09-08T00:00:00Z",
                    }
                },
            }
        )
        write_application_lifecycle(self.state["workspace"], lifecycle)

    def _confirm(self, **changes):
        """调用被测公共函数，不 mock 门禁或文件读写。"""

        return confirm_pending_build_task_plan(self.state, **{**self.request, "current_inputs": self.inputs, **changes})

    def _rewrite(self, plan, *, resign=False):
        """模拟损坏磁盘或服务端写入错误；重签可将测试推进到后续门禁。"""

        if resign:
            plan["draft_identity"]["draft_digest"] = build_task_plan_draft_sha256(plan)
            self.request["draft_digest"] = plan["draft_identity"]["draft_digest"]
        self.pending_path.write_text(json.dumps(plan), encoding="utf-8")

    def _assert_rejected(self, status, **changes):
        """拒绝必须保留 Formal 与 Pending 原始字节，且不能调用 atomic replace。"""

        formal = self.formal_path.read_bytes() if self.formal_path.exists() else None
        pending = self.pending_path.read_bytes() if self.pending_path.exists() else None
        with patch("app.workspace.json_documents.os.replace", wraps=os.replace) as replace:
            result = self._confirm(**changes)
        self.assertEqual(result.status, status, result)
        replace.assert_not_called()
        self.assertEqual(self.formal_path.read_bytes() if self.formal_path.exists() else None, formal)
        self.assertEqual(self.pending_path.read_bytes() if self.pending_path.exists() else None, pending)
        return result

    def test_normal_confirm_without_baseline(self):
        """首次确认原子保存身份和完整正文，并在提交后删除 Pending。"""

        pending = load_pending_build_task_plan(self.state)
        with patch("app.workspace.json_documents.os.replace", wraps=os.replace) as replace:
            result = self._confirm()
        self.assertEqual(result.status, "confirmed", result.errors)
        replace.assert_called_once()
        self.assertEqual(Path(replace.call_args.args[1]), self.formal_path)
        formal = json.loads(self.formal_path.read_text())
        self.assertEqual(formal["confirmed_from"], self.request)
        self.assertEqual(formal["confirmation_status"], "confirmed")
        self.assertTrue(formal["confirmed_at"])
        self.assertNotIn("draft_identity", formal)
        for key, value in pending.items():
            if key not in {"confirmation_status", "confirmed_at", "draft_identity"}:
                self.assertEqual(formal[key], value)
        self.assertFalse(self.pending_path.exists())

    def test_normal_confirm_replaces_exact_baseline(self):
        """已有 ConfirmedPlan 时精确绑定旧摘要，并替换成新确认身份。"""

        self.assertEqual(self._confirm().status, "confirmed")
        baseline = json.loads(self.formal_path.read_text())
        self.inputs = self._inputs(baseline)
        self._write_pending("run-2")
        self.assertEqual(load_pending_build_task_plan(self.state)["draft_identity"]["base_confirmed_plan_digest"],
                         build_task_plan_sha256(baseline))
        self.assertEqual(self._confirm().status, "confirmed")
        self.assertEqual(json.loads(self.formal_path.read_text())["confirmed_from"], self.request)

    def test_canonical_key_reordering_preserves_confirm_identity(self):
        """只改变 JSON 对象键顺序不影响 digest，也不能被图检查误判为失效。"""

        pending = load_pending_build_task_plan(self.state)
        registry = pending["task_registry"]
        pending["task_registry"] = {key: registry[key] for key in reversed(registry)}
        self._rewrite(pending)
        self.assertEqual(self._confirm().status, "confirmed")

    def test_stale_ui_and_invalid_request_identity(self):
        """旧 Run、旧 digest、空字段或隐式类型转换均不可确认当前草稿。"""

        for changes in ({"planning_run_id": "old"}, {"draft_digest": "a" * 64},
                        {"planning_run_id": ""}, {"draft_digest": ""}, {"planning_run_id": 7}):
            with self.subTest(changes=changes):
                self._assert_rejected("stale_draft", **changes)

    def test_tampered_pending(self):
        """正文篡改即使仍有 validation=true，也必须在 DAG 检查前拒绝。"""

        pending = load_pending_build_task_plan(self.state)
        next(iter(pending["task_registry"].values()))["title"] = "篡改任务"
        self._rewrite(pending)
        with patch("app.services.build_task_plan_lifecycle._dag_gate_errors") as gate:
            self._assert_rejected("stale_draft")
        gate.assert_not_called()

    def test_missing_or_malformed_pending(self):
        """不存在或无法解析的 Pending 不得回退到 Formal/checkpoint。"""

        self.pending_path.unlink()
        self._assert_rejected("stale_draft")
        for content in ("{broken", "[]"):
            self.pending_path.write_text(content)
            self._assert_rejected("stale_draft")

    def test_baseline_changed_missing_or_invalid(self):
        """基线新增、改动、消失或损坏全部阻断，不将非法正式文件当空基线。"""

        baseline = {**self.draft, "confirmation_status": "confirmed"}
        self.formal_path.write_text(json.dumps(baseline))
        self._assert_rejected("stale_base")
        self.inputs = self._inputs(baseline)
        self._write_pending()
        for content in (json.dumps({**baseline, "confirmed_at": "changed"}), "{}", "{broken", "[]"):
            self.formal_path.write_text(content)
            self._assert_rejected("stale_base")
        self.formal_path.unlink()
        self._assert_rejected("stale_base")

    def test_inputs_stale_and_scope_stale(self):
        """每类冻结输入变化均使指纹失效，包括 scope 和调用方错误基线。"""

        payload = self.inputs.model_dump(mode="json")
        for field in ("project_plan", "skeleton_plan", "build_context", "workspace_snapshot", "build_execution_scope"):
            changed = deepcopy(payload)
            changed[field]["changed"] = True
            with self.subTest(field=field):
                self._assert_rejected("stale_inputs", current_inputs=changed)
        changed = deepcopy(payload)
        changed["base_confirmed_plan"] = {**self.draft, "confirmation_status": "confirmed"}
        self._assert_rejected("stale_inputs", current_inputs=changed)

    def test_validation_order(self):
        """同时制造多层错误，结果必须按身份→self→base→inputs→DAG 决定。"""

        pending = load_pending_build_task_plan(self.state)
        pending["status"] = "failed"
        self._rewrite(pending)
        self.formal_path.write_text("{}")
        self._assert_rejected("stale_draft", current_inputs={})
        self._rewrite(pending, resign=True)
        self._assert_rejected("stale_base", current_inputs={})
        self.formal_path.unlink()
        self._assert_rejected("stale_inputs", current_inputs={})
        self._assert_rejected("invalid_dag")

    def test_dag_gate_rejects_false_validation_and_forged_valid_graph(self):
        """重签的无效图必须被真实 DAG gate 拦截，不能信任 is_valid 缓存。"""

        original = load_pending_build_task_plan(self.state)
        for fault in ("validation", "cycle", "nodes", "blocked", "owner", "registry", "order", "unit"):
            pending = deepcopy(original)
            task = next(iter(pending["task_registry"].values()))
            if fault == "validation":
                pending["task_graph"]["validation"] = {"is_valid": False, "errors": []}
            elif fault == "cycle":
                task["dependencies"] = [task["id"]]
            elif fault == "nodes":
                pending["task_graph"]["nodes"] = []
            elif fault == "blocked":
                pending["execution"]["batches"] = [{"mode": "blocked"}]
            elif fault == "owner":
                task["owner"] = "wrong-owner"
            elif fault == "order":
                pending["task_graph"]["topological_order"].reverse()
            elif fault == "unit":
                task["unit_id"] = []
            else:
                task["id"] = "forged"
            self._rewrite(pending, resign=True)
            with self.subTest(fault=fault):
                self._assert_rejected("invalid_dag")

    def test_formal_write_failure_preserves_both_files(self):
        """原子 replace 失败不得损坏旧 Formal、提前删 Pending 或遗留临时文件。"""

        self.assertEqual(self._confirm().status, "confirmed")
        self.inputs = self._inputs(json.loads(self.formal_path.read_text()))
        self._write_pending("run-2")
        original = self.formal_path.read_bytes(), self.pending_path.read_bytes()
        with patch("app.workspace.json_documents.os.replace", side_effect=OSError("formal replace failed")):
            with self.assertRaisesRegex(OSError, "formal replace failed"):
                self._confirm()
        self.assertEqual((self.formal_path.read_bytes(), self.pending_path.read_bytes()), original)
        self.assertFalse(list(self.formal_path.parent.glob(".*.tmp")))
        self.assertEqual(self._confirm().status, "confirmed")

    def test_formal_success_pending_delete_failure_and_retry(self):
        """提交成功后清理失败仍算已确认，重试仅清理且不能重写 Formal。"""

        original_unlink = Path.unlink

        def fail_pending(path, *args, **kwargs):
            """只对 Pending 删除注入故障，保留 atomic writer 临时文件清理。"""

            if path == self.pending_path:
                self.assertEqual(json.loads(self.formal_path.read_text())["confirmed_from"], self.request)
                raise OSError("pending delete failed")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_pending):
            result = self._confirm()
        self.assertEqual(result.status, "confirmed")
        self.assertEqual(result.pending_cleanup_error, "pending delete failed")
        self.assertTrue(self.pending_path.exists())
        formal_bytes = self.formal_path.read_bytes()
        with patch("app.workspace.json_documents.os.replace") as replace:
            result = self._confirm(current_inputs={})
        self.assertEqual(result.status, "already_confirmed")
        self.assertIsNone(result.pending_cleanup_error)
        replace.assert_not_called()
        self.assertEqual(self.formal_path.read_bytes(), formal_bytes)
        self.assertFalse(self.pending_path.exists())

    def test_duplicate_confirm_idempotent(self):
        """无 Pending 的重复确认保持 Formal 字节和 confirmed_at 不变。"""

        self.assertEqual(self._confirm().status, "confirmed")
        original = self.formal_path.read_bytes()
        self.assertEqual(self._confirm().status, "already_confirmed")
        self.assertEqual(self.formal_path.read_bytes(), original)
        self._assert_rejected("stale_draft", planning_run_id="another-run")

    def test_old_duplicate_never_deletes_new_pending(self):
        """旧请求已提交后出现新 Pending，旧请求重试只能报告自身成功。"""

        self.assertEqual(self._confirm().status, "confirmed")
        old_request = self.request.copy()
        formal = self.formal_path.read_bytes()
        self.inputs = self._inputs(json.loads(formal))
        self._write_pending("run-2")
        pending = self.pending_path.read_bytes()
        self.assertEqual(self._confirm(**old_request).status, "already_confirmed")
        self.assertEqual(self.pending_path.read_bytes(), pending)
        self.assertEqual(self.formal_path.read_bytes(), formal)

    def test_concurrent_duplicate_has_single_commit(self):
        """同进程重复提交串行核验，确保只有一个调用执行 atomic replace。"""

        with patch("app.workspace.json_documents.os.replace", wraps=os.replace) as replace:
            with ThreadPoolExecutor(max_workers=2) as executor:
                results = list(executor.map(lambda _: self._confirm(), range(2)))
        self.assertEqual(sorted(result.status for result in results), ["already_confirmed", "confirmed"])
        replace.assert_called_once()

    def test_abandon_after_confirm_is_already_confirmed_and_preserves_formal(self):
        """Confirm 先提交时，随后 Abandon 明确拒绝 already-confirmed，不能回滚 Formal。"""

        self.assertEqual(self._confirm().status, "confirmed")
        formal = self.formal_path.read_bytes()

        result = abandon_pending_build_task_plan(self.state, **self.request)

        self.assertEqual(result.status, "already_confirmed")
        self.assertEqual(self.formal_path.read_bytes(), formal)

    def test_abandoned_tombstone_blocks_confirm_even_when_pending_cleanup_failed(self):
        """Abandon commit 后残留 Pending 只是 cleanup residue，不能再次 Promote。"""
        self._write_lifecycle()
        original_unlink = Path.unlink

        def fail_pending(path, *args, **kwargs):
            if path == self.pending_path:
                raise OSError("pending delete failed")
            return original_unlink(path, *args, **kwargs)

        with patch.object(Path, "unlink", fail_pending):
            with self.assertRaisesRegex(OSError, "pending delete failed"):
                abandon_pending_build_task_plan(
                    self.state,
                    **self.request,
                    workflow_run_id="workflow-current",
                )

        self.assertTrue(self.pending_path.exists())
        result = self._confirm()
        self.assertEqual(result.status, "stale_draft")
        self.assertTrue(any("已被放弃" in error for error in result.errors))
        self.assertFalse(self.formal_path.exists())

    def test_abandoned_draft_cannot_be_promoted(self):
        """Abandon 先提交后，同一 DraftIdentity 永久失去 Confirm/Promote 资格。"""

        abandoned = abandon_pending_build_task_plan(self.state, **self.request)
        confirmed = self._confirm()

        self.assertEqual(abandoned.status, "abandoned")
        self.assertEqual(confirmed.status, "stale_draft")
        self.assertFalse(self.formal_path.exists())

    def test_confirm_abandon_race_has_one_backend_winner(self):
        """Confirm/Abandon 竞争由同一 Backend lifecycle 锁仲裁，最终状态不会分裂。"""

        with ThreadPoolExecutor(max_workers=2) as executor:
            confirm_future = executor.submit(self._confirm)
            abandon_future = executor.submit(
                abandon_pending_build_task_plan,
                self.state,
                **self.request,
            )
            confirmed = confirm_future.result()
            abandoned = abandon_future.result()

        outcome = (confirmed.status, abandoned.status)
        self.assertIn(
            outcome,
            {
                ("confirmed", "already_confirmed"),
                ("stale_draft", "abandoned"),
            },
        )
        self.assertFalse(self.pending_path.exists())
        self.assertEqual(self.formal_path.exists(), confirmed.status == "confirmed")


class PlanningPromotionIntegrationTests(unittest.IsolatedAsyncioTestCase):
    """串起 T6.4 与 T7.2/T7.3，验证真实新链路产物可以通过 Confirm。"""

    async def test_bounded_parallel_plan_pending_confirm(self):
        """仅模型响应固定，Queue/Local/Assembly/Global/Pending/Confirm 均执行真实服务。"""

        from app.services.dag_planning_orchestrator import plan_dag_sequential
        from app.services.planning_frozen import plain_json
        from app.services.unit_generation_contracts import UnitGenerationAttemptResult, UnitGenerationPolicy
        from tests.dag_planning_orchestrator_fixtures import model_tasks
        from tests.test_unit_generation_contracts import _policy_payload

        async def generate(job, **kwargs):
            """为当前独立 Unit 提供严格模型正文，不替换任何平台校验。"""

            tasks = model_tasks(job)
            return UnitGenerationAttemptResult(
                identity=job.identity, input_fingerprint=job.context.input_fingerprint,
                raw_response=json.dumps({"tasks": tasks}), tasks=tasks,
            )

        with tempfile.TemporaryDirectory() as directory:
            state = {"workspace": directory}
            inputs = planning_inputs()
            result = await plan_dag_sequential(
                inputs, workspace_state=state, planning_run_id="integration-run", workflow_run_id="workflow",
                thread_id="thread", policy=UnitGenerationPolicy(**_policy_payload()), generate_once=generate,
            )
            run = result.planning_run
            write_pending_build_task_plan_atomic(
                state, plain_json(result.assembly.assembled_plan), planning_run_id=run.planning_run_id,
                base_confirmed_plan_digest=run.base_confirmed_plan_digest, input_fingerprint=run.input_fingerprint,
                build_execution_scope=plain_json(run.build_execution_scope), created_at=run.updated_at,
            )
            identity = load_pending_build_task_plan(state)["draft_identity"]
            confirmed = confirm_pending_build_task_plan(
                state, planning_run_id=identity["planning_run_id"], draft_digest=identity["draft_digest"],
                current_inputs=inputs,
            )
            self.assertEqual(confirmed.status, "confirmed", confirmed.errors)
            self.assertEqual(confirmed.confirmed_plan["task_registry"], result.assembly.assembled_plan["task_registry"])
            self.assertFalse(build_task_plan_pending_json_path(state).exists())


if __name__ == "__main__":
    unittest.main()
