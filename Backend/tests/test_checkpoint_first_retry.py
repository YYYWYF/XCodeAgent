"""验证一次 checkpoint 恢复及自动降级始终锚定原始业务失败。"""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import openai
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import START, END

from app.graph.state import ProjectState
from app.services.stage_artifacts import ArtifactStateGraph
from app.services.retry_origin import RetryOrigin, load_retry_origin, save_retry_origin
from app.services.business_node_retry import read_retry_hint
from app.services.checkpoint_first_retry import CheckpointFirstRetryGraph, prepare_checkpoint_first_retry
from tests import test_business_node_retry as retry_fixtures


class CheckpointFirstRetryTests(unittest.IsolatedAsyncioTestCase):
    """使用真实 LangGraph checkpoint 检查恢复、降级与取消路径。"""

    async def test_checkpoint_fork_keeps_current_worker_metadata(self):
        """checkpoint 位置沿 fork，模型和工具取消身份沿本轮 Runtime 元数据。"""
        from langchain_core.runnables.config import ensure_config
        observed = []

        def behavior(count, state):
            """构造来源失败，再观察恢复节点实际收到的运行元数据。"""
            if count == 1:
                raise RuntimeError("source failed")
            observed.append(ensure_config()["metadata"]["run_id"])
            return {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            update = graph.aupdate_state

            async def fork_with_source_metadata(*args, **kwargs):
                """保留真实 fork，并模拟来源配置携带旧的运行追踪元数据。"""
                fork = await update(*args, **kwargs)
                return {**fork, "metadata": {"run_id": "source", "workspace": root}}

            options["config"]["metadata"] = {"run_id": "child", "workspace": root}
            with patch.object(graph, "aupdate_state", side_effect=fork_with_source_metadata):
                _ = [item async for item in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options)]
            self.assertEqual(observed, ["child"])

    async def test_build_fallback_archives_only_failed_checkpoint_attempt_binding(self):
        """同一 AG-UI child 可在恢复失败后重新绑定，来源进度和不可变计划仍保留。"""
        from app.workspace.task_documents import build_execution_record_path, write_build_execution_record

        def behavior(count, state):
            """恢复先写 child 绑定再失败，业务降级必须能建立自己的绑定。"""
            if count == 1:
                raise RuntimeError("original interrupted Build")
            if count == 2:
                write_build_execution_record(state, {"execution_run_id": "child", "build_run_id": "native-attempt"})
                raise RuntimeError("checkpoint Build failed after binding")
            self.assertFalse(build_execution_record_path(state, "child").exists())
            write_build_execution_record(state, {"execution_run_id": "child", "build_run_id": "business-attempt"})
            return {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            write_build_execution_record(state, {"execution_run_id": "source", "build_run_id": "original"})
            source_path = build_execution_record_path(state, "source")
            source_bytes = source_path.read_bytes()
            immutable_plan = source_path.parent / ("build-" + "a" * 32 + ".json")
            immutable_plan.write_text('{"immutable": true}')
            chunks = [chunk async for chunk in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options)]
            child_path = build_execution_record_path(state, "child")
            archived = list(child_path.parent.glob("checkpoint-attempts/*/" + child_path.name))
            self.assertEqual(len(archived), 1)
            self.assertEqual(json.loads(archived[0].read_text())["build_run_id"], "native-attempt")
            self.assertEqual(json.loads(child_path.read_text())["build_run_id"], "business-attempt")
            self.assertEqual(source_path.read_bytes(), source_bytes)
            self.assertEqual(immutable_plan.read_text(), '{"immutable": true}')
            self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
            self.assertNotIn("checkpoint Build failed", str(chunks))

    async def fixture(self, workspace, behavior):
        """创建失败在 Build 的真实快照，保留一个仅 checkpoint 拥有的旧状态字段。"""

        received = []

        def build(state):
            """按测试指定的次数失败或完成，并记录当前实际节点输入。"""
            received.append(dict(state))
            return behavior(len(received), state)

        builder = ArtifactStateGraph(ProjectState)
        builder.add_node("build", build)
        builder.add_edge(START, "build")
        builder.add_edge("build", END)
        graph = builder.compile(checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "thread"}}
        with self.assertRaises(RuntimeError):
            await graph.ainvoke({"active_run_id": "source", "request": "原始问题 ORIGINAL",
                                 "workspace": workspace, "clarification": {"checkpointOnly": True}}, config)
        snapshot = await graph.aget_state(config)
        origin = RetryOrigin(thread_id="thread", source_run_id="source", node="build",
                             request="原始问题 ORIGINAL", checkpoint_config=snapshot.config,
                             checkpoint_attempted=True)
        context = SimpleNamespace(workspace=workspace, new_run_id="child", recovery_plan=SimpleNamespace(target_node="build"))
        state = {"workspace": workspace, "active_run_id": "child", "request": "原始问题 ORIGINAL", "resume_from": "build"}
        options = {"config": {"configurable": {"thread_id": "thread", "checkpoint_id": "fresh-child"}},
                   "stream_mode": ["updates", "custom"]}
        return graph, origin, context, state, options, received

    async def test_checkpoint_success_preserves_state_and_reads_latest_disk(self):
        """恢复成功只执行一次，使用原 State，同时阶段入口读取最新磁盘文件。"""

        def behavior(count, state):
            """第一次构造失败，checkpoint 恢复成功。"""
            if count == 1:
                raise RuntimeError("original failure")
            return {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            file = Path(root) / ".devagentstudio/specs/requirement-spec.json"
            file.parent.mkdir(parents=True); file.write_text('{"latest":true}')
            wrapper = CheckpointFirstRetryGraph(graph, context, origin, True)
            chunks = [c async for c in wrapper.astream(state, **options)]
            self.assertTrue(chunks)
            self.assertEqual(len(received), 2)
            self.assertEqual(received[-1]["clarification"], {"checkpointOnly": True})
            self.assertEqual(received[-1]["requirement_spec"], {"latest": True})
            self.assertEqual(received[-1]["active_run_id"], "child")
            self.assertIsNone(load_retry_origin(root, "child", "thread"))

    async def test_missing_pending_during_checkpoint_automatically_reexecutes_business(self):
        """续跑旧动作找不到草稿时，同一次重试转入新业务执行，不发布虚假确认。"""

        from app.graph.nodes.task_planning_adapter import _stale_confirm_result
        from app.services.build_task_plan_lifecycle import ConfirmPromotionResult

        def behavior(count, state):
            """原运行失败，续跑碰到已消费草稿，第三次使用干净输入生成结果。"""

            if count == 1:
                raise RuntimeError("original generation failure")
            if count == 2:
                return _stale_confirm_result(ConfirmPromotionResult(status="stale_draft"), state=state,
                                             context=SimpleNamespace(scope={"type": "application", "targetId": "application"}))
            return {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            chunks = [chunk async for chunk in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options)]
            self.assertEqual(len(received), 3)
            self.assertNotIn("clarification", received[-1])
            self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
            self.assertNotIn("requires_user_input", str(chunks))

    async def test_checkpoint_exception_automatically_reexecutes_original_node(self):
        """恢复抛错在同一次流中降级；新输入不带旧 State，原问题不变。"""

        def behavior(count, state):
            """原执行及恢复失败，第三次业务重执行成功。"""
            if count < 3:
                raise RuntimeError("original failure" if count == 1 else "restore failure at new checkpoint")
            return {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            wrapper = CheckpointFirstRetryGraph(graph, context, origin, True)
            chunks = [c async for c in wrapper.astream(state, **options)]
            self.assertEqual(len(received), 3)
            self.assertNotIn("clarification", received[-1])
            self.assertEqual(received[-1]["request"], "原始问题 ORIGINAL")
            self.assertNotIn("restore failure", str(chunks))

    async def test_fork_failure_falls_back_and_durable_origin_prevents_second_attempt(self):
        """恢复准备失败也自动降级；业务再失败时，后续点击不能恢复新失败现场。"""

        def behavior(count, state):
            """所有业务执行失败，以检查下一次仍锚定原节点和原输入。"""
            raise RuntimeError("business still failed")

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            save_retry_origin(root, "child", origin)
            with patch.object(graph, "aupdate_state", AsyncMock(side_effect=ValueError("cannot restore"))) as restore:
                with self.assertRaisesRegex(RuntimeError, "business still failed"):
                    async for _ in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options):
                        pass
                restore.assert_awaited_once()
            source = retry_fixtures.BusinessNodeRetryTests().source(root).model_copy(update={"run_id": "child", "current_node": "workflow_entry"})
            hint = await read_retry_hint(source, graph)
            self.assertEqual((hint.node, hint.request), ("build", "原始问题 ORIGINAL"))
            new_context = SimpleNamespace(**vars(context)); new_context.new_run_id = "second-child"
            new_context.retry_origin = None
            new_context.graph = graph
            with patch("app.services.checkpoint_first_retry.prepare_business_node_retry", AsyncMock(return_value=new_context)), \
                    patch.object(graph, "aupdate_state", AsyncMock()) as restore:
                next_context = await prepare_checkpoint_first_retry(source, graph)
                self.assertFalse(next_context.graph.attempt)
                with self.assertRaises(RuntimeError):
                    async for _ in next_context.graph.astream({**state, "active_run_id": "second-child"},
                            config={"configurable": {"thread_id": "thread", "checkpoint_id": "fresh-second"}},
                            stream_mode=["updates", "custom"]):
                        pass
                restore.assert_not_awaited()

    async def test_model_configuration_error_does_not_trigger_fallback(self):
        """恢复时模型不存在或额度权限错误直接上报，不再额外执行业务节点。"""

        def behavior(count, state):
            """构造原业务失败，恢复时模拟 SDK 的模型不存在响应。"""
            if count == 1:
                raise RuntimeError("original failure")
            raise openai.NotFoundError("model does not exist",
                response=httpx.Response(404, request=httpx.Request("POST", "https://example.invalid")), body={})

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            with self.assertRaises(openai.NotFoundError):
                async for _ in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options):
                    pass
            self.assertEqual(len(received), 2)

    async def test_cancellation_never_launches_business_fallback(self):
        """取消恢复不能被吞成恢复失败并继续执行。"""

        def behavior(count, state):
            """生成原始异常快照。"""
            raise RuntimeError("original")

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            with patch.object(graph, "aupdate_state", AsyncMock(side_effect=asyncio.CancelledError)):
                with self.assertRaises(asyncio.CancelledError):
                    async for _ in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options):
                        pass
            self.assertEqual(len(received), 1)

    async def test_terminal_business_failure_also_falls_back(self):
        """节点以 failed 增量结束而非抛异常，也自动进入一次业务重执行。"""

        def behavior(count, state):
            """第二次恢复返回失败对象，第三次完成。"""
            if count == 1:
                raise RuntimeError("original")
            return {"status": "failed", "error": "restore failed"} if count == 2 else {"status": "completed"}

        with tempfile.TemporaryDirectory() as root:
            graph, origin, context, state, options, received = await self.fixture(root, behavior)
            chunks = [c async for c in CheckpointFirstRetryGraph(graph, context, origin, True).astream(state, **options)]
            self.assertEqual(len(received), 3)
            self.assertNotIn("restore failed", str(chunks))
