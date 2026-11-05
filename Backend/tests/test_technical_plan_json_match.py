"""覆盖 JSON 排版导致的真实局部编辑失败与唯一匹配约束。"""
from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch
from langchain_core.messages import AIMessage
from app.agents.main.technical_plan_json_match import unique_json_fragment
from app.agents.main.technical_plan_editor import edit_technical_plan_with_agent, TECHNICAL_PLAN_CANDIDATE_PATH
from app.config import Settings
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, candidate_plan, tool_response


class TechnicalPlanJsonMatchTests(TestCase):
    """忽略排版不等于忽略字符串内容或选择任意重复项。"""

    def test_layout_only_difference_matches_actual_source(self):
        """紧凑片段可匹配多行 Schema，字符串中的空格保持精确。"""
        source = '{\n  "Output": {\n    "description": "两 个字段",\n    "required": [\n      "balances"\n    ]\n  }\n}'
        fragment = '"Output": {"description":"两 个字段","required":["balances"]}'
        matched = unique_json_fragment(source, fragment)
        self.assertIsNotNone(matched)
        self.assertIn('\n', matched)
        self.assertIsNone(unique_json_fragment(source, fragment.replace('两 个', '两个')))

    def test_ambiguous_changed_or_whole_document_is_rejected(self):
        """不能靠忽略空白绕过唯一定位、旧值核对与整文件限制。"""
        self.assertIsNone(unique_json_fragment('{"a":{"x":1},"b":{"x":1}}', '"x": 1'))
        self.assertIsNone(unique_json_fragment('{"a":10}', '"a": 1'))
        self.assertIsNone(unique_json_fragment('{"a":1}', '{ "a": 1 }'))
        self.assertIsNone(unique_json_fragment('{"a":1}', '1: "a": 1'))

    def test_native_edit_tool_succeeds_with_different_json_layout(self):
        """通过真实候选后端执行一次工具编辑，不需要模型重读重试修缩进。"""
        baseline = candidate_plan()
        before = deepcopy(baseline)
        endpoint = deepcopy(baseline['api_contracts'][0]['endpoints'][0])
        changed = deepcopy(endpoint)
        changed['parameters'][0]['schema']['default'] = 8
        model = ScriptedEditorModel(responses=[tool_response('edit_file', file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
            old_string=json.dumps(endpoint, ensure_ascii=False), new_string=json.dumps(changed, ensure_ascii=False)),
            AIMessage(content='已修改。')])
        with patch('app.agents.main.technical_plan_editor.create_chat_model', return_value=model):
            result = edit_technical_plan_with_agent({'confirmed_product_plan': {'pages': []},
                'planning_adjustment_request': '只修改分页默认值为8。'}, baseline,
                settings=Settings(model_name='test', model_api_key='unused', model_base_url='http://unused'))
        self.assertEqual(result['api_contracts'][0]['endpoints'][0], changed)
        self.assertEqual(result['api_contracts'][0]['endpoints'][1], before['api_contracts'][0]['endpoints'][1])
        self.assertEqual(baseline, before)
