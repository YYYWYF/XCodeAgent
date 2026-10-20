"""统一为所有工作区 Agent 注入运行取消和真实调用退出登记。"""

import asyncio
from deepagents import create_deep_agent as _create_deep_agent
from langchain.agents.middleware import AgentMiddleware
from app.services.run_workers import run_worker


async def _tracked_async(handler, request):
    """独立等待真实异步调用，外层取消不能提前释放仍在执行的工具登记。"""
    async def invoke():
        """在子任务内持有登记直到调用及其清理全部结束。"""
        with run_worker():
            return await handler(request)

    task = asyncio.create_task(invoke())
    # shield 防止外层 Graph 的取消把 to_thread 工具伪装成已经退出。
    def consume_result(done):
        """回收取消后独立结束的异常，避免无人读取的 Task 警告。"""
        if not done.cancelled():
            done.exception()
    task.add_done_callback(consume_result)
    return await asyncio.shield(task)


class RunCancellationMiddleware(AgentMiddleware):
    """所有 Agent 共用同一运行停止边界，不依赖各 Agent 的业务实现。"""

    def wrap_model_call(self, request, handler):
        """登记同步模型调用并阻止停止后继续生成。"""
        with run_worker():
            return handler(request)

    def wrap_tool_call(self, request, handler):
        """登记同步工具调用，停止成功前等待文件或命令操作结束。"""
        with run_worker():
            return handler(request)

    async def awrap_model_call(self, request, handler):
        """登记异步模型调用的真实生命周期。"""
        return await _tracked_async(handler, request)

    async def awrap_tool_call(self, request, handler):
        """登记异步工具及其同步线程的真实生命周期。"""
        return await _tracked_async(handler, request)


def create_deep_agent(*args, **kwargs):
    """沿用官方 Agent 工厂，只增加产品统一的运行控制中间件。"""
    kwargs["middleware"] = [RunCancellationMiddleware(), *kwargs.get("middleware", [])]
    return _create_deep_agent(*args, **kwargs)
