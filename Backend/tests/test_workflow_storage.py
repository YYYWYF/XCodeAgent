"""覆盖内容去重、真实 checkpoint 恢复和身份引用边界。"""

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from langgraph.graph import StateGraph

from app.domain.execution_recovery import DurableExecutionRecord, DurableExecutionStatus
from app.graph.state import ProjectState
from app.persistence.checkpoints import workflow_checkpointer, close_workflow_checkpointer_for_workspace
from app.persistence.execution_recovery import insert_execution
from app.protocols.workflow.presentation import workflow_presentation
from app.protocols.workflow.resume_source import read_resume_source, resume_reference
from app.workspace.content_artifacts import artifact_root, pack_materials, expand_materials, read_artifact
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.routes.workflow_artifacts import workflow_artifacts_router
from app.workspace.content_artifacts import put_artifact
from app.workspace.task_documents import build_task_plan_sha256, write_build_task_plan_json, load_build_task_plan_json
from app.protocols.workflow.runtime import build_workflow_ag_ui_stream
from app.protocols.workflow.request import workflow_run_inputs


def _waiting(state):
    """用确定节点产生待确认状态，不调用模型或修改正式文档。"""
    return {"status": "requires_user_input", "phase": "build"}


class WorkflowStorageTests(unittest.IsolatedAsyncioTestCase):
    """真实 SQLite 验证资料从磁盘恢复，不需要前端完整状态。"""

    async def asyncSetUp(self):
        """为每条用例隔离工作区和恢复数据库。"""
        self.directory = tempfile.TemporaryDirectory()
        self.workspace = self.directory.name

    async def asyncTearDown(self):
        """关闭连接后回收隔离工作区。"""
        await close_workflow_checkpointer_for_workspace(workspace=self.workspace)
        self.directory.cleanup()

    def test_repeated_materials_are_addressed_once_and_verified(self):
        """二十个任务的相同资料落盘一份，展开对象共享且损坏不能静默通过。"""
        document = {"endpoint_designs": "接口设计" * 50000}
        value = {"tasks": [{"id": str(index), "source_refs": document} for index in range(20)]}
        packed = pack_materials(self.workspace, value)
        self.assertLess(len(json.dumps(packed)), 10000)
        restored = expand_materials(self.workspace, packed)
        self.assertEqual(restored, value)
        self.assertIs(restored["tasks"][0]["source_refs"], restored["tasks"][19]["source_refs"])
        reference = packed["tasks"][0]["source_refs"]["$artifact"]
        with self.assertRaises(ValueError):
            read_artifact(self.workspace, reference, max_bytes=1)
        path = artifact_root(self.workspace) / (reference["artifactId"] + ".json")
        path.write_text('"corrupt"')
        with self.assertRaises(ValueError):
            expand_materials(self.workspace, packed)

    def test_public_view_has_single_state_and_artifact_references(self):
        """状态与结果合并且正式资料、diff 不再跟随事件和消息复制。"""
        document = {"text": "资料" * 20000}
        payload = workflow_presentation({"state": {"product_plan": document, "technical_plan_baseline": document},
            "result": {"product_plan": document, "tasks": [{"source_refs": document}]},
            "summary": {"status": "completed", "lifecycle": {"revision": 1}},
            "codeChanges": {"files": [{"path": "file", "diff": "+line"}]}}, workspace=self.workspace)
        self.assertNotIn("result", payload)
        self.assertNotIn("product_plan", payload["state"])
        self.assertNotIn("technical_plan_baseline", payload["state"])
        self.assertEqual(read_artifact(self.workspace, payload["state"]["artifactRefs"]["technical_plan_baseline"]), document)
        self.assertNotIn("lifecycle", payload["summary"])
        self.assertEqual(payload["state"]["lifecycle"], {"revision": 1})
        self.assertEqual(read_artifact(self.workspace, payload["state"]["artifactRefs"]["product_plan"]), document)
        self.assertEqual(read_artifact(self.workspace, payload["codeChanges"]["files"][0]["diffRef"]), "+line")
        self.assertLess(len(json.dumps(payload)), 2000)

    async def test_resume_uses_real_checkpoint_and_rejects_stale_run(self):
        """继续只给 Run/Thread，实际任务及隐藏源资料由后端 checkpoint 还原。"""
        saver = await workflow_checkpointer(workspace=self.workspace)
        builder = StateGraph(ProjectState)
        builder.add_node("waiting", _waiting)
        builder.set_entry_point("waiting")
        builder.set_finish_point("waiting")
        graph = builder.compile(checkpointer=saver)
        materials = {"endpoint_designs": "原始接口资料" * 10000}
        await graph.ainvoke({"workspace": self.workspace, "active_run_id": "run", "active_thread_id": "thread",
            "owner_session_id": "owner", "tasks": [{"id": "task", "source_refs": materials}]},
            {"configurable": {"thread_id": "thread"}})
        await insert_execution(DurableExecutionRecord(run_id="run", thread_id="thread", workspace=self.workspace,
            owner_session_id="owner", execution_kind="workbench", first_node="build", current_node="build",
            status=DurableExecutionStatus.AWAITING_USER, started_at=datetime.now(timezone.utc), updated_at=datetime.now(timezone.utc)))
        payload = {"forwardedProps": {"workspaceRoot": self.workspace, "sessionId": "owner",
            "resumeReference": {"runId": "run", "threadId": "thread"}}}
        _, restored = await read_resume_source(graph, payload, resume_reference(payload))
        self.assertEqual(restored["tasks"][0]["source_refs"], materials)
        self.assertNotIn("timeline", restored)
        await graph.aupdate_state({"configurable": {"thread_id": "thread"}}, {"active_run_id": "new-run"})
        with self.assertRaises(ValueError):
            await read_resume_source(graph, payload, resume_reference(payload))
        with self.assertRaises(ValueError):
            resume_reference({"forwardedProps": {"resumeState": {"tasks": []}}})

    async def test_client_snapshot_is_rejected_with_complete_ag_ui_lifecycle(self):
        """旧快照请求明确失败，不能进入 Graph，也不能被误判为断线。"""
        frames = [frame async for frame in build_workflow_ag_ui_stream(graph=object(), payload={
            "threadId": "thread", "runId": "run", "request": "继续", "forwardedProps": {"resumeState": {"tasks": []}}})]
        events = [json.loads(line[5:]) for frame in frames for line in frame.splitlines() if line.startswith("data:")]
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertIn("resumeReference", "".join(frames))

    def test_sdk_display_state_cannot_supply_execution_materials(self):
        """AG-UI 客户端自动发送的展示 state 不能覆盖后端任务或目标。"""
        source = {"status": "requires_user_input", "phase": "build", "selectedPageId": "server-page",
            "build_execution_scope": {"type": "page", "targetId": "server-page"}, "tasks": [{"id": "server-task"}]}
        inputs = workflow_run_inputs({"request": "继续", "state": {"tasks": [{"id": "injected"}]},
            "forwardedProps": {"selectedPageId": "other-page"}}, server_state=source)
        self.assertEqual(inputs["resume_values"]["tasks"], [{"id": "server-task"}])
        self.assertEqual(inputs["resume_values"]["selectedPageId"], "server-page")

    def test_material_and_non_material_fields_do_not_share_reference_wrapper(self):
        """同一对象出现在不同字段时，只有资料字段被外置。"""
        shared = {"text": "x" * 10000}
        packed = pack_materials(self.workspace, {"source_refs": shared, "display": shared})
        self.assertIn("$artifact", packed["source_refs"])
        self.assertIn("text", packed["display"])

    def test_dag_semantic_digest_is_stable_after_reference_storage(self):
        """正式 DAG 引用化不改变确认摘要，隐藏资料与验收输入仍完整还原。"""
        plan = {"task_registry": {"task": {"id": "task", "source_refs": {"endpoint_designs": "设计" * 10000},
            "acceptance_checks": [{"description": "验收" * 10000}]}}}
        digest = build_task_plan_sha256(plan)
        path = write_build_task_plan_json({"workspace": self.workspace}, plan)
        self.assertLess(Path(path).stat().st_size, 2000)
        restored = load_build_task_plan_json(path)
        self.assertEqual(restored, plan)
        self.assertEqual(build_task_plan_sha256(restored), digest)

    def test_ag_ui_artifact_read_has_complete_lifecycle_and_rejects_bad_references(self):
        """独立读取使用标准 AG-UI 生命周期；错误引用不会读取任意路径。"""
        app = FastAPI()
        app.include_router(workflow_artifacts_router)
        client = TestClient(app)
        reference = put_artifact(self.workspace, "# 文档")
        payload = {"threadId": "read", "runId": "read-run", "forwardedProps": {
            "artifact": {"workspaceRoot": self.workspace, "reference": reference}}}
        response = client.post("/workflow-artifacts/run", json=payload)
        events = [json.loads(line[5:]) for line in response.text.splitlines() if line.startswith("data:")]
        self.assertEqual(events[0]["type"], "RUN_STARTED")
        self.assertEqual(events[-1]["type"], "RUN_FINISHED")
        self.assertEqual(events[-1]["result"]["workflowArtifact"]["content"], "# 文档")
        payload["forwardedProps"]["artifact"]["reference"]["artifactId"] = "../secret"
        response = client.post("/workflow-artifacts/run", json=payload)
        self.assertIn('"status":"failed"', response.text)
        self.assertIn('"type":"RUN_FINISHED"', response.text)
