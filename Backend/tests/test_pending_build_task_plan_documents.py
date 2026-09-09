from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.workspace.task_documents import (
    DraftIdentity,
    build_task_plan_draft_sha256,
    build_task_plan_json_path,
    build_task_plan_pending_json_path,
    load_pending_build_task_plan,
    validate_pending_self_digest,
    write_pending_build_task_plan_atomic,
)


PLANNING_RUN_ID = "planning-run-pending-documents"
BASE_DIGEST = "a" * 64
INPUT_FINGERPRINT = "b" * 64
BUILD_EXECUTION_SCOPE = {"type": "page", "targetId": "orders"}
CREATED_AT = "2026-09-06T08:00:00+00:00"
OWNER_SESSION_ID = "session-pending-documents"


def _validated_plan(task_id: str = "page:orders::render") -> dict:
    """构造已完成全局校验的最小 BuildTaskPlan。"""

    return {
        "schema_version": "build-dag.v3",
        "status": "ready",
        "task_registry": {task_id: {"id": task_id, "unit_id": "page:orders"}},
        "task_graph": {
            "schema_version": "build-task-graph.v3",
            "nodes": [task_id],
            "edges": [],
            "topological_order": [task_id],
            "validation": {"is_valid": True, "errors": []},
        },
    }


class PendingBuildTaskPlanDocumentTests(unittest.TestCase):
    """验证 PendingPlan 与 Formal plan 隔离的原子持久化边界。"""

    def setUp(self) -> None:
        """为每个测试创建独立工作区和固定路径。"""

        temporary_workspace = tempfile.TemporaryDirectory()
        self.addCleanup(temporary_workspace.cleanup)
        self.workspace = Path(temporary_workspace.name)
        self.state = {"workspace": str(self.workspace)}
        self.pending_path = build_task_plan_pending_json_path(self.state)
        self.formal_path = build_task_plan_json_path(self.state)

    def _write_pending(self, plan: dict | None = None, **metadata) -> str:
        """使用固定服务端元数据写 Pending，允许测试只覆盖单个身份字段。"""

        return write_pending_build_task_plan_atomic(
            self.state,
            plan if plan is not None else _validated_plan(),
            owner_session_id=metadata.get("owner_session_id", OWNER_SESSION_ID),
            planning_run_id=metadata.get("planning_run_id", PLANNING_RUN_ID),
            base_confirmed_plan_digest=metadata.get(
                "base_confirmed_plan_digest", BASE_DIGEST
            ),
            input_fingerprint=metadata.get("input_fingerprint", INPUT_FINGERPRINT),
            build_execution_scope=metadata.get(
                "build_execution_scope", BUILD_EXECUTION_SCOPE
            ),
            created_at=metadata.get("created_at", CREATED_AT),
        )

    def test_write_pending_plan_to_independent_path(self) -> None:
        """首次写入应创建固定 Pending 文件并可原样读取。"""

        plan = _validated_plan()

        written_path = self._write_pending(plan)
        loaded = load_pending_build_task_plan(self.state)

        self.assertEqual(Path(written_path), self.pending_path)
        self.assertEqual(loaded["confirmation_status"], "pending")
        self.assertIsNone(loaded["confirmed_at"])
        self.assertEqual(loaded["task_registry"], plan["task_registry"])
        self.assertEqual(loaded["draft_identity"]["owner_session_id"], OWNER_SESSION_ID)
        self.assertIsInstance(validate_pending_self_digest(loaded), DraftIdentity)
        self.assertFalse(self.formal_path.exists())

    def test_write_pending_preserves_formal_bytes(self) -> None:
        """写 Pending 前后 Formal 的原始字节必须完全不变。"""

        self.formal_path.parent.mkdir(parents=True)
        formal_bytes = b'{"confirmation_status":"confirmed"}\r\n'
        self.formal_path.write_bytes(formal_bytes)

        self._write_pending()

        self.assertEqual(self.formal_path.read_bytes(), formal_bytes)

    def test_overwrite_pending_is_atomic(self) -> None:
        """覆盖 Pending 应使用原子替换并只暴露完整的新版本。"""

        original = _validated_plan("page:orders::old")
        updated = _validated_plan("page:orders::new")
        self._write_pending(original)

        with patch("app.workspace.json_documents.os.replace", wraps=os.replace) as replace:
            self._write_pending(updated)

        replace.assert_called_once()
        loaded = load_pending_build_task_plan(self.state)
        self.assertEqual(loaded["task_registry"], updated["task_registry"])
        self.assertIsInstance(validate_pending_self_digest(loaded), DraftIdentity)
        self.assertEqual(list(self.pending_path.parent.glob(f".{self.pending_path.name}.*.tmp")), [])

    def test_write_failure_preserves_previous_pending_and_formal(self) -> None:
        """原子替换失败时旧 Pending 与 Formal 均不得损坏。"""

        original = _validated_plan("page:orders::old")
        self._write_pending(original)
        original_pending_bytes = self.pending_path.read_bytes()
        self.formal_path.write_text('{"sentinel":"formal"}\n', encoding="utf-8")
        original_formal_bytes = self.formal_path.read_bytes()

        with patch(
            "app.workspace.json_documents.os.replace",
            side_effect=OSError("replace failed"),
        ):
            with self.assertRaisesRegex(OSError, "replace failed"):
                self._write_pending(_validated_plan("page:orders::new"))

        self.assertEqual(self.pending_path.read_bytes(), original_pending_bytes)
        self.assertEqual(self.formal_path.read_bytes(), original_formal_bytes)
        self.assertEqual(list(self.pending_path.parent.glob(f".{self.pending_path.name}.*.tmp")), [])

    def test_write_succeeds_without_formal_baseline(self) -> None:
        """没有 Formal baseline 时仍可创建 Pending，且不得顺带创建 Formal。"""

        self.assertFalse(self.formal_path.exists())

        self._write_pending(base_confirmed_plan_digest=None)

        self.assertTrue(self.pending_path.is_file())
        self.assertFalse(self.formal_path.exists())
        self.assertIsNone(
            load_pending_build_task_plan(self.state)["draft_identity"][
                "base_confirmed_plan_digest"
            ]
        )

    def test_failed_validation_does_not_write_pending(self) -> None:
        """DAG 校验失败必须在任何 Pending 文件写入前被拒绝。"""

        invalid_plan = _validated_plan()
        invalid_plan["task_graph"]["validation"] = {
            "is_valid": False,
            "errors": ["missing dependency"],
        }

        with self.assertRaisesRegex(ValueError, "task_graph validation"):
            self._write_pending(invalid_plan)

        self.assertFalse(self.pending_path.exists())
        self.assertFalse(self.formal_path.exists())

    def test_invalid_pending_root_is_reported_without_rewrite(self) -> None:
        """读取非对象 Pending 时应保留现场并明确报错。"""

        self.pending_path.parent.mkdir(parents=True)
        raw = json.dumps([_validated_plan()])
        self.pending_path.write_text(raw, encoding="utf-8")

        with self.assertRaisesRegex(ValueError, "JSON object"):
            load_pending_build_task_plan(self.state)

        self.assertEqual(self.pending_path.read_text(encoding="utf-8"), raw)

    def test_draft_digest_is_deterministic_for_canonical_json(self) -> None:
        """相同 JSON 内容即使键顺序不同，也必须得到同一 draft digest。"""

        self._write_pending()
        pending = load_pending_build_task_plan(self.state)
        reordered = {key: pending[key] for key in reversed(tuple(pending))}

        self.assertEqual(
            build_task_plan_draft_sha256(pending),
            build_task_plan_draft_sha256(reordered),
        )
        self.assertEqual(
            pending["draft_identity"]["draft_digest"],
            build_task_plan_draft_sha256(pending),
        )

    def test_draft_digest_changes_with_plan_content(self) -> None:
        """任务正文变化必须改变 draft digest。"""

        self._write_pending(_validated_plan("page:orders::old"))
        original = load_pending_build_task_plan(self.state)
        self._write_pending(_validated_plan("page:orders::new"))
        updated = load_pending_build_task_plan(self.state)

        self.assertNotEqual(
            original["draft_identity"]["draft_digest"],
            updated["draft_identity"]["draft_digest"],
        )

    def test_draft_digest_excludes_only_its_own_field(self) -> None:
        """替换 draft_digest 自身不得影响服务端重算结果。"""

        self._write_pending()
        pending = load_pending_build_task_plan(self.state)
        expected = build_task_plan_draft_sha256(pending)
        pending["draft_identity"]["draft_digest"] = "f" * 64

        self.assertEqual(build_task_plan_draft_sha256(pending), expected)

    def test_draft_digest_changes_with_identity_metadata(self) -> None:
        """除摘要自身外，任一 DraftIdentity 元数据变化都必须改变摘要。"""

        self._write_pending()
        original_digest = load_pending_build_task_plan(self.state)["draft_identity"][
            "draft_digest"
        ]
        changes = (
            {"planning_run_id": "planning-run-other"},
            {"base_confirmed_plan_digest": "c" * 64},
            {"input_fingerprint": "d" * 64},
            {"build_execution_scope": {"type": "page", "targetId": "customers"}},
            {"created_at": "2026-09-06T08:00:01+00:00"},
        )

        for change in changes:
            with self.subTest(change=change):
                self._write_pending(**change)
                updated_digest = load_pending_build_task_plan(self.state)[
                    "draft_identity"
                ]["draft_digest"]
                self.assertNotEqual(original_digest, updated_digest)

    def test_writer_rejects_caller_supplied_draft_identity(self) -> None:
        """assembled plan 不能注入前端或其他调用方自行构造的草稿身份。"""

        plan = _validated_plan()
        plan["draft_identity"] = {
            "planning_run_id": "frontend-supplied",
            "draft_digest": "f" * 64,
        }

        with self.assertRaisesRegex(ValueError, "不得预置"):
            self._write_pending(plan)

        self.assertFalse(self.pending_path.exists())

    def test_tampered_pending_is_detected(self) -> None:
        """落盘后篡改任务内容必须被 self-digest 校验拒绝。"""

        self._write_pending()
        pending = load_pending_build_task_plan(self.state)
        pending["task_registry"]["tampered"] = {
            "id": "tampered",
            "unit_id": "page:orders",
        }

        with self.assertRaisesRegex(ValueError, "draft_digest"):
            validate_pending_self_digest(pending)


if __name__ == "__main__":
    unittest.main()
