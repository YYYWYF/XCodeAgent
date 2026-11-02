"""验证技术规划编辑器在 DeepAgents 共有接口下的搜索、调度和字段修订边界。"""

from copy import deepcopy
import json
from unittest import TestCase
from unittest.mock import patch

from deepagents.backends.state import StateBackend
from deepagents.backends.utils import create_file_data
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from app.agents.main.technical_plan_editor import (
    TECHNICAL_PLAN_CANDIDATE_PATH,
    TechnicalPlanCandidateBackend,
    TECHNICAL_PLAN_EDIT_SECTIONS,
    edit_technical_plan_with_agent,
)
from app.config import Settings
from tests.test_technical_plan_fragment_editor import ScriptedEditorModel, candidate_plan, tool_response


class ReadWindowEditorModel(ScriptedEditorModel):
    """直接复制真实读取工具返回的窗口，复现模型使用多行上下文修改重名字段。"""

    offset: int
    limit: int

    def _generate(self, messages, **kwargs):
        """先读再原样复制窗口编辑，避免预先手写正确缩进掩盖展示污染。"""

        self.seen_messages.append(deepcopy(messages))
        if len(self.seen_messages) == 1:
            message = tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                                    offset=self.offset, limit=self.limit)
        elif len(self.seen_messages) == 2:
            read = next(item for item in reversed(messages) if isinstance(item, ToolMessage))
            source = read.content.split("\n```\n", 1)[1].rsplit("\n```", 1)[0]
            message = tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                                    old_string=source, new_string=source.replace('"title": {', '"imageTitle": {'))
        else:
            message = AIMessage(content="已修改列表字段，详情字段保持不变。")
        return ChatResult(generations=[ChatGeneration(message=message)])


