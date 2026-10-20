"""使用原生 DeepAgent 验证嵌套运行上下文变化后，平台仍可取消真实同步 worker。"""

import asyncio
from contextvars import Context
import threading
import unittest

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import tool

from app.agents.execution_control import create_deep_agent
from app.protocols.workflow.run_control import WorkflowRunRegistry
from app.services.run_workers import active_worker_runs, bind_run_workers, current_run_id, run_worker


class WorkerRuntimeIdentityTests(unittest.IsolatedAsyncioTestCase):
    """模型及工具沿本次 RunnableConfig 识别身份，不依赖外层 Python ContextVar 永远存在。"""

    async def test_nested_agent_stops_after_inflight_model_returns(self):
        """内层上下文重建后也只等在途模型返回，禁止新工具和第二次模型请求。"""
        started, release, finished = threading.Event(), threading.Event(), threading.Event()
        calls, tools, errors = [], [], []
        run_id = "nested-config-cancellation"

        class Model(BaseChatModel):
            """提供可控在途响应，实际执行 DeepAgent 模型和工具中间件。"""

            @property
            def _llm_type(self):
                """声明本地测试模型类型。"""
                return "worker-cancellation-test"

            def bind_tools(self, *args, **kwargs):
                """保留真实工具循环，免去外部模型请求。"""
                return self

            def _generate(self, messages, *args, **kwargs):
                """首个模型调用保持在途，返回后尝试调度工具。"""
                calls.append(current_run_id())
                started.set()
                release.wait(3)
                return ChatResult(generations=[ChatGeneration(message=AIMessage(
                    content="", tool_calls=[{"name": "probe", "args": {}, "id": str(len(calls))}],
                ))])

        @tool
        def probe() -> str:
            """记录不应在暂停后执行的工具。"""
            tools.append(current_run_id())
            return "继续"

        agent = create_deep_agent(model=Model(), tools=[probe])

        def work():
            """保持节点登记，并模拟框架嵌套调用使用新 Context 及明确配置。"""
            with run_worker():
                try:
                    Context().run(agent.invoke, {"messages": [{"role": "user", "content": "检查"}]},
                        config={"recursion_limit": 8, "metadata": {
                            "run_id": run_id, "workspace": "/tmp/nested-config-cancellation",
                        }})
                except BaseException as error:
                    errors.append(error)
                finally:
                    finished.set()

        async def outer():
            """让真实同步节点在线程中运行，外层取消不会强制杀掉 Python 线程。"""
            with bind_run_workers(run_id, "/tmp/nested-config-cancellation"):
                await asyncio.to_thread(work)

        registry = WorkflowRunRegistry()
        task = asyncio.create_task(outer())
        registry.register(run_id, task, workspace="/tmp/nested-config-cancellation")
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 3))
            cancellation = asyncio.create_task(registry.cancel_and_wait(run_id, timeout_seconds=3, ending=True))
            await asyncio.sleep(0.02)
            self.assertFalse(cancellation.done())
            release.set()
            self.assertEqual(await cancellation, "cancelled")
            self.assertTrue(finished.is_set())
            self.assertEqual(calls, [run_id])
            self.assertEqual(tools, [])
            self.assertIsInstance(errors[0], RuntimeError)
            self.assertNotIn(run_id, active_worker_runs())
        finally:
            release.set()
            await asyncio.to_thread(finished.wait, 3)
