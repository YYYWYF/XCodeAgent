"""测试专用：组装当前模板并运行真实 DeepAgents/工具/checkpoint，仅替换供应商模型。"""
import argparse
from functools import lru_cache
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import socket
import sys
import tempfile


def main() -> None:
    """启动隔离的本机业务 Runtime，不读取用户 dotenv 或访问真实业务数据。"""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--template-root', required=True, type=Path)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='t27-python-runtime-') as directory:
        root = Path(directory)
        source = args.template_root / 'template-source/code/agent-runtime'
        shutil.copytree(source / 'base/src', root / 'src')
        for extension in sorted((source / 'extensions').iterdir()):
            if (extension / 'files/src').is_dir():
                shutil.copytree(extension / 'files/src', root / 'src', dirs_exist_ok=True)
        sys.path.insert(0, str(root / 'src'))
        os.environ.update(AGENT_RUNTIME_WORKING_DIR=str(root), AGENT_RUNTIME_LAUNCH_ID='t27-integration',
            AGENT_RUNTIME_NAMESPACE='probe:t27-integration', AGENT_RUNTIME_PROFILE='probe',
            AGENT_RUNTIME_HOST='127.0.0.1', AGENT_RUNTIME_WORKERS='1',
            AGENT_RUNTIME_AUTH_ENABLED='false', AGENT_RUNTIME_AUTH_MODE='anonymous-session',
            AGENT_RUNTIME_DATABASE_ENABLED='false', AGENT_RUNTIME_ALLOWED_ORIGINS='[]',
            AGENT_RUNTIME_ANONYMOUS_SESSION_SECRET='integration-only-signing-key-at-least-32-characters')
        start(root)


