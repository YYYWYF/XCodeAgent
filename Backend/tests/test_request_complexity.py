from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.services.request_complexity import decide_request_complexity


class FakeModel:
    def __init__(self, content: str) -> None:
        self.content = content

    def invoke(self, messages):
        self.messages = messages
        return SimpleNamespace(content=self.content)


class RequestComplexityTests(unittest.TestCase):
    def test_decision_uses_model_classification(self) -> None:
        fake_model = FakeModel(
            """
            {
              "complexity": "simple",
              "confidence": 0.86,
              "reason": "Localized copy change.",
              "signals": ["localized edit"]
            }
            """
        )

        with (
            patch("app.services.request_complexity.Settings.from_env") as from_env,
            patch(
                "app.services.request_complexity.create_chat_model",
                return_value=fake_model,
            ) as create_chat_model,
        ):
            from_env.return_value = SimpleNamespace(model_name="test-model")
            decision = decide_request_complexity("把按钮文案改成提交")

        create_chat_model.assert_called_once_with(from_env.return_value)
        self.assertEqual(decision.complexity, "simple")
        self.assertEqual(decision.confidence, 0.86)
        self.assertEqual(decision.reason, "Localized copy change.")
        self.assertEqual(decision.signals, ["localized edit"])

    def test_invalid_model_output_defaults_to_complex(self) -> None:
        fake_model = FakeModel("not json")

        with (
            patch("app.services.request_complexity.Settings.from_env") as from_env,
            patch(
                "app.services.request_complexity.create_chat_model",
                return_value=fake_model,
            ),
        ):
            from_env.return_value = SimpleNamespace(model_name="test-model")
            decision = decide_request_complexity("随便弄一下")

        self.assertEqual(decision.complexity, "complex")
        self.assertIn("invalid_model_classifier_response", decision.signals)

    def test_model_error_defaults_to_complex(self) -> None:
        with patch(
            "app.services.request_complexity.Settings.from_env",
            side_effect=RuntimeError("missing env"),
        ):
            decision = decide_request_complexity("随便弄一下")

        self.assertEqual(decision.complexity, "complex")
        self.assertIn("model_classifier_error", decision.signals)


if __name__ == "__main__":
    unittest.main()
