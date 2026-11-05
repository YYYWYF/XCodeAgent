"""回归需求返回方式字段与澄清过滤后的完整产物补齐。"""

from copy import deepcopy
import tempfile
import unittest
from unittest.mock import patch

from app.agents.main.requirements_analyzer import (
    _validate_complete_requirement_spec, analyze_requirements_with_chat_model,
)
from app.services.requirement_spec import create_requirement_spec
from app.tools.ask_user import clear_clarification
from tests.test_requirements_confirmation import requirements


class RequirementResponseModeRecoveryTests(unittest.TestCase):
    """只使用内存模型结果与临时工作区，不调用真实模型。"""

    def setUp(self):
        """构造已完整生成的业务需求，避免依赖真实项目产物。"""
        self.spec = create_requirement_spec("创建一个库房管理系统")
        self.spec["agent_requirements"] = [{"agentId": "ddl_detector", "name": "检测助手", "purpose": "检测敏感字段",
            "capabilities": ["识别敏感字段"], "entryPageIds": [], "interactionMode": "task", "boundaries": []}]

    def test_optional_response_modes_pass_strict_model_boundary(self):
        """七个基础字段完整时，可选返回方式不能被当成未知字段拒绝。"""
        for mode in ("json", "ag-ui"):
            with self.subTest(mode=mode):
                spec = deepcopy(self.spec)
                spec["agent_requirements"][0]["responseMode"] = mode
                _validate_complete_requirement_spec(spec)

    def test_missing_and_unknown_fields_are_named(self):
        """保留真实缺字段检查，同时告诉用户具体字段而非 complete-fields。"""
        spec = deepcopy(self.spec)
        del spec["agent_requirements"][0]["purpose"]
        with self.assertRaisesRegex(ValueError, "缺少字段：purpose"):
            _validate_complete_requirement_spec(spec)
        spec = deepcopy(self.spec)
        spec["agent_requirements"][0]["stream"] = False
        with self.assertRaisesRegex(ValueError, "未声明字段：stream"):
            _validate_complete_requirement_spec(spec)
        spec = deepcopy(self.spec)
        spec["agent_requirements"][0]["responseMode"] = "invalid"
        with self.assertRaisesRegex(ValueError, "responseMode 必须为 json 或 ag-ui"):
            _validate_complete_requirement_spec(spec)

    def test_filtered_questions_complete_empty_draft_once(self):
        """泛化问题被删除后，先补齐空草稿，再显示原来的正式确认卡。"""
        empty = create_requirement_spec("创建一个库房管理系统", allow_inferred_defaults=False)
        first = {"requirement_spec": empty, "clarification": {"status": "requires_user_input", "questions": [
            {"header": "需求确认", "question": "请确认需求已完整，无需进一步澄清", "type": "yesno"}]}}
        complete = {"requirement_spec": self.spec, "clarification": clear_clarification(self.spec)}
        with tempfile.TemporaryDirectory() as workspace, patch(
                "app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                side_effect=[first, complete]) as analyze:
            result = requirements({"request": "创建一个库房管理系统", "workspace": workspace})
        self.assertEqual(analyze.call_count, 2)
        self.assertIs(analyze.call_args.kwargs["allow_clarification"], False)
        self.assertEqual(result["requirement_spec"]["confirmation_status"], "pending_user_confirmation")
        self.assertFalse(result["requirements_confirmed"])

    def test_completion_failure_does_not_loop(self):
        """补齐模式失败后保留错误，不重复发起内容生成。"""
        with patch("app.agents.main.requirements_analyzer._analyze_requirements_once",
                side_effect=ValueError("需求 AI 未返回完整 RequirementSpec JSON。")) as analyze:
            with self.assertRaisesRegex(ValueError, "未返回完整"):
                analyze_requirements_with_chat_model("创建库房系统", allow_clarification=False)
        self.assertEqual(analyze.call_count, 1)
