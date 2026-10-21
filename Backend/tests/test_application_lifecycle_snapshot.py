"""验证生命周期快照的宽松读取与无损写回。"""

import json
import tempfile
import unittest

from app.services.application_lifecycle import (
    ApplicationLifecycleCorruptedError,
    application_lifecycle_path,
    load_application_lifecycle,
    write_application_lifecycle,
)


class ApplicationLifecycleSnapshotTests(unittest.TestCase):
    """可选与额外字段不阻断工作区读取，必要身份仍需完整。"""

    def snapshot(self) -> dict:
        """构造省略全部可选字段并包含嵌套额外数据的当前快照。"""

        return {
            "application": {"id": "app", "name": "测试应用", "displayHint": "保留"},
            "updatedAt": "2026-10-21T00:00:00Z",
            "revision": 1,
            "initialization": {
                "stage": "ready_for_workbench", "status": "completed", "displayHint": "就绪",
            },
            "displayHint": {"collapsed": True},
            "activeFormalRevision": {
                "changeId": "change", "formalBranch": "workbench_plan_revision",
                "sourceThreadId": "source-thread", "sourceRunId": "source-run",
                "request": "修改技术规划", "target": {"type": "application", "displayHint": "应用"},
                "impactInteractionId": "impact", "planningThreadId": "planning-thread",
                "status": "awaiting_user", "pendingApplicationConfigChanges": [],
                "pendingInitialAdministratorSubjects": [],
            },
        }

    def test_optional_and_extra_fields_survive_read_and_write(self) -> None:
        """根对象及正式修订的额外字段完整保留，可选字段缺失时使用默认值。"""

        with tempfile.TemporaryDirectory() as directory:
            payload = self.snapshot()
            path = application_lifecycle_path(directory)
            path.parent.mkdir(parents=True)
            path.write_text(json.dumps(payload), encoding="utf-8")
            loaded = load_application_lifecycle(directory)
            self.assertIsNotNone(loaded)
            self.assertEqual(loaded.active_executions, {})
            self.assertIsNone(loaded.initialization.thread_id)
            self.assertIsNone(loaded.active_formal_revision.continuation_token_sha256)
            self.assertEqual(loaded.active_formal_revision.remaining_artifacts, [])
            write_application_lifecycle(directory, loaded)
            reloaded = load_application_lifecycle(directory).model_dump(mode="json", by_alias=True)
            self.assertEqual(reloaded["displayHint"], payload["displayHint"])
            self.assertEqual(reloaded["application"], payload["application"])
            revision = reloaded["activeFormalRevision"]
            self.assertEqual(revision["pendingApplicationConfigChanges"], [])
            self.assertEqual(revision["pendingInitialAdministratorSubjects"], [])
            self.assertEqual(revision["target"]["displayHint"], "应用")
            self.assertEqual(reloaded["initialization"]["displayHint"], "就绪")

    def test_required_identity_and_invalid_json_still_fail(self) -> None:
        """宽松读取不凭空补造应用身份，也不吞掉文件损坏。"""

        with tempfile.TemporaryDirectory() as directory:
            path = application_lifecycle_path(directory)
            path.parent.mkdir(parents=True)
            payload = self.snapshot()
            del payload["application"]["id"]
            for content in (json.dumps(payload), "{broken", "[]"):
                path.write_text(content, encoding="utf-8")
                with self.assertRaises(ApplicationLifecycleCorruptedError):
                    load_application_lifecycle(directory)
