"""恢复读取、报告重试路由及重复 Diff 统计回归。"""
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from app.domain.execution_recovery import DurableExecutionStatus
from app.services.checkpoint_execution import read_checkpoint_execution, CheckpointExecution
from app.services.business_node_retry import _execution_retry_hint
from app.workspace.code_changes import build_code_change_set, merge_code_change_sets


class RecoveryUiBlockerTests(IsolatedAsyncioTestCase):
    """不运行业务节点，验证实际恢复入口和展示统计。"""

    async def test_history_query_is_bounded_before_loading(self):
        """最新失败收尾无节点时，只查询有限现场以定位同 Run 业务节点。"""
        source = SimpleNamespace(run_id="run", thread_id="thread", status=DurableExecutionStatus.FAILED,
                                 execution_kind="workbench", workflow_scope="application", workspace="/workspace")
        snapshot = SimpleNamespace(values={"active_run_id": "run", "status": "failed", "phase": "failed"},
                                   config={}, tasks=(), next=())
        previous = SimpleNamespace(values={"active_run_id": "run", "status": "failed", "phase": "test_phase_confirmation"},
                                   config={}, tasks=(), next=())
        calls = []

        async def get_state(config):
            """返回最近失败收尾现场。"""
            return snapshot

        async def history(config, *, limit):
            """在数据库读取之前检查调用方已设置查询上限。"""
            calls.append(limit)
            yield snapshot
            yield previous

        graph = SimpleNamespace(nodes={"test_phase_confirmation": None}, aget_state=get_state, aget_state_history=history)
        with patch("app.services.checkpoint_execution.execution_is_live", return_value=False):
            result = await read_checkpoint_execution(source, graph)
        self.assertEqual(result.node, "test_phase_confirmation")
        self.assertEqual(calls, [8])

    def test_invalid_report_retry_returns_to_unit_test(self):
        """报告已失效时重试单测，正常确认入口和中断续接不改写。"""
        source = SimpleNamespace(execution_kind="workbench", workflow_scope="application", status=DurableExecutionStatus.FAILED)
        snapshot = SimpleNamespace(values={"unit_test_gate_passed": False, "unit_test_next_action": "test_phase_confirmation"})
        execution = CheckpointExecution(snapshot, DurableExecutionStatus.FAILED, "test_phase_confirmation", "原请求")
        graph = SimpleNamespace(nodes={"unit_test": None, "test_phase_confirmation": None})
        self.assertEqual(_execution_retry_hint(execution, graph, source).node, "unit_test")
        snapshot.values["unit_test_gate_passed"] = True
        self.assertEqual(_execution_retry_hint(execution, graph, source).node, "test_phase_confirmation")

    def test_nested_diff_summaries_do_not_multiply_line_counts(self):
        """重复叶子 Diff 只统计一次，同文件的不同真实修改仍保留。"""
        with TemporaryDirectory() as root:
            first = build_code_change_set(workspace_root=root, source_tool="build",
                files=[{"id": "first", "path": "agent-runtime/src/app/a.py", "additions": 10, "deletions": 2}])
            second = build_code_change_set(workspace_root=root, source_tool="repair",
                files=[{"id": "second", "path": "agent-runtime/src/app/a.py", "additions": 3, "deletions": 1}])
            merged = merge_code_change_sets([first, second])
            repeated = merge_code_change_sets([first, merged, second, merged])
        self.assertEqual(repeated["summary"]["additions"], 13)
        self.assertEqual(repeated["summary"]["deletions"], 3)
        self.assertEqual(len(repeated["files"]), 2)
