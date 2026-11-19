"""独立验证需求澄清的模型输入，不依赖未提交的文档生成入口。"""

from copy import deepcopy
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.graph.nodes.requirements import _request_for_requirement_node, requirements
from app.services.requirement_clarification import requirement_clarification_request


class ModelInputCaptured(Exception):
    """捕获真实需求节点的模型输入后结束测试，避免生成或保存业务文档。"""


class RequirementClarificationTests(unittest.TestCase):
    """检查各题型的业务含义及原需求完整进入模型，而不是预设模型成功结果。"""

    def test_mixed_answers_reach_actual_model_boundary(self):
        """上游文本即使只剩应用名，需求节点仍从结构化答案恢复全部问答。"""
        source = "展示键盘外观、价格和名称，只供访客浏览。"
        questions = [
            {"id": "name", "header": "应用名称", "question": "应用叫什么名字？", "type": "text"},
            {"id": "features", "header": "功能模块", "question": "需要哪些功能？", "type": "choice",
                "options": [{"value": "list", "label": "键盘列表展示"}, {"value": "search", "label": "键盘搜索/筛选"}]},
            {"header": "业务参与者", "question": "应用面向哪些用户？", "type": "choice",
                "options": [{"value": "visitor", "label": "仅访客/用户", "description": "无需登录，只浏览键盘信息"}]},
            {"id": "flow", "header": "核心流程", "question": "主要流程是什么？", "type": "choice",
                "options": [{"label": "浏览键盘详情"}, {"value": "__other__", "label": "其他"}]},
        ]
        answers = {"name": "键盘展示应用", "features": {"selected": ["list", "search"]},
            "业务参与者": {"selected": ["visitor"]},
            "flow": {"selected": ["浏览键盘详情", "__other__"], "other": "按价格从低到高排序"}}
        with tempfile.TemporaryDirectory() as workspace:
            state = {"workspace": workspace, "request": "键盘展示应用", "workflow_scope": "application_planning",
                "requirement_spec": {"source_request": source, "confirmation_status": "pending_user_input", "pages": []},
                "requirements_clarification_round": 1,
                "clarification": {"mode": "ask_user_question", "status": "requires_user_input", "questions": questions},
                "application_planning_interaction": {"action": "answer", "request": "键盘展示应用", "answers": answers}}
            before = deepcopy(state)
            with patch("app.graph.nodes.requirements.analyze_requirements_with_chat_model",
                       side_effect=ModelInputCaptured) as model:
                with self.assertRaises(ModelInputCaptured):
                    requirements(state)
            request = model.call_args.args[0]
            for text in (source, "应用名称", "应用叫什么名字？", "键盘展示应用", "功能模块", "需要哪些功能？",
                         "键盘列表展示", "键盘搜索/筛选", "业务参与者", "仅访客/用户", "无需登录，只浏览键盘信息",
                         "核心流程", "浏览键盘详情", "按价格从低到高排序"):
                self.assertIn(text, request)
            self.assertNotIn("__other__", request)
            self.assertEqual(model.call_args.kwargs["clarification_round"], 1)
            self.assertEqual(model.call_args.kwargs["existing_spec"]["confirmation_status"], "pending_user_input")
            self.assertEqual(state, before)
            self.assertEqual(list(Path(workspace).iterdir()), [], "不写 checkpoint 或业务文档")

    def test_only_choices_and_other_keep_context(self):
        """没有文本框也保留数组、其他补充，以及当前前端采用的问题键规则。"""
        questions = [{"question": "使用什么终端？", "options": [{"value": "desktop", "label": "电脑"}]},
            {"options": [{"value": "browse", "label": "浏览详情"}]}, {"id": "other", "header": "补充要求"}]
        answers = {"使用什么终端？": ["desktop"], "1": {"selected": ["browse"]},
            "other": {"selected": ["__other__"], "other": "按品牌分组"}, "__applicationPlanningAction": "answer"}
        before = deepcopy(answers)
        result = requirement_clarification_request("展示键盘", {"questions": questions}, answers, "只展示有库存的键盘")
        for text in ("展示键盘", "只展示有库存的键盘", "使用什么终端？", "电脑", "浏览详情", "补充要求", "按品牌分组"):
            self.assertIn(text, result)
        self.assertNotIn("__other__", result)
        self.assertNotIn("__applicationPlanningAction", result)
        self.assertEqual(answers, before)

    def test_non_answer_requests_keep_existing_behavior(self):
        """正式确认、修改、普通请求及其他工作流不经过澄清文本转换。"""
        for action in ("confirm", "revise", "design_change"):
            with self.subTest(action=action):
                self.assertEqual(_request_for_requirement_node({"workflow_scope": "application_planning"},
                    {"action": action, "request": " 本轮明确操作 ", "answers": {"choice": {"selected": ["option"]}}}), "本轮明确操作")
        self.assertEqual(_request_for_requirement_node({"workflow_scope": "application_planning", "request": "原始要求"}, {}), "原始要求")
        self.assertEqual(_request_for_requirement_node({"request": "原始要求"}, {"action": "answer", "request": "其他流程"}), "原始要求")
