from __future__ import annotations

import json
import unittest

from langchain_core.messages import AIMessage, ToolMessage

from app.tools.ask_user import (
    AskUserQuestion,
    ask_user,
    build_ask_user_payload,
    extract_ask_user_clarification,
)


class AskUserToolTests(unittest.TestCase):
    def test_tool_builds_generic_gemini_style_payload(self) -> None:
        content = ask_user.invoke(
            {
                "questions": [
                    {
                        "header": "Database",
                        "question": "Which data source should the app use?",
                        "type": "choice",
                        "options": [
                            {
                                "label": "Mock",
                                "description": "Use local mock data for the first version.",
                            },
                            {
                                "label": "API",
                                "description": "Connect to an existing external API.",
                            },
                        ],
                    }
                ]
            }
        )

        payload = json.loads(content)
        self.assertEqual(payload["mode"], "ask_user_question")
        self.assertEqual(payload["status"], "requires_user_input")
        self.assertEqual(payload["question_schema"], "gemini_cli.ask_user.v1")
        self.assertEqual(payload["questions"][0]["header"], "Database")
        self.assertEqual(payload["questions"][0]["options"][-1]["label"], "其他")
        self.assertEqual(payload["questions"][0]["options"][-1]["value"], "__other__")

    def test_payload_preserves_eight_questions_for_batched_clarification(self) -> None:
        """通用问题工具必须保留需求澄清所需的批量问题，而不是截断成旧上限。"""

        questions = [
            AskUserQuestion(
                header=f"问题{i}",
                question=f"请补充第{i}项业务信息。",
                type="text",
            )
            for i in range(1, 9)
        ]
        payload = build_ask_user_payload(
            questions
        )

        self.assertEqual(len(payload["questions"]), 8)
        clarification = extract_ask_user_clarification(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "name": "ask_user",
                                "args": {
                                    "questions": [
                                        question.model_dump(
                                            by_alias=True,
                                            exclude_none=True,
                                        )
                                        for question in questions
                                    ]
                                },
                                "id": "call-8",
                            }
                        ],
                    )
                ]
            },
            {"app_info": {"name": "Demo App"}},
        )

        self.assertEqual(len(clarification["questions"]), 8)

    def test_extracts_clarification_from_tool_message(self) -> None:
        payload = build_ask_user_payload(
            [
                AskUserQuestion.model_validate(
                    {
                        "header": "Pages",
                        "question": "Which pages are required?",
                        "type": "text",
                        "placeholder": "Dashboard, list, detail...",
                    }
                )
            ]
        )
        result = {
            "messages": [
                ToolMessage(
                    content=json.dumps(payload),
                    tool_call_id="call-1",
                    name="ask_user",
                )
            ]
        }

        clarification = extract_ask_user_clarification(
            result,
            {"app_info": {"name": "Demo App"}},
        )

        self.assertEqual(clarification["status"], "requires_user_input")
        self.assertEqual(clarification["questions"][0]["header"], "Pages")
        self.assertEqual(clarification["spec_summary"], "Demo App")

    def test_preserves_multi_select_for_combinable_choice_options(self) -> None:
        payload = build_ask_user_payload(
            [
                AskUserQuestion.model_validate(
                    {
                        "header": "列表能力",
                        "question": "人员列表需要哪些附加能力？",
                        "type": "choice",
                        "multiSelect": True,
                        "options": [
                            {"label": "搜索", "description": "按关键词检索人员"},
                            {"label": "筛选", "description": "按条件筛选人员"},
                            {"label": "导入导出", "description": "批量导入或导出数据"},
                            {"label": "分页", "description": "按页浏览人员"},
                        ],
                    }
                )
            ]
        )

        question = payload["questions"][0]
        self.assertTrue(question["multiSelect"])
        self.assertEqual(
            [option["label"] for option in question["options"]],
            ["搜索", "筛选", "导入导出", "分页", "其他"],
        )

    def test_extracts_clarification_from_ai_tool_call(self) -> None:
        result = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "ask_user",
                            "args": {
                                "questions": [
                                    {
                                        "header": "Auth",
                                        "question": "Do users need login?",
                                        "type": "yesno",
                                    }
                                ]
                            },
                            "id": "call-1",
                        }
                    ],
                )
            ]
        }

        clarification = extract_ask_user_clarification(
            result,
            {"app_info": {"name": "Demo App"}},
        )

        self.assertEqual(clarification["status"], "requires_user_input")
        self.assertEqual(clarification["questions"][0]["type"], "yesno")
        self.assertTrue(clarification["questions"][0]["allowOther"])

    def test_returns_clear_when_agent_did_not_call_ask_user(self) -> None:
        clarification = extract_ask_user_clarification(
            {"messages": [AIMessage(content="requirements are clear")]},
            {"app_info": {"name": "Demo App"}},
        )

        self.assertEqual(clarification["status"], "clear")
        self.assertEqual(clarification["questions"], [])


if __name__ == "__main__":
    unittest.main()
