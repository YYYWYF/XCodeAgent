"""验证五项编辑值实际进入生成包和 Agent 装配，不启动模型或业务服务。"""

import unittest

from app.services.business_acceptance_verifiers.python_agent import verify_agent_module
from app.services.runtime_agent_editable_config import compile_agent_prompt


MODEL = '''from langchain_openai import ChatOpenAI
from app.settings import RuntimeSettings
def project_model(settings: RuntimeSettings):
    return ChatOpenAI(model=settings.model_name, api_key=settings.model_api_key,
        base_url=settings.model_base_url, temperature=settings.model_temperature)
'''
FACTORY = '''from deepagents import create_deep_agent
from app.models.factory import project_model
def create(settings, context, checkpointer):
    model = project_model(settings)
    configured_model = model.model_copy(update={"temperature": 0.7})
    return create_deep_agent(model=configured_model, system_prompt="提示词", tools=[])
'''


class RuntimeAgentSettingsAcceptanceTests(unittest.TestCase):
    """检查缺字段、错误参数及额外参数都不能冒充实际生效。"""

    def setUp(self):
        """为编译、生成和静态验收准备相同的当前业务配置。"""

        self.prompt = {"persona": {"role": "政策助手", "tone": "简洁专业"},
            "systemPrompt": "依据公司政策回答。", "constraints": ["不编造政策", "说明适用条件"]}
        self.expected_prompt = "角色：政策助手\n表达风格：简洁专业\n\n依据公司政策回答。\n\n业务约束：\n- 不编造政策\n- 说明适用条件"

    def verify(self, module, configuration, source=FACTORY, extra=None):
        """消费生产验收入口，禁止用合同文件中出现字符串作为装配证据。"""

        return verify_agent_module({"agent-runtime/src/app/agent/factory.py": source,
            "agent-runtime/src/app/models/factory.py": MODEL, **(extra or {})},
            {"agent_id": "policy", "agent_count": 1, "module": module, "configuration": configuration})

    def test_complete_prompt_is_composed_and_verified(self):
        """角色、语气、正文和约束缺一项都不能通过相同正式配置的验收。"""

        self.assertEqual(compile_agent_prompt(self.prompt), self.expected_prompt)
        source = FACTORY.replace('"提示词"', repr(self.expected_prompt))
        self.assertEqual(self.verify("prompt", self.prompt, source)["status"], "passed")
        incomplete = FACTORY.replace('"提示词"', repr(self.prompt["systemPrompt"]))
        self.assertEqual(self.verify("prompt", self.prompt, incomplete)["status"], "failed")
        self.assertEqual(self.verify("prompt", {**self.prompt, "unknown": "未实现"}, source)["status"], "blocked")

    def test_agent_local_temperature_and_inherited_model_both_pass(self):
        """Agent 覆盖和跟随项目默认分别验证，覆盖不能改成全局默认或无效 bind。"""

        config = {"source": "project", "generation": {"temperature": 0.7}}
        self.assertEqual(self.verify("model", config)["status"], "passed")
        inherited = FACTORY.replace('model=configured_model', 'model=model')
        self.assertEqual(self.verify("model", {"source": "project"}, inherited)["status"], "passed")
        for source in (inherited, FACTORY.replace('"temperature": 0.7', '"temperature": 0.2'),
                       FACTORY.replace('model.model_copy(update={"temperature": 0.7})', 'model.bind(temperature=0.7)')):
            with self.subTest(source=source):
                self.assertNotEqual(self.verify("model", config, source)["status"], "passed")
        self.assertNotEqual(self.verify("model", {"source": "project"})["status"], "passed")

    def test_model_copy_cannot_override_credentials_or_other_parameters(self):
        """当前模型参数白名单不能被模型副本或额外正式字段绕过。"""

        config = {"source": "project", "generation": {"temperature": 0.7}}
        source = FACTORY.replace('"temperature": 0.7', '"temperature": 0.7, "api_key": "forbidden"')
        self.assertEqual(self.verify("model", config, source)["status"], "blocked")
        self.assertEqual(self.verify("model", {**config, "maxTokens": 100})["status"], "blocked")
        mutated = FACTORY.replace('    configured_model =', '    model.temperature = 0.9\n    configured_model =')
        self.assertEqual(self.verify("model", config, mutated)["status"], "failed")

    def test_registered_agent_model_copy_tracks_real_agui_injection(self):
        """多 Agent 模板模式下同样追踪 Builder→分派→AG-UI 的模型来源。"""

        source = '''from deepagents import create_deep_agent
def build_policy(model, runtime_context, checkpointer):
    return create_deep_agent(model=model.model_copy(update={"temperature": 0.7}), system_prompt="提示词", tools=[])
_AGENT_BUILDERS = {"policy": build_policy}
def create_agent(agent_id, model, runtime_context, checkpointer):
    return _AGENT_BUILDERS[agent_id](model=model, runtime_context=runtime_context, checkpointer=checkpointer)
'''
        assembly = '''from app.agent.factory import create_agent
from app.models.factory import project_model
def build_agent_runtime_stream(settings, runtime_context, checkpointer):
    def stream():
        agent = create_agent(agent_id="policy", model=project_model(settings), runtime_context=runtime_context, checkpointer=checkpointer)
        return agent
    return stream()
'''
        result = self.verify("model", {"source": "project", "generation": {"temperature": 0.7}}, source,
            {"agent-runtime/src/app/server/agui.py": assembly})
        self.assertEqual(result["status"], "passed")

    def test_module_packet_contains_compiled_prompt_without_changing_other_modules(self):
        """生成器收到平台编译的最终 Prompt，不能自行漏掉角色或业务约束。"""

        from app.agents.agent_runtime.modules import agent_module_packet
        from app.services.runtime_agent_module_policy import module_source_refs

        plan = {"topology": {"type": "agent_runtime_direct", "serviceIds": ["agent-runtime"],
            "publicEdgeServiceId": "agent-runtime", "authenticationTermination": "anonymous-session"},
            "pages": [{"pageId": "policy_page"}], "api_contracts": [], "agent_contracts": [{
                "agentId": "policy", "name": "政策助手", "purpose": "政策问答", "pageIds": ["policy_page"],
                "agentSettings": {"prompt": self.prompt, "model": {"source": "project"}, "memory": {}, "context": {},
                    "tools": {"enabled": False, "bindings": []}, "skills": {"enabled": False}, "knowledge": {"enabled": False}}}]}
        path = "agent-runtime/src/app/agent/factory.py"
        policy = {"policySha256": "policy-sha", "modulePaths": {"prompt": {"modifyPaths": ["src/app/agent/factory.py"]}}}
        task = {"id": "agent:policy::prompt", "unit_id": "agent:policy", "owner": "agent", "task_type": "agent.code",
            "target_files": [path], "allowed_paths": [path], "can_run_in_parallel": False,
            "change_scope": [{"path": path, "operation": "modify"}], "deliverables": [{"paths": [path]}]}
        task["source_refs"] = module_source_refs(plan, task, policy)
        packet = agent_module_packet(plan, task, policy)
        self.assertEqual(packet["resolvedSystemPrompt"], self.expected_prompt)
        self.assertEqual(packet["configuration"], self.prompt)
        self.assertEqual(plan["agent_contracts"][0]["agentSettings"]["model"], {"source": "project"})