class TechnicalPlanEditorToolsTests(TestCase):
    """使用真实文件工具验证两个版本共用的限制，不伪造中间件或工具执行。"""

    def _edit(self, baseline, request, responses):
        """只替换模型响应，让原生 Agent 调度内存候选工具。"""

        model = ScriptedEditorModel(responses=responses)
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
            result = edit_technical_plan_with_agent(
                {"confirmed_product_plan": {"pages": []}, "planning_adjustment_request": request},
                baseline,
                settings=Settings(model_base_url="http://unused", model_api_key="unused", model_name="test"),
            )
        return result, model

    def test_native_search_and_read_remain_bounded(self):
        """真实内存搜索最多返回四十项，显式缩小上限有效，读取仍最多一百二十行。"""

        backend = TechnicalPlanCandidateBackend()
        files = {TECHNICAL_PLAN_CANDIDATE_PATH: create_file_data("\n".join(f"match {i}" for i in range(150)))}
        with patch.object(StateBackend, "_read_files", return_value=files):
            self.assertEqual(len(backend.grep("match").matches), 40)
            self.assertEqual(len(backend.grep("match", max_count=5).matches), 5)
            self.assertEqual(len(backend.grep("match", max_count=100).matches), 40)
            self.assertEqual(backend.grep("absent").matches, [])
            self.assertEqual(len(backend.read(TECHNICAL_PLAN_CANDIDATE_PATH, limit=1000).file_data["content"].splitlines()), 120)

    def test_excluded_tools_cannot_be_dispatched(self):
        """即使模型调用已排除的工具，也不能整文件写入、删除或执行命令。"""

        baseline = candidate_plan()
        original = deepcopy(baseline)
        result, model = self._edit(baseline, "只修改分页默认值为8。", [
            tool_response("write_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH, content="overwritten"),
            tool_response("delete", file_path=TECHNICAL_PLAN_CANDIDATE_PATH),
            tool_response("execute", command="must-not-execute"),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="已完成局部修改。"),
        ])
        rejected = [m for m in model.seen_messages[-1] if isinstance(m, ToolMessage) and m.status == "error"]
        self.assertEqual({m.name for m in rejected}, {"write_file", "delete", "execute"})
        self.assertTrue(all("not a valid tool" in m.content for m in rejected))
        self.assertTrue(all(set(tools) == {"grep", "read_file", "edit_file"} for tools in model.seen_tools))
        self.assertEqual(baseline, original)
        expected = {key: deepcopy(original[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        expected["api_contracts"][0]["endpoints"][0]["parameters"][0]["schema"]["default"] = 8
        self.assertEqual(result, expected)

    def test_diary_response_field_rename_preserves_other_sections(self):
        """复现用户的 API 字段改名要求，同步必填引用且不改动其他规划内容。"""

        baseline = candidate_plan()
        baseline["api_contracts"][0]["schemas"]["DiaryListItem"] = {
            "type": "object", "properties": {"diaryDate": {"type": "string", "description": "日记日期"}},
            "required": ["diaryDate"],
        }
        original = deepcopy(baseline)
        result, _ = self._edit(baseline, "DiaryListItem中的diaryDate就叫date吧", [
            tool_response("grep", pattern="diaryDate", output_mode="content"),
            tool_response("read_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH, offset=0, limit=120),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"diaryDate": {', new_string='"date": {'),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"diaryDate"', new_string='"date"'),
            AIMessage(content="已修改返回字段及必填引用，等待确认。"),
        ])
        expected = {key: deepcopy(original[key]) for key in ("architecture", "entities", "api_contracts", "pages")}
        schema = expected["api_contracts"][0]["schemas"]["DiaryListItem"]
        schema["properties"]["date"] = schema["properties"].pop("diaryDate")
        schema["required"] = ["date"]
        self.assertEqual(result, expected)
        self.assertEqual(baseline, original)

    def test_copying_native_read_window_edits_only_target_schema(self):
        """读取窗口没有行号污染或长行换行，复制原文可只改同名字段中的列表项。"""

        baseline = candidate_plan()
        field = {"type": "string", "description": "猫图片标题。" * 600, "entity_field_ref": "CatImage.title"}
        baseline["api_contracts"][0]["schemas"] = {
            "CatImageListItem": {"type": "object", "properties": {"title": deepcopy(field)}},
            "CatImageDetailOutput": {"type": "object", "properties": {"title": deepcopy(field)}},
        }
        original = deepcopy(baseline)
        sections = {key: deepcopy(baseline[key]) for key in TECHNICAL_PLAN_EDIT_SECTIONS}
        lines = json.dumps(sections, ensure_ascii=False, indent=2).splitlines()
        offset = next(i for i, line in enumerate(lines) if '"CatImageListItem": {' in line)
        end = next(i for i, line in enumerate(lines) if '"CatImageDetailOutput": {' in line)
        model = ReadWindowEditorModel(responses=[], offset=offset, limit=end-offset)
        with patch("app.agents.main.technical_plan_editor.create_chat_model", return_value=model):
            result = edit_technical_plan_with_agent(
                {"confirmed_product_plan": {"pages": []},
                 "planning_adjustment_request": "我要把CatImageListItem中的title改成imageTitle"},
                baseline, settings=Settings(model_base_url="http://unused", model_api_key="unused", model_name="test"),
            )
        expected_schema = sections["api_contracts"][0]["schemas"]["CatImageListItem"]["properties"]
        expected_schema["imageTitle"] = expected_schema.pop("title")
        self.assertEqual(result, sections)
        self.assertEqual(baseline, original)
        read = next(item for item in model.seen_messages[1] if isinstance(item, ToolMessage))
        self.assertIn("\n".join(lines[offset:end]), read.content)

    def test_native_read_errors_are_preserved(self):
        """无效路径仍由原生工具拒绝，原始文本投影不把读取失败伪装成成功。"""

        _, model = self._edit(candidate_plan(), "分页默认值改为8。", [
            tool_response("read_file", file_path="/missing.json"),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": 8'),
            AIMessage(content="已修改。"),
        ])
        read = next(item for item in model.seen_messages[1] if isinstance(item, ToolMessage))
        self.assertEqual(read.status, "error")
        self.assertNotIn("原始文本", read.content)

    def test_edit_feedback_checks_json_without_confirming_artifact(self):
        """中间候选可暂时无效，修复后的回执只证明语法和根结构，不冒充正式确认。"""

        _, model = self._edit(candidate_plan(), "分页默认值改为8。", [
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": 20', new_string='"default": PENDING'),
            tool_response("edit_file", file_path=TECHNICAL_PLAN_CANDIDATE_PATH,
                          old_string='"default": PENDING', new_string='"default": 8'),
            AIMessage(content="已修改，等待正式确认。"),
        ])
        first = next(item for item in model.seen_messages[1] if isinstance(item, ToolMessage))
        last = next(item for item in reversed(model.seen_messages[-1]) if isinstance(item, ToolMessage))
        self.assertEqual(first.status, "success")
        self.assertIn("JSON 语法尚未通过", first.content)
        self.assertIn("JSON 语法和四段根结构检查通过", last.content)
        self.assertIn("尚未执行正式合同校验或用户确认", last.content)
