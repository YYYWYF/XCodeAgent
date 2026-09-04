"""T2.4 收尾：正式基线非法独立于上游缺项，且完整保留 AG-UI 恢复语义。"""

import asyncio
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.memory import InMemorySaver

from app.graph.state import ProjectState
from app.graph.nodes.tasks import _confirmed_baseline_blocked_result, prepare_build_tasks
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.definition import workflow_capabilities
from app.protocols.workflow.projection import (
    _public_workflow_state, _workflow_next_nodes, _workflow_node_detail, _workflow_summary,
)
from tests.test_build_task_reuse_workspace import _ready_template
from tests.test_frontend_shell_prerequisite import PLAN_PATH, _history, _state


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

    def test_ag_ui_stream_retains_baseline_error_through_run_finish(self) -> None:
        """真实节点经过 Graph 和 AG-UI adapter 后，完整生命周期仍携带专用基线错误。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ready_template(root)
            state = _state(root)
            (root / PLAN_PATH).write_text("{broken", encoding="utf-8")

            def prepare(_state: ProjectState) -> dict:
                """将已准备的正式工作区输入交给真实节点，仅隔离其他 Workflow 阶段。"""

                return prepare_build_tasks(state)

            builder = StateGraph(ProjectState)
            builder.add_node("prepare_build_tasks", prepare)
            builder.add_edge(START, "prepare_build_tasks")
            builder.add_edge("prepare_build_tasks", END)
            graph = builder.compile(checkpointer=InMemorySaver())

            async def collect() -> list[str]:
                """收集完整协议帧，验证最终快照与运行终止没有丢失错误身份。"""

                return [frame async for frame in build_workflow_ag_ui_stream(
                    graph=graph,
                    payload={
                        "threadId": "baseline-projection", "runId": "baseline-projection-run",
                        "messages": [{"role": "user", "content": "开始任务规划"}],
                        "workspaceRoot": directory, "resumeFrom": "prepare_build_tasks",
                    },
                )]

            with patch("app.graph.nodes.tasks.prepare_build_tasks_with_main_agent") as model:
                frames = asyncio.run(collect())
            model.assert_not_called()
            events = [json.loads(line[5:].strip()) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
            types = [event["type"] for event in events]
            self.assertNotIn("RUN_ERROR", types, [event for event in events if event["type"] == "RUN_ERROR"])
            for required in ("RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
                self.assertIn(required, types)
            snapshot = [event["snapshot"] for event in events if event["type"] == "STATE_SNAPSHOT"][-1]
            self.assertIn("confirmed_baseline_invalid", json.dumps(snapshot))
            self.assertIn("CONFIRMED_BASELINE_INVALID", json.dumps(snapshot))
            self.assertNotIn("build_prerequisite_not_ready", json.dumps(events))
            self.assertNotIn("还有 1 个问题需要补充", json.dumps(events, ensure_ascii=False))
            self.assertEqual(types[-1], "RUN_FINISHED")
            self.assertEqual((root / PLAN_PATH).read_text(encoding="utf-8"), "{broken")

    def test_unreadable_formal_plan_uses_baseline_error_without_writes(self) -> None:
        """读取权限失败与内容非法使用同一基线分类；不消费 checkpoint、不写回正式文件。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ready_template(root)
            state = _state(root)
            path = root / PLAN_PATH
            path.write_text(json.dumps(_history("completed")), encoding="utf-8")
            before = path.read_bytes()
            with patch("app.graph.nodes.tasks._existing_build_task_plan", side_effect=PermissionError("permission denied")), patch(
                "app.graph.nodes.tasks.prepare_build_tasks_with_main_agent",
            ) as model:
                result = prepare_build_tasks(state)
            model.assert_not_called()
            self.assertEqual(result["clarification"]["code"], "confirmed_baseline_invalid")
            self.assertIn("permission denied", str(result["clarification"]["errors"]))
            self.assertEqual(path.read_bytes(), before)

    def test_plain_confirmation_does_not_bypass_invalid_baseline(self) -> None:
        """普通确认回复不能恢复非法基线；下一次进入仍读取正式文件并阻断。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _ready_template(root)
            state = _state(root)
            (root / PLAN_PATH).write_text("{broken", encoding="utf-8")
            with patch("app.graph.nodes.tasks.prepare_build_tasks_with_main_agent") as model:
                first = prepare_build_tasks(state)
                repeated = prepare_build_tasks({**state, **first, "request": "已确认，继续"})
            model.assert_not_called()
            self.assertEqual(first["clarification"]["code"], "confirmed_baseline_invalid")
            self.assertEqual(repeated["clarification"]["code"], "confirmed_baseline_invalid")
            self.assertEqual((root / PLAN_PATH).read_text(encoding="utf-8"), "{broken")


if __name__ == "__main__":
    unittest.main()
