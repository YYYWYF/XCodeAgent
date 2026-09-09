"""验证当前 Engine 文件 Operation 的 Reconcile 所有权预检。"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from app.services.template_reconcile.models import ChangeSetBody, TemplateState
from app.services.template_reconcile.preflight import (
    OwnershipKind,
    ReconcileOwnershipRegistry,
    TemplateOperationOwnershipError,
)


FIXTURE_ROOT = Path(__file__).with_name("fixtures") / "template_reconcile"


def _fixture(relative_path: str) -> object:
    """读取当前 Engine 协议 fixture。"""

    return json.loads((FIXTURE_ROOT / relative_path).read_text(encoding="utf-8"))


class TemplateReconcilePreflightTests(unittest.TestCase):
    """验证 Current/Next managedFiles 是唯一所有权输入。"""

    def test_accepts_current_engine_file_operations_in_original_order(self) -> None:
        """确认 fixture 的 DELETE、ADD、UPDATE 可按原顺序通过预检。"""

        registry = ReconcileOwnershipRegistry(
            TemplateState.model_validate(_fixture("current-template-state.json")),
            TemplateState.model_validate(
                _fixture("update-package/next-template-state.json")
            ),
        )
        classified = registry.classify_change_set(
            ChangeSetBody.model_validate(_fixture("update-package/change-set.json"))
        )
        self.assertEqual([item.operation.type for item in classified], ["DELETE_FILE", "ADD_FILE", "UPDATE_FILE"])
        self.assertEqual({item.ownership for item in classified}, {OwnershipKind.ENGINE_EXCLUSIVE})

    def test_classifies_unmanaged_path_as_business_agent(self) -> None:
        """确认未被任一 State 声明的路径不会被误标为模板受管。"""

        registry = ReconcileOwnershipRegistry(
            TemplateState.model_validate(_fixture("current-template-state.json")),
            TemplateState.model_validate(
                _fixture("update-package/next-template-state.json")
            ),
        )
        self.assertEqual(
            registry.classify_path("frontend/src/business-feature.ts"),
            OwnershipKind.BUSINESS_AGENT,
        )

    def test_rejects_operation_touching_business_path(self) -> None:
        """确认 Engine ChangeSet 不能通过 Reconcile 修改业务路径。"""

        registry = ReconcileOwnershipRegistry(
            TemplateState.model_validate(_fixture("current-template-state.json")),
            TemplateState.model_validate(
                _fixture("update-package/next-template-state.json")
            ),
        )
        change_set = ChangeSetBody.model_validate(
            {
                "operations": [
                    {
                        "type": "UPDATE_FILE",
                        "path": "frontend/src/business-feature.ts",
                        "content": "export {};\n",
                    }
                ]
            }
        )
        with self.assertRaisesRegex(
            TemplateOperationOwnershipError,
            "TEMPLATE_OPERATION_OWNERSHIP_CONFLICT",
        ):
            registry.classify_change_set(change_set)

    def test_rejects_invalid_current_next_state_transition(self) -> None:
        """确认 DELETE 仍存在于 Next State 时会在 Apply 前失败。"""

        current = TemplateState.model_validate(_fixture("current-template-state.json"))
        next_state = TemplateState.model_validate(_fixture("current-template-state.json"))
        registry = ReconcileOwnershipRegistry(current, next_state)
        change_set = ChangeSetBody.model_validate(
            {
                "operations": [
                    {"type": "DELETE_FILE", "path": "frontend/src/obsolete.ts"}
                ]
            }
        )
        with self.assertRaises(TemplateOperationOwnershipError):
            registry.classify_change_set(change_set)
