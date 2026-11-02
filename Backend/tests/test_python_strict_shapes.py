"""严格基础类型的静态形态及旧证据阻断的显式恢复回归。"""

import ast
from unittest import TestCase
from unittest.mock import patch

from app.services.business_acceptance_verifiers.python_ast import PythonEvidenceUnavailable, PythonProgram
from app.services.business_acceptance_verifiers.python_shapes import python_shape, shape_errors
from app.services.build_business_retry import restored_source_task_ids


class PythonStrictShapeTests(TestCase):
    """使用真实 AST 验证常见导入写法和失败边界，不执行目标项目。"""

    def test_strict_types_and_aliases_keep_basic_schema(self):
        """根导入、模块别名和 types 导入均按真实 Pydantic 身份解析。"""
        program = PythonProgram({"agent-runtime/src/app/schemas.py": """
import pydantic as pd
from pydantic import BaseModel, Field, StrictStr as Text, StrictBool
from pydantic.types import StrictInt, StrictFloat
class Request(BaseModel):
    employee_id: Text = Field(alias='employeeId')
    count: StrictInt
    amount: StrictFloat
    active: StrictBool
    labels: list[pd.StrictStr]
"""})
        actual = python_shape(program, program.units[0], ast.Name(id="Request"), mode="request")
        properties = {key: {"type": kind, "required": True} for key, kind in
                      [("employeeId", "string"), ("count", "integer"), ("amount", "number"), ("active", "boolean")]}
        properties["labels"] = {"type": "array", "items": {"type": "string"}, "required": True}
        self.assertEqual(shape_errors({"type": "object", "properties": properties}, actual), [])
        self.assertTrue(shape_errors({"type": "object", "properties": {"count": {"type": "string"}}}, actual))

    def test_unrelated_same_named_type_is_not_trusted(self):
        """第三方或缺失的同名类型不能因名称相同绕过结构验证。"""
        program = PythonProgram({"agent-runtime/src/app/schemas.py": "from custom import StrictStr"})
        with self.assertRaises(PythonEvidenceUnavailable):
            python_shape(program, program.units[0], ast.Name(id="StrictStr"))

    def test_explicit_retry_requires_current_verifier_to_pass(self):
        """旧证据不足只在当前完整验收通过时释放，未知或错误实现继续阻断。"""
        task = {"id": "objects", "status": "failed", "failure_category": "business_acceptance_blocked",
                "business_acceptance_checks": [{"id": "objects-check"}],
                "business_acceptance_evidence": [{"check_id": "objects-check", "status": "blocked",
                                                  "facts": {"reason_code": "python_evidence_unavailable"}}]}
        with patch("app.services.build_business_retry._source_hash_errors", return_value=[]), patch(
            "app.services.build_business_retry.verify_business_acceptance"
        ) as verify:
            for status, expected in [("passed", {"objects"}), ("blocked", set()), ("failed", set())]:
                verify.return_value = {"status": status}
                self.assertEqual(restored_source_task_ids([task], formal_artifacts={"current": True},
                                                        workspace_root="/workspace"), expected)
