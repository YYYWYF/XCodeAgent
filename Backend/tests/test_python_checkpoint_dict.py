"""checkpoint 配置字典 AST 解析回归，不加载或执行目标工程。"""
import ast
from unittest import TestCase

from app.services.business_acceptance_verifiers.python_ast import PythonProgram
from app.services.business_acceptance_verifiers.python_agent_context import _dict_field


class PythonCheckpointDictTests(TestCase):
    """覆盖 memory 隔离键验收所消费的真实嵌套字典形态。"""

    def test_nested_checkpointer_config_and_missing_key(self):
        """静态返回字典必须能逐层读取，不调用 AST.values 列表。"""
        program = PythonProgram({"agent-runtime/src/app/persistence/checkpointer.py":
            "def checkpointer_config(thread_key):\n    return {'configurable': {'thread_id': thread_key}}\n"})
        unit, function = program.functions["app.persistence.checkpointer.checkpointer_config"]
        config = _dict_field(program, unit, function, function.body[0].value, "configurable")
        thread = _dict_field(program, unit, function, config, "thread_id")
        self.assertIsInstance(thread, ast.Name)
        self.assertEqual(thread.id, "thread_key")
        self.assertIsNone(_dict_field(program, unit, function, config, "missing"))

    def test_dynamic_dictionary_is_not_static_evidence(self):
        """动态展开或非字典返回不能被当成静态线程配置。"""
        program = PythonProgram({"agent-runtime/src/app/persistence/checkpointer.py": "def config(): return {}"})
        unit, function = program.functions["app.persistence.checkpointer.config"]
        for expression in ("{**other, 'thread_id': key}", "make_config()"):
            self.assertIsNone(_dict_field(program, unit, function, ast.parse(expression, mode="eval").body, "thread_id"))
