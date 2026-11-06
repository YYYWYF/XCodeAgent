"""本地启动允许实现变化，同时保留正式能力和交付策略校验。"""

from hashlib import sha256
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from app.services.runtime_execution_policy import (
    BEGIN, END, POLICY_PATH, execution_policy_operation, publish_execution_policies,
    verify_execution_policy,
)
from tests.test_runtime_endpoint_contracts import plan
from app.services.runtime_agent_implementation import agent_implementation_facts


class RuntimeExecutionPolicyStartupTests(unittest.TestCase):
    """用真实静态依赖编译与平台发布流程验证启动准入，不执行目标代码。"""

    def test_registry_accepts_local_string_constants(self):
        """普通及带类型注解的字符串常量保持原静态注册语义。"""
        original = self.factory.read_text()
        expected = agent_implementation_facts(self.root, {'order_assistant'})
        for declaration in ('AGENT_ID = "order_assistant"', 'AGENT_ID: str = "order_assistant"'):
            with self.subTest(declaration=declaration):
                self.factory.write_text(declaration + '\n' + original.replace(
                    '{"order_assistant": _build}', '{AGENT_ID: _build}'))
                self.assertEqual(agent_implementation_facts(self.root, {'order_assistant'}), expected)

    def test_registry_rejects_duplicate_resolved_identities(self):
        """不同键写法解析为同一身份时仍拒绝重复注册。"""
        original = self.factory.read_text()
        for registry in ('{AGENT_ID: _build, "order_assistant": _build}',
                         '{AGENT_ID: _build, OTHER_ID: _build}'):
            with self.subTest(registry=registry):
                self.factory.write_text('AGENT_ID = "order_assistant"\nOTHER_ID = "order_assistant"\n'
                    + original.replace('{"order_assistant": _build}', registry))
                with self.assertRaisesRegex(ValueError, '重复身份：order_assistant'):
                    agent_implementation_facts(self.root, {'order_assistant'})

    def test_registry_rejects_dynamic_and_unknown_keys(self):
        """动态值、未定义身份与非字符串值不能通过常量解析绕过检查。"""
        original = self.factory.read_text()
        for declaration, key in (('AGENT_ID = get_id()', 'AGENT_ID'),
                                 ('AGENT_ID = 123', 'AGENT_ID'), ('', 'UNKNOWN_ID'), ('', 'get_id()')):
            with self.subTest(key=key, declaration=declaration):
                self.factory.write_text(declaration + '\n' + original.replace(
                    '{"order_assistant": _build}', '{' + key + ': _build}'))
                with self.assertRaisesRegex(ValueError, '静态字符串常量'):
                    agent_implementation_facts(self.root, {'order_assistant'})

    def test_registry_constant_preserves_formal_identity_check(self):
        """常量解析后仍只允许当前正式合同中的智能体身份。"""
        self.factory.write_text('AGENT_ID = "unknown_agent"\n' + self.factory.read_text().replace(
            '{"order_assistant": _build}', '{AGENT_ID: _build}'))
        with self.assertRaisesRegex(ValueError, '正式合同以外'):
            agent_implementation_facts(self.root, {'order_assistant'})

    def setUp(self):
        """准备一个已确认且存在实际静态 Builder 的最小应用。"""
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.plan = plan()
        self.plan['agent_contracts'] = self.plan['agent_contracts'][:1]
        self.factory = self.root / 'agent-runtime/src/app/agent/factory.py'
        self.factory.parent.mkdir(parents=True)
        self.factory.write_text('''def _build():
    """构造当前业务智能体。"""
    return "original"

_AGENT_BUILDERS = {"order_assistant": _build}

def create_agent():
    """装配已注册智能体。"""
    return _build()
''')
        self.policy = self.root / POLICY_PATH
        self.policy.write_text(BEGIN + "POLICIES_JSON = '{}'\n" + END + '\n')
        scope = {'type': 'agent', 'targetId': 'order_assistant'}
        publish_execution_policies(self.root, self.plan, execution_policy_operation(self.plan, scope), scope)
        self.original = self.policy.read_bytes()

    def test_unchanged_policy_uses_actual_file_digest(self):
        """注入值必须是磁盘策略的实际哈希，与 Runtime 加载器一致。"""
        self.assertEqual(verify_execution_policy(self.root, self.plan), sha256(self.original).hexdigest())

    def test_changed_source_and_dependency_symbols_do_not_block_startup(self):
        """修复实现和新增内部 helper 均可重新启动，不要求重新确认开发计划。"""
        self.factory.write_text(self.factory.read_text().replace('return "original"', 'return _helper()')
            + '\ndef _helper():\n    """返回修复后的业务结果。"""\n    return "fixed"\n')
        self.assertEqual(verify_execution_policy(self.root, self.plan), sha256(self.original).hexdigest())
        self.assertEqual(self.policy.read_bytes(), self.original)

    def test_formal_capability_changes_still_block_startup(self):
        """交互能力发生正式变更时，旧策略仍需经过已确认发布流程更新。"""
        self.plan['agent_contracts'][0]['interactionContract']['interactionKinds'] = ['confirmation']
        with self.assertRaisesRegex(ValueError, '当前正式能力不一致'):
            verify_execution_policy(self.root, self.plan)

    def test_changed_budgets_are_not_treated_as_source_changes(self):
        """执行预算修改不能冒充普通实现变更获得放行。"""
        self.policy.write_bytes(self.original.replace(b'"modelRequests":1', b'"modelRequests":99'))
        self.assertNotEqual(self.policy.read_bytes(), self.original)
        with self.assertRaisesRegex(ValueError, '当前正式能力不一致'):
            verify_execution_policy(self.root, self.plan)

    def test_changed_registration_still_blocks_startup(self):
        """删除实际注册入口不能继续沿用旧 Agent 执行策略。"""
        self.factory.write_text(self.factory.read_text().replace('{"order_assistant": _build}', '{}'))
        with self.assertRaisesRegex(ValueError, '当前正式能力不一致'):
            verify_execution_policy(self.root, self.plan)