def start(root: Path) -> None:
    """通过模板正式 FastAPI 入口运行，测试模型仍经过实际 DeepAgents 工具路由。"""
    import uvicorn
    from deepagents import create_deep_agent
    from langchain_core.language_models.chat_models import BaseChatModel
    from langchain_core.messages import AIMessage, ToolMessage
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain.tools import ToolRuntime, tool
    from langgraph.types import interrupt
    from app.agent.context import RuntimeContext
    import app.agent.factory as factory
    import app.agent.execution_policy as policies
    import app.agent.invocation as invocation
    import app.models.factory as models
    from app.agent.execution_guard import text_only_middleware

    writes: dict[str, int] = {}
    failures: set[str] = set()

    class BusinessModel(BaseChatModel):
        """确定性选择已声明工具；不模拟 Runtime、AG-UI 编码或工具执行器。"""

        @property
        def _llm_type(self) -> str:
            """标识测试模型，不调用外部供应商。"""
            return 't27-offline-business'

        def bind_tools(self, tools, **kwargs):
            """保留真实工具装配和中间件校验。"""
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            """工具结果直接形成结构化回复，普通输入决定本轮工具。"""
            if isinstance(messages[-1], ToolMessage):
                answer = AIMessage(content=messages[-1].content)
            else:
                text = str(messages[-1].content)
                if '连接失败回归' in text:
                    if text not in failures:
                        failures.add(text)
                        import httpx
                        from openai import APIConnectionError
                        raise APIConnectionError(request=httpx.Request('POST', 'https://model.invalid/chat'))
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content='显式重试成功'))])
                if '失败后继续回归' in text:
                    return ChatResult(generations=[ChatGeneration(message=AIMessage(content='失败后继续成功'))])
                name = 'update_order' if '更新' in text else 'clarify_order' if '澄清' in text else 'lookup_order'
                answer = AIMessage(content='', tool_calls=[{'name': name, 'args': {},
                    'id': 'call-' + str(len(messages)), 'type': 'tool_call'}])
            return ChatResult(generations=[ChatGeneration(message=answer)])

    def selected(runtime) -> str:
        """读取本轮实际 RuntimeContext 中的页面记录，缺目标明确失败。"""
        page = runtime.context.page_context
        if page is None:
            raise ValueError('缺少页面上下文')
        return next(item.value for item in page.context_items if item.id == 'orderId')

    @tool
    def lookup_order(runtime: ToolRuntime[RuntimeContext]) -> dict:
        """读取当前测试订单。"""
        record = selected(runtime)
        return {'orderId': record, 'writes': writes.get(record, 0), 'amount': 42}

    @tool
    def update_order(runtime: ToolRuntime[RuntimeContext]) -> dict:
        """用户确认后更新隔离内存订单。"""
        record = selected(runtime)
        approved = interrupt({'schemaVersion': 1, 'interactionId': 'approval-' + record,
            'kind': 'confirmation', 'message': '是否更新测试订单 ' + record + '？', 'payload': {'orderId': record}})
        if approved is True:
            writes[record] = writes.get(record, 0) + 1
        return {'orderId': record, 'updated': approved is True, 'writes': writes.get(record, 0)}

    @tool
    def clarify_order(runtime: ToolRuntime[RuntimeContext]) -> dict:
        """通过真实 LangGraph 中断收集补充说明。"""
        record = selected(runtime)
        answer = interrupt({'schemaVersion': 1, 'interactionId': 'clarify-' + record,
            'kind': 'clarification', 'message': '请补充订单说明', 'payload': {'orderId': record}})
        return {'orderId': record, 'answer': answer}

    def builder(*, model, runtime_context, checkpointer, execution_policy):
        """装配与生成业务 Agent 相同的 DeepAgents、能力校验和 SQLite checkpoint。"""
        return create_deep_agent(model=model, context_schema=RuntimeContext,
            tools=[lookup_order, update_order, clarify_order], middleware=text_only_middleware(execution_policy),
            checkpointer=checkpointer, name='assistant')

    @lru_cache(maxsize=1)
    def offline_model(settings):
        """复用正式工厂清理合同，不加载任何模型凭据。"""
        return BusinessModel()

    factory._AGENT_BUILDERS = {'assistant': builder}
    models.create_chat_model = offline_model
    invocation.create_chat_model = offline_model
    contract = {'mode': 'conversation', 'inputSchema': {'type': 'object', 'properties': {'amount': {'type': 'integer'}},
        'required': [], 'additionalProperties': False}, 'resultSchema': {'type': 'object'},
        'textOutput': True, 'publicStateSchema': None, 'outcomes': [],
        'interactionKinds': ['clarification', 'confirmation', 'approval_required']}
    entry = {'agentId': 'assistant', 'apiContractId': 'agent.assistant', 'endpointId': 'agent.assistant.run',
        'rule': 'execution.policy.v2', 'invocation': {'exposure': 'public'}, 'interactionContract': contract,
        'capabilitySha256': 'sha256:' + 'a' * 64, 'implementationSha256': 'sha256:' + 'b' * 64,
        'budgets': {'requestBytes': 262144, 'messages': 128, 'userTextBytes': 16384, 'textBytes': 131072,
            'threadRuns': 1, 'identityRuns': 2, 'processRuns': 4, 'runSeconds': 120, 'cleanupSeconds': 10},
        'executionCapabilities': {'toolIds': ['lookup_order', 'update_order', 'clarify_order'],
            'resourceFiles': {'skills': [], 'knowledge': []}, 'modelRequests': 8}, 'resourceSha256': {}}
    policies.POLICIES_JSON = json.dumps({'assistant': entry})
    os.environ['AGENT_RUNTIME_EXECUTION_POLICY_SHA256'] = sha256(Path(policies.__file__).read_bytes()).hexdigest()
    from app.server.deployment import bind_server_config
    config = uvicorn.Config('app.server.app:app', host='127.0.0.1', port=0, workers=1, log_level='warning')
    bind_server_config(config)
    with socket.socket() as listener:
        listener.bind(('127.0.0.1', 0))
        listener.listen(128)
        print(json.dumps({'port': listener.getsockname()[1]}), flush=True)
        uvicorn.Server(config).run(sockets=[listener])


if __name__ == '__main__':
    main()
