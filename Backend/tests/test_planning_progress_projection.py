"""验证技术规划子节点的 AG-UI 进度不会提前结束原加载卡。"""

import json
import unittest

from ag_ui.encoder import EventEncoder

from app.protocols.workflow.projection import (
    _workflow_next_nodes,
    _workflow_progress_summary,
    _workflow_summary,
)
from app.protocols.workflow.stream_events import _workflow_ag_ui_frames


class PlanningProgressProjectionTests(unittest.TestCase):
    """覆盖节点进度、确认门和终态，不依赖模型或实际工作区。"""

    def test_completed_node_keeps_running_workflow_in_both_ag_ui_frames(self):
        """子节点完成后业务仍运行时，自定义事件与快照都必须保留加载状态。"""
        for phase in ("technical_planning_begin", "technical_planning_generate"):
            with self.subTest(phase=phase):
                result = {"phase": phase, "status": "running"}
                events = [{
                    "type": "workflow.node.completed",
                    "nodeName": phase,
                    "node": {"id": phase},
                    "status": "completed",
                }]
                frames = list(_workflow_ag_ui_frames(
                    EventEncoder(), run_id="planning-run", thread_id="planning-thread",
                    events=events, result=result,
                ))
                decoded = [json.loads(line[5:].strip()) for frame in frames
                           for line in frame.splitlines() if line.startswith("data:")]
                self.assertEqual(len(decoded), 2)
                workflows = [decoded[0]["value"], decoded[1]["snapshot"]["workflow"]]
                for workflow in workflows:
                    self.assertEqual(workflow["summary"]["status"], "running")
                    self.assertEqual(workflow["state"]["status"], "running")
                    self.assertEqual(workflow["summary"]["phase"], phase)
                    self.assertEqual(workflow["events"][-1]["status"], "completed")
                    self.assertEqual(workflow["summary"]["completedNodeCount"], 1)

    def test_other_workflow_statuses_remain_unchanged(self):
        """失败、待确认、正常完成和下一节点开始的原投影保持不变。"""
        for event_type, event_status, result_status, expected in (
            ("workflow.node.completed", "failed", "failed", "failed"),
            ("workflow.node.completed", "requires_user_input", "requires_user_input", "requires_user_input"),
            ("workflow.node.completed", "completed", "completed", "completed"),
            ("workflow.node.started", "running", "completed", "running"),
        ):
            with self.subTest(event_status=event_status, result_status=result_status):
                summary = _workflow_progress_summary(
                    {"phase": "unit_test", "status": result_status},
                    [{"type": event_type, "status": event_status, "node": {"id": "unit_test"}}],
                )
                self.assertEqual(summary["status"], expected)
        self.assertEqual(_workflow_summary({"status": "completed"}, [])["status"], "completed")

    def test_technical_progress_follows_current_graph_successors(self):
        """进度只投射真实后继，确认完成不能重新显示生成卡或越过审阅门。"""
        cases = (
            ("planning_stage_entry", {}, ["technical_planning_begin"]),
            ("technical_planning_begin", {"status": "running"}, ["technical_planning_generate"]),
            ("technical_planning_generate", {"technical_plan_candidate": {"id": "candidate"}}, ["technical_planning_commit"]),
            ("technical_planning_generate", {"status": "requires_user_input", "technical_plan_candidate": {}}, ["technical_planning_review"]),
            ("technical_planning_commit", {"status": "requires_user_input"}, ["technical_planning_review"]),
            ("technical_planning_confirm", {"status": "completed"}, []),
            ("technical_planning_confirm", {"template_reconcile_pending": True}, ["template_reconcile"]),
            ("technical_planning_confirm", {"application_planning_review_route": "technical_planning_begin"}, ["technical_planning_begin"]),
        )
        for node, update, expected in cases:
            with self.subTest(node=node, update=update):
                self.assertEqual(_workflow_next_nodes(node, update), expected)

