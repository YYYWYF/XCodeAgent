"""通过真实 Graph checkpoint 和 AG-UI 终帧验证开发、实体设计、续接链路。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

from app.graph.nodes.development_readiness import development_readiness_gate
from app.graph.nodes.planning import entity_source_binding
from app.graph.state import ProjectState
from app.graph.workflow import route_workflow_start
from app.protocols.workflow import build_workflow_ag_ui_stream
from app.services.application_lifecycle import load_application_lifecycle
from tests.entity_design_test_utils import confirm_entity_designs
from tests.test_development_continuation import (
    _prepare_source_execution,
    _technical_plan,
    _write_technical_plan,
)


class DevelopmentContinuationStreamTests(unittest.IsolatedAsyncioTestCase):
    """避免只测试服务函数而漏掉终帧覆盖、跨轮确认及多实体串联。"""

    async def test_page_and_endpoint_continue_only_after_all_entities_confirmed(self) -> None:
        """页面和接口都必须保留原目标，逐个确认实体后经显式续接重跑门禁。"""

        for target_type in ("page", "endpoint"):
            with self.subTest(target_type=target_type), tempfile.TemporaryDirectory() as raw:
                workspace = Path(raw)
                plan = _technical_plan()
                plan["entities"].append({"id": "Customer", "name": "客户", "fields": []})
                plan["api_contracts"][0]["entity_ids"].append("Customer")
                _write_technical_plan(workspace, plan)
                _prepare_source_execution(workspace)
                inspections = []

                def inspect_target(state):
                    """记录实际到达扫描的目标，在模型任务生成前结束测试。"""
                    inspections.append(dict(state))
                    return {"phase": "inspect_workspace", "status": "completed", "clarification": {}}

                def after_gate(state):
                    """模拟主图的门禁分支，未就绪时绝不进入扫描。"""
                    return "inspect_workspace" if state["status"] == "completed" else END

                def design_entity(state):
                    """用确定性产物代替模型设计，保留真实确认轮次和磁盘读取。"""
                    entity_id = state["selected_entity_id"]
                    if state.get("entity_source_binding_submission"):
                        saved = confirm_entity_designs(
                            state["project_plan"], source_type="static", entity_ids=[entity_id]
                        )
                        _write_technical_plan(workspace, saved)
                        return {"status": "completed", "project_plan": saved, "clarification": {}}
                    return {
                        "status": "requires_user_input",
                        "clarification": {"mode": "entity_source_binding", "status": "requires_user_input"},
                    }

                def load_plan(*args, **kwargs):
                    """每轮读取真实文件，防止使用测试初始快照掩盖实体确认结果。"""
                    return {"project_plan": json.loads(
                        (workspace / ".xcodeagent/plans/technical-plan.json").read_text()
                    )}

                builder = StateGraph(ProjectState)
                builder.add_node("development_readiness_gate", development_readiness_gate)
                builder.add_node("entity_source_binding", entity_source_binding)
                builder.add_node("inspect_workspace", inspect_target)
                builder.add_conditional_edges(START, route_workflow_start, {
                    "development_readiness_gate": "development_readiness_gate",
                    "entity_source_binding": "entity_source_binding",
                })
                builder.add_conditional_edges("development_readiness_gate", after_gate)
                builder.add_edge("entity_source_binding", END)
                builder.add_edge("inspect_workspace", END)
                graph = builder.compile(checkpointer=InMemorySaver())

                async def run(thread, run_id, props):
                    """消费完整 AG-UI 流，断言所有最终公开载荷保持一致。"""
                    frames = [frame async for frame in build_workflow_ag_ui_stream(
                        graph=graph,
                        payload={
                            "threadId": thread,
                            "runId": run_id,
                            "messages": [{"role": "user", "content": "开始开发订单目标"}],
                            "forwardedProps": {"workspaceRoot": str(workspace), **props},
                        },
                    )]
                    events = [json.loads(line[5:]) for frame in frames
                              for line in frame.splitlines() if line.startswith("data:")]
                    self.assertFalse([event for event in events if event["type"] == "RUN_ERROR"], events[-3:])
                    final = next(event["result"] for event in reversed(events) if event["type"] == "RUN_FINISHED")
                    workflow = final["workflow"]
                    continuation = workflow["summary"].get("developmentContinuation")
                    if continuation:
                        self.assertEqual(workflow["state"]["developmentContinuation"], continuation)
                        self.assertEqual(final["result"]["developmentContinuation"], continuation)
                    return workflow

                with patch("app.protocols.workflow.request._project_plan_start_values", side_effect=load_plan), patch(
                    "app.graph.nodes.planning._entity_source_binding_implementation", side_effect=design_entity
                ):
                    target = {"selectedPageId": "orders_page", "detailTargetType": "page"} if target_type == "page" else {
                        "selectedApiContractId": "orders-api", "selectedEndpointId": "orders.list", "detailTargetType": "endpoint",
                    }
                    gate = await run("source-thread", "source-run", target)
                    continuation = gate["summary"].get("developmentContinuation")
                    self.assertIsNotNone(continuation, "门禁终帧必须带有可点击的续接合同")
                    self.assertEqual(continuation["remainingEntityIds"], ["Order", "Customer"])
                    self.assertEqual(inspections, [])

                    for entity_id in ("Order", "Customer"):
                        thread = f"entity-{entity_id}"
                        binding = await run(thread, f"start-{entity_id}", {
                            "workflowAction": "start_entity_binding",
                            "developmentContinuation": {"id": continuation["id"]},
                            "selectedEntityId": entity_id,
                            "detailTargetType": "entity",
                            "buildExecutionScope": {"type": "data_source", "targetId": entity_id},
                        })
                        # 确认请求不回传内部 continuation ID，只由原实体 thread checkpoint 保留。
                        completed = await run(thread, f"confirm-{entity_id}", {
                            "resumeState": binding,
                            "clarificationAnswers": {"entity_source_binding": {"review_status": "confirmed"}},
                        })
                        continuation = completed["summary"].get("developmentContinuation")
                        self.assertIsNotNone(continuation, "实体确认终帧必须重新投影原开发意图")
                        self.assertEqual(inspections, [], "确认实体不能自动启动页面或接口开发")
                        if entity_id == "Order":
                            self.assertEqual(continuation["status"], "awaiting_entity_binding")
                            self.assertEqual(continuation["remainingEntityIds"], ["Customer"])
                            self.assertNotIn("token", continuation)

                    self.assertEqual(continuation["status"], "ready")
                    self.assertEqual(continuation["remainingEntityIds"], [])
                    self.assertEqual(continuation["sourceThreadId"], "source-thread")
                    self.assertEqual(continuation["sourceRunId"], "source-run")
                    await run("source-thread", "continued-run", {
                        "workflowAction": "continue_after_entity_binding",
                        "developmentContinuation": {"id": continuation["id"], "token": continuation["token"]},
                    })
                    self.assertEqual(len(inspections), 1)
                    self.assertEqual(inspections[0]["detail_target_type"], target_type)
                    self.assertFalse(inspections[0].get("selected_entity_id"))
                    lifecycle = load_application_lifecycle(workspace)
                    self.assertNotIn("source-run", lifecycle.active_executions)
                    self.assertEqual(lifecycle.development_continuations[continuation["id"]].status, "consumed")
