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

    def test_returns_clear_when_agent_did_not_call_ask_user(self) -> None:
        clarification = extract_ask_user_clarification(
            {"messages": [AIMessage(content="requirements are clear")]},
            {"app_info": {"name": "Demo App"}},
        )

        self.assertEqual(clarification["status"], "clear")
        self.assertEqual(clarification["questions"], [])


if __name__ == "__main__":
    unittest.main()
