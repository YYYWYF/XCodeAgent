from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace

from app.protocols.workflow import build_workflow_ag_ui_stream
from app.protocols.workflow.projection import _workflow_event, _workflow_event_state


class WorkflowEventProjectionTests(unittest.TestCase):
    """以重复大状态和真实 AG-UI 编码验证事件瘦身，不使用用户会话或外部模型。"""

    def test_large_state_is_never_embedded_in_event_and_input_is_unchanged(self) -> None:
        """十三个事件保留顺序、轮次与失败证据，但不携带重复任务材料或整份计划。"""

        document = {"endpoint_designs": [{"mapping": "x" * 200_000}], "mapping_flows": ["flow"]}
        tasks = [{"id": f"task-{index}", "status": "failed", "source_refs": document,
                  "acceptance_checks": [{"context": "y" * 10_000}],
                  "businessAcceptanceChecks": [{"id": "visible", "description": "显示业务验收标准"}]}
                 for index in range(20)]
        state = {"phase": "build", "status": "failed", "selectedPageId": "home", "tasks": tasks,
                 "build_task_plan": {"task_graph": {"tasks": tasks}}, "timeline": ["build"]}
        before_bytes = len(json.dumps(state).encode()) * 13
        events = []
        for attempt in range(1, 14):
            _workflow_event(events, "workflow.node.completed", run_id="run", thread_id="thread",
                            node_name="build", status="failed", attempt=attempt,
                            message="构建失败", data={"stateDelta": _workflow_event_state(state),
                            "detail": {"buildTaskPlan": state["build_task_plan"],
                                       "buildExecutionSlice": {"tasks": tasks},
                                       "buildResults": tasks, "error": "模型请求失败"}})
        after_bytes = len(json.dumps(events, ensure_ascii=False).encode())
        self.assertLess(after_bytes, 100_000)
        self.assertLess(after_bytes, before_bytes // 100)
        self.assertEqual(len(events), 13)
        self.assertEqual(events[-1]["attempt"], 13)
        self.assertEqual(events[-1]["data"]["detail"]["error"], "模型请求失败")
        self.assertNotIn("stateDelta", events[-1]["data"])
        self.assertEqual(tasks[0]["source_refs"], document)
        self.assertEqual(events[-1]["data"]["detail"]["buildResultsCount"], 20)
        self.assertIn("businessAcceptanceChecks", events[-1]["data"]["detail"]["buildExecutionSlice"]["tasks"][0])
        print(f"event projection fixture: {before_bytes} -> {after_bytes} bytes (13 events)")
        _workflow_event(events, "workflow.run.finished", run_id="run", thread_id="thread",
                        data={"summary": {"status": "failed", "buildTaskPlan": state["build_task_plan"],
                              "failureDiagnostic": {"code": "MODEL_FAILED", "message": "请求失败"}}})
        self.assertNotIn("buildTaskPlan", events[-1]["data"]["summary"])
        self.assertEqual(events[-1]["data"]["summary"]["failureDiagnostic"]["code"], "MODEL_FAILED")

    def test_only_changed_fields_are_recorded_and_snapshots_are_frozen(self) -> None:
        """显式清空字段仍记录 null；后续原地修改不能倒写此前事件。"""

        events = []
        context = {"phase": "build", "selectedPageId": "home", "timeline": ["inspect_workspace:cache_hit"]}
        for changed in (context, {**context, "phase": "unit_test"}, {"selectedPageId": None}):
            _workflow_event(events, "workflow.node.completed", run_id="run", thread_id="thread", data={"stateDelta": changed})
        context["timeline"].append("later")
        self.assertEqual(events[1]["data"]["stateDelta"], {"phase": "unit_test"})
        self.assertEqual(events[2]["data"]["stateDelta"], {"selectedPageId": None})
        self.assertEqual(events[0]["data"]["stateDelta"]["timeline"], ["inspect_workspace:cache_hit"])
        _workflow_event(events, "workflow.node.completed", run_id="another-run", thread_id="thread",
                        data={"stateDelta": {"selectedPageId": "home"}})
        self.assertEqual(events[-1]["data"]["stateDelta"], {"selectedPageId": "home"})

    def test_real_ag_ui_stream_keeps_lifecycle_and_compact_events(self) -> None:
        """真实 Runtime 保留起止帧、工作流快照与节点事件，不用事件日志承担执行恢复。"""

        class Graph:
            """提供大节点结果的受控 Graph，不执行模型或写入业务工作区。"""

            async def astream(self, initial_state, *, config, stream_mode):
                """发布两个包含相同大任务材料的节点结果。"""
                for index in range(2):
                    yield "updates", {"build": {"phase": "build", "status": "completed", "selectedPageId": "home",
                        "tasks": [{"id": "task", "source_refs": {"endpoint_designs": "x" * 200_000}}],
                        "build_summary": {"completed": index + 1, "failed": 0}}}

            async def aget_state(self, config):
                """终态读取保持最小 checkpoint 接口。"""
                return SimpleNamespace(values={"phase": "completed", "status": "completed"}, next=())

        async def collect():
            """收集标准 AG-UI 帧，验证流传输而不构造替代产品协议。"""
            return [frame async for frame in build_workflow_ag_ui_stream(graph=Graph(), payload={
                "runId": "thin-event-run", "threadId": "thin-event-thread", "messages": [{"role": "user", "content": "受控验证"}]
            })]

        frames = [json.loads(line[5:].strip()) for frame in asyncio.run(collect())
                  for line in frame.splitlines() if line.startswith("data:")]
        self.assertEqual(frames[0]["type"], "RUN_STARTED")
        self.assertEqual(frames[-1]["type"], "RUN_FINISHED")
        payloads = [frame["value"] for frame in frames if frame.get("name") == "workflow-run"]
        self.assertTrue(payloads)
        events = payloads[-1]["events"]
        completed = [event for event in events if event["type"] == "workflow.node.completed"]
        self.assertEqual(len(completed), 2)
        for event in completed:
            self.assertNotIn("tasks", event["data"].get("stateDelta", {}))
        self.assertLess(len(json.dumps(events)), 10_000)
