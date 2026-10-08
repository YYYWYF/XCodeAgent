from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import httpx

from app.agents.screenshot_ui_design.prompts import (
    build_static_contract_repair_prompt,
    build_targeted_contract_repair_prompt,
)
from app.agents.screenshot_ui_design.targeted_repair import (
    TsxRepairPatch,
    apply_tsx_repair_patch,
)
from app.agents.screenshot_ui_design.visibility_contract import validate_visible_bindings
from app.agents.screenshot_ui_design.transport import (
    NonRetryableScreenshotModelError,
    invoke_vision_text,
)


class ScreenshotTargetedRepairTests(unittest.TestCase):
    """验证任意页面契约的局部修复不会覆盖其他视觉代码。"""

    def test_multiple_nonoverlapping_edits_keep_other_layout(self) -> None:
        """多处控件替换按原文位置应用，保留无关布局与字体设置。"""

        source = (
            "<main style={{fontFamily: 'system-ui'}}>"
            "<span>手机号码</span><button>变更</button></main>"
        )
        patch = TsxRepairPatch.model_validate(
            {"edits": [
                {"old": "<span>手机号码</span>", "new": '<span data-information-item-id="phone" data-control-id="phone-display">手机号码</span>'},
                {"old": "<button>变更</button>", "new": '<button data-action-id="change-phone" data-control-id="change-phone-control">变更</button>'},
            ]}
        )
        result = apply_tsx_repair_patch(source, patch)
        self.assertIn("fontFamily: 'system-ui'", result)
        self.assertIn('data-information-item-id="phone"', result)
        self.assertIn('data-action-id="change-phone"', result)

    def test_ambiguous_or_overlapping_edit_is_rejected(self) -> None:
        """模型给出不唯一或交叠原文时，整份候选不发生部分修改。"""

        with self.assertRaises(ValueError):
            apply_tsx_repair_patch(
                "<span>A</span><span>A</span>",
                TsxRepairPatch.model_validate({"edits": [{"old": "<span>A</span>", "new": "<span>B</span>"}]}),
            )
        with self.assertRaises(ValueError):
            apply_tsx_repair_patch(
                "<span>A</span>",
                TsxRepairPatch.model_validate({"edits": [
                    {"old": "<span>A</span>", "new": "<span>B</span>"},
                    {"old": "A", "new": "B"},
                ]}),
            )

    def test_repair_prompts_include_semantic_product_facts(self) -> None:
        """修复模型同时得到字段标签与组合交互步骤，避免只根据 ID 猜。"""

        page = {
            "pageId": "any_page",
            "information_items": [{"itemId": "credit", "label": "可用额度"}],
            "actions": [{"actionId": "create", "behavior": {"type": "sequence", "steps": [
                {"stepId": "reveal", "type": "interface", "expectedResult": "展示完整密钥"}
            ]}}],
        }
        for prompt in (
            build_static_contract_repair_prompt(page, "AnyPage", "export default AnyPage;", "missing"),
            build_targeted_contract_repair_prompt(page, "AnyPage", "export default AnyPage;", "missing"),
        ):
            self.assertIn("可用额度", prompt)
            self.assertIn("reveal", prompt)
            self.assertIn("展示完整密钥", prompt)

    def test_hidden_controls_report_all_action_ids(self) -> None:
        """隐藏的真实绑定一次性列出，供局部修复定位可见控件。"""

        code = (
            '<div><input data-action-id="select-left" style={{display: "none"}} />'
            '<input data-action-id="select-right" style={{display: "none"}} />'
            '<button>左</button><button>右</button></div>'
        )
        errors = validate_visible_bindings(code)
        self.assertEqual(len(errors), 1)
        self.assertIn("select-left", errors[0])
        self.assertIn("select-right", errors[0])

    def test_exhausted_model_quota_fails_without_repeated_requests(self) -> None:
        """403 额度耗尽应立即报告，避免把同一失败请求重复提交多次。"""

        response = httpx.Response(
            403,
            request=httpx.Request("POST", "https://example.test/chat/completions"),
            json={"error": {"message": "Free quota exhausted"}},
        )
        client = MagicMock()
        client.__enter__.return_value.post.return_value = response
        settings = SimpleNamespace(
            screenshot_model_api_name="qwen3-vl-plus",
            screenshot_timeout_seconds=10,
            model_max_retries=3,
            model_trust_env=False,
            screenshot_model_base_url="https://example.test",
            screenshot_model_api_key="test-key",
        )
        with patch("app.agents.screenshot_ui_design.transport.httpx.Client", return_value=client):
            with self.assertRaisesRegex(NonRetryableScreenshotModelError, "Free quota exhausted"):
                invoke_vision_text(
                    settings=settings,
                    system_prompt="system",
                    user_prompt="repair",
                    images=[],
                    max_tokens=100,
                )
        self.assertEqual(client.__enter__.return_value.post.call_count, 1)


if __name__ == "__main__":
    unittest.main()
