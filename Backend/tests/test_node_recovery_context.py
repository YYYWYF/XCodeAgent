"""验证 Native Recovery 来源只对指定 child 的首次目标节点调用可见。"""

from __future__ import annotations

import unittest

from app.domain.execution_recovery import WorkflowReentryReason
from app.services.node_recovery_context import (
    NodeRecoveryContext,
    bind_node_recovery,
    bind_recovery_runtime,
    current_node_recovery_context,
)


class NodeRecoveryContextTests(unittest.TestCase):
    """覆盖跨节点、跨 execution 和再次进入同名节点的隔离边界。"""

    def test_source_is_visible_once_only_to_matching_child_node(self) -> None:
        """同名节点在后续循环中也不能再次领取原 source。"""

        context = NodeRecoveryContext(
            source_run_id="source-r1",
            execution_run_id="child-r2",
            thread_id="thread-a",
            target_node="prepare_build_tasks",
            checkpoint_id="entry-a",
            reentry_reason=WorkflowReentryReason.BUSINESS_RETRY,
        )
        state = {"active_run_id": "child-r2", "active_thread_id": "thread-a"}
        with bind_recovery_runtime(context):
            with bind_node_recovery(state, "build"):
                self.assertIsNone(current_node_recovery_context())
            with bind_node_recovery(
                {"active_run_id": "different-child", "active_thread_id": "thread-a"},
                "prepare_build_tasks",
            ):
                self.assertIsNone(current_node_recovery_context())
            with bind_node_recovery(state, "prepare_build_tasks"):
                self.assertIs(current_node_recovery_context(), context)
            self.assertIsNone(current_node_recovery_context())
            with bind_node_recovery(state, "prepare_build_tasks"):
                self.assertIsNone(current_node_recovery_context())
        self.assertIsNone(current_node_recovery_context())
