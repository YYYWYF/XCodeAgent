"""验证当前 Template Engine Reconcile 协议的本地消费模型。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from pydantic import ValidationError

from app.services.template_reconcile.digests import artifact_file_sha256
from app.services.template_reconcile.models import ChangeSetBody, PlanResponse, TemplateState


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "template_reconcile"


def _fixture(relative_path: str) -> object:
    """读取并解析固定的当前 Engine JSON fixture。"""

    return json.loads((FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"))


class TemplateReconcileModelTests(unittest.TestCase):
    """验证未知字段与未实现 Operation 不会被本地消费者放行。"""

    def test_accepts_current_engine_template_state(self) -> None:
        """确认四字段 TemplateState 可按当前 OpenAPI 消费。"""

        state = TemplateState.model_validate(_fixture("current-template-state.json"))
        self.assertEqual(state.templateRevision, "2026.09.04.1")
        self.assertIn("frontend/src/App.tsx", state.managedFiles)

    def test_preserves_current_file_operation_order(self) -> None:
        """确认 ChangeSet 不重排 Engine 给定的三类文件操作。"""

        change_set = ChangeSetBody.model_validate(
            _fixture("update-package/change-set.json")
        )
        self.assertEqual(
            [operation.type for operation in change_set.operations],
            ["DELETE_FILE", "ADD_FILE", "UPDATE_FILE"],
        )

    def test_accepts_no_change_plan_response(self) -> None:
        """确认 NO_CHANGE 响应保持 null body。"""

        response = PlanResponse.model_validate(_fixture("no-change-plan-response.json"))
        self.assertEqual(response.kind, "NO_CHANGE")
        self.assertIsNone(response.body)

    def test_rejects_unknown_state_field_and_unsupported_operation(self) -> None:
        """确认当前 Engine 未定义字段和结构化操作均 fail closed。"""

        with self.assertRaises(ValidationError):
            TemplateState.model_validate(_fixture("invalid-template-state-extra-field.json"))
        with self.assertRaises(ValidationError):
            ChangeSetBody.model_validate(
                {
                    "operations": [
                        {
                            "type": "UPSERT_JSON_NODE",
                            "path": "frontend/package.json",
                            "pointer": "/dependencies/demo",
                            "value": "1.0.0",
                        }
                    ]
                }
            )

    def test_rejects_missing_file_content_for_add_and_update(self) -> None:
        """确认 ADD_FILE 和 UPDATE_FILE 不接受缺少 content 的 Engine 输出。"""

        for operation_type in ("ADD_FILE", "UPDATE_FILE"):
            with self.subTest(operation_type=operation_type), self.assertRaises(
                ValidationError
            ):
                ChangeSetBody.model_validate(
                    {
                        "operations": [
                            {"type": operation_type, "path": "frontend/src/App.tsx"}
                        ]
                    }
                )

    def test_hashes_original_file_bytes(self) -> None:
        """确认文件摘要直接绑定 bytes，不对内容作 JSON 规范化。"""

        with tempfile.TemporaryDirectory() as directory:
            artifact = Path(directory) / "artifact.json"
            artifact.write_bytes(b'{"key": 1}\n')
            self.assertEqual(
                artifact_file_sha256(artifact),
                "67c40a161889b2de243cab979e3f5b7d08df73ba5c6c4ab271f6d4b173f0964e",
            )
