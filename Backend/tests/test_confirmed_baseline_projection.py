"""T2.4 收尾：正式基线非法独立于上游缺项，且完整保留 AG-UI 恢复语义。"""

import json
import unittest

from app.graph.nodes.tasks import _confirmed_baseline_blocked_result
from app.protocols.workflow.definition import workflow_capabilities
from app.protocols.workflow.projection import (
    _public_workflow_state, _workflow_next_nodes, _workflow_node_detail, _workflow_summary,
)


PLAN_PATH = ".xcodeagent/plans/build-task-plan.json"


class ConfirmedBaselineProjectionTests(unittest.TestCase):
    def test_error_identity_and_manual_recovery_are_baseline_specific(self) -> None:
        """分类、产物、问题级别与恢复动作全部指向正式 DAG，不引导重做上游。"""

        result = _confirmed_baseline_blocked_result({}, {"type": "page", "targetId": "orders"}, ["invalid DAG"])
        payload = result["clarification"]
        self.assertEqual(result["status"], "requires_user_input")
        self.assertFalse(result["build_task_plan_persisted"])
        self.assertEqual(payload["mode"], "confirmed_baseline_error")
        self.assertEqual(payload["code"], "confirmed_baseline_invalid")
        self.assertEqual(payload["artifact"], PLAN_PATH)
        self.assertFalse(payload["automatic_routing"])
        self.assertFalse(payload["retryable"])
        self.assertNotIn("upstreamStages", payload)
        self.assertIn("平台维护者", payload["recommended_action"])
        self.assertIn("验证为合法 ConfirmedPlan", payload["recommended_action"])
        issue = payload["issues"][0]
        self.assertEqual((issue["code"], issue["level"], issue["category"]),
                         ("CONFIRMED_BASELINE_INVALID", "pre_generation", "platform"))
        self.assertFalse(issue["retryable"])
        self.assertEqual(issue["retry_unit_ids"], [])
        self.assertEqual(issue["details"], {"artifact": PLAN_PATH, "errors": ["invalid DAG"]})
        for misleading in ("EntitySourceBinding", "模板初始化", "返回上游", "TechnicalPlan"):
            self.assertNotIn(misleading, json.dumps(payload, ensure_ascii=False))

    def test_node_summary_and_public_snapshot_keep_independent_projection(self) -> None:
        """节点事件、最终摘要及公开快照保持同一错误身份，不退化成补充问题或上游缺项。"""

        result = _confirmed_baseline_blocked_result({}, {"type": "application", "targetId": "application"}, ["failed baseline"])
        detail = _workflow_node_detail("prepare_build_tasks", result)
        summary = _workflow_summary(result, [])
        public = _public_workflow_state(result)
        self.assertEqual(detail["data"]["clarification"], result["clarification"])
        self.assertEqual(public["clarification"], result["clarification"])
        self.assertTrue(detail["data"]["requiresUserInput"])
        self.assertFalse(detail["data"]["buildTaskPlanPersisted"])
        self.assertEqual(summary["message"], detail["message"])
        for message in (summary["message"], detail["message"]):
            self.assertIn(PLAN_PATH, message)
            self.assertIn("平台维护者", message)
            self.assertNotIn("补充", message)
            self.assertNotIn("上游", message)
        self.assertEqual(_workflow_next_nodes("prepare_build_tasks", result), [])

    def test_health_metadata_describes_nonretryable_baseline_error(self) -> None:
        """公开能力元数据与真实错误投影一致，不声明自动修复或确认豁免入口。"""

        metadata = workflow_capabilities()["clarificationModes"]["confirmed_baseline_error"]
        self.assertEqual(metadata["code"], "confirmed_baseline_invalid")
        self.assertEqual(metadata["artifact"], PLAN_PATH)
        self.assertEqual(metadata["issueCode"], "CONFIRMED_BASELINE_INVALID")
        self.assertFalse(metadata["retryable"])
        self.assertFalse(metadata["automaticRouting"])
        self.assertNotIn("answerField", metadata)



if __name__ == "__main__":
    unittest.main()
