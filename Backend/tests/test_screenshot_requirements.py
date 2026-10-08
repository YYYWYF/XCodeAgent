from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PIL import Image
from pydantic import ValidationError

from app.agents.screenshot_requirements.agent import (
    _invoke_vision_model,
    analyze_requirements_from_screenshots,
)
from app.agents.screenshot_requirements.models import (
    RequirementInput,
    ScreenshotRequirementOutput,
    normalize_requirement_input,
)
from app.agents.screenshot_requirements.preprocess import prepare_image
from app.agents.screenshot_model_compat import (
    screenshot_http_timeout,
    screenshot_request_options,
    screenshot_response_format,
)
from app.config import Settings
from app.graph.nodes.requirements import requirements as requirements_node
from app.protocols.workflow.request import workflow_run_inputs
from app.services.requirement_spec import create_requirement_spec
from app.workspace.spec_documents import (
    load_requirement_spec_json,
    requirement_spec_draft_json_path,
    write_requirement_spec_draft_document,
)


def _png_bytes(size: tuple[int, int] = (800, 600)) -> bytes:
    """生成测试使用的纯色 PNG 字节。"""

    image = Image.new("RGB", size, "white")
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _model_output() -> ScreenshotRequirementOutput:
    """构造无需真实模型调用的完整截图需求候选。"""

    return ScreenshotRequirementOutput(
        generated_at="2026-09-07T00:00:00+00:00",
        app_info={
            "name": "订单中心",
            "summary": "根据截图管理订单。",
            "target": "PC",
            "menu_enabled": True,
            "route_root_path": "/page",
        },
        user_roles=[
            {
                "id": "business_user",
                "name": "业务使用者",
                "description": "查看和处理订单。",
            }
        ],
        feature_modules=[
            {
                "id": "orders",
                "name": "订单管理",
                "description": "管理订单列表。",
            }
        ],
        pages=[
            {
                "pageId": "order_list",
                "name": "订单列表",
                "path": "/page/orders",
                "module_id": "orders",
                "description": "查看订单列表。",
                "visible_information": ["订单编号", "订单状态"],
                "visible_controls": ["状态筛选", "导出订单"],
            }
        ],
        entities=[
            {
                "id": "Order",
                "name": "订单",
                "description": "订单列表中展示的业务记录。",
                "module_id": "orders",
                "fields": [
                    {
                        "label": "订单编号",
                        "description": "用于识别订单的编号。",
                    }
                ],
            }
        ],
        business_flows=[
            {
                "id": "browse_orders",
                "name": "浏览订单",
                "description": "进入订单列表查看订单。",
                "steps": ["打开订单列表", "查看订单信息"],
            }
        ],
        authorization_requirements={
            "enabled": False,
            "restrictedPages": [],
            "restrictedOperations": [],
        },
        acceptance_criteria=["业务使用者可以查看订单列表。"],
        clarification_questions=[],
        unresolved_requirement_dimensions=[],
        agent_note='{"confidence":"high"}',
    )


def _write_application_config(workspace: Path) -> None:
    """为需求节点集成测试写入最小应用权威配置。"""

    application_dir = workspace / ".xcodeagent"
    application_dir.mkdir(parents=True, exist_ok=True)
    (application_dir / "application.json").write_text(
        json.dumps(
            {
                "schemaVersion": 6,
                "configRevision": 1,
                "datasource": {"type": "database"},
                "auth": {"enable": False},
                "authorization": {
                    "enabled": False,
                    "initialAdministratorSubjects": [],
                },
                "track": {"enable": False},
                "apiTrack": {"enable": False},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


class ScreenshotRequirementTests(unittest.TestCase):
    """验证截图需求输入、预处理和 XCodeAgent 适配结果。"""

    def test_preprocess_creates_overview_and_orientation_guide(self) -> None:
        """普通截图应生成全局图和方向参考图。"""

        prepared = prepare_image(_png_bytes(), "page.png", max_side=700)
        self.assertEqual([item.label for item in prepared], ["overview", "orientation-guide"])
        self.assertEqual(max(prepared[0].width, prepared[0].height), 700)

    def test_screenshot_mode_requires_an_image(self) -> None:
        """截图模式缺少图片时应在协议边界失败。"""

        with self.assertRaises(ValidationError):
            normalize_requirement_input({"mode": "screenshot", "screenshots": []})

    def test_screenshot_input_accepts_every_count_from_one_to_ten(self) -> None:
        """需求协议按输入上限校验，而非假设固定五张截图。"""

        references = [
            {
                "relativePath": f".xcodeagent/inputs/screenshots/run/{index}.png",
                "name": f"{index}.png",
                "mimeType": "image/png",
                "size": 100,
                "sha256": f"{index:064x}",
            }
            for index in range(1, 12)
        ]
        for count in range(1, 11):
            with self.subTest(screenshots=count):
                normalized = normalize_requirement_input(
                    {"mode": "screenshot", "screenshots": references[:count]}
                )
                self.assertEqual(len(normalized["screenshots"]), count)
        with self.assertRaises(ValidationError):
            normalize_requirement_input(
                {"mode": "screenshot", "screenshots": references}
            )

    def test_model_output_normalizes_module_id_camel_alias(self) -> None:
        """视觉模型误用 moduleId 时应受控兼容并统一输出 module_id。"""

        payload = _model_output().model_dump()
        payload["pages"][0]["moduleId"] = payload["pages"][0].pop("module_id")
        payload["entities"][0]["moduleId"] = payload["entities"][0].pop("module_id")

        normalized = ScreenshotRequirementOutput.model_validate(payload).model_dump()

        self.assertEqual(normalized["pages"][0]["module_id"], "orders")
        self.assertEqual(normalized["entities"][0]["module_id"], "orders")
        self.assertNotIn("moduleId", normalized["pages"][0])
        self.assertNotIn("moduleId", normalized["entities"][0])

    def test_model_output_keeps_other_unknown_fields_forbidden(self) -> None:
        """兼容 moduleId 不能放宽其他拼写错误或模型发明字段。"""

        payload = _model_output().model_dump()
        payload["pages"][0]["moduleID"] = payload["pages"][0].pop("module_id")

        with self.assertRaises(ValidationError):
            ScreenshotRequirementOutput.model_validate(payload)

    def test_model_output_normalizes_object_evidence_for_any_page_count(self) -> None:
        """多页截图中对象形式的可见证据与流程步骤应保留信息并满足正式契约。"""

        for count in (1, 5, 10):
            with self.subTest(pages=count):
                payload = _model_output().model_dump()
                payload["pages"] = [
                    {
                        **payload["pages"][0],
                        "pageId": f"page_{index}",
                        "path": f"/page/{index}",
                        "visible_information": [
                            {"label": "充值余额", "description": "显示 ¥9.53"}
                        ],
                        "visible_controls": [
                            {"label": "去充值", "description": "跳转充值页面"}
                        ],
                    }
                    for index in range(count)
                ]
                payload["business_flows"][0]["steps"] = [
                    {"step": "选择金额"},
                    {"step": "去支付", "description": "提交当前金额"},
                ]
                payload["acceptance_criteria"] = [
                    {"criterion": "能够完成支付", "description": "展示支付结果"}
                ]
                normalized = ScreenshotRequirementOutput.model_validate(payload).model_dump()
                self.assertEqual(len(normalized["pages"]), count)
                self.assertEqual(
                    normalized["pages"][-1]["visible_information"],
                    ["充值余额：显示 ¥9.53"],
                )
                self.assertEqual(
                    normalized["business_flows"][0]["steps"],
                    ["选择金额", "去支付：提交当前金额"],
                )
                self.assertEqual(
                    normalized["acceptance_criteria"],
                    ["能够完成支付：展示支付结果"],
                )

    def test_unknown_evidence_shape_stays_invalid(self) -> None:
        """无法无损表达的模型对象仍需校验失败，不能被静默丢弃。"""

        payload = _model_output().model_dump()
        payload["pages"][0]["visible_information"] = [{"unknown": "余额"}]
        with self.assertRaises(ValidationError):
            ScreenshotRequirementOutput.model_validate(payload)

    def test_invalid_model_json_gets_one_text_only_schema_repair(self) -> None:
        """结构错误只进行一次纯文本修复，成功后才向后续流程交付候选。"""

        invalid = _model_output().model_dump()
        invalid["pages"][0]["visible_information"] = [{"unknown": "余额"}]
        valid = _model_output().model_dump()
        responses = []
        for body in (invalid, valid):
            response = Mock()
            response.json.return_value = {
                "choices": [{"message": {"content": json.dumps(body, ensure_ascii=False)}}]
            }
            responses.append(response)
        settings = Settings(
            model_base_url="https://main.example/v1",
            model_api_key="main-key",
            model_name="main-model",
            screenshot_base_url="https://vision.example/v1",
            screenshot_api_key="vision-key",
            screenshot_model_name="qwen3-vl-plus",
        )
        token_output: list[str] = []
        image = SimpleNamespace(
            source_name="page.png",
            label="overview",
            width=800,
            height=600,
            operations=[],
            jpeg_bytes=b"image-bytes",
        )
        with patch("app.agents.screenshot_requirements.agent.httpx.Client") as client_type:
            client = client_type.return_value.__enter__.return_value
            client.post.side_effect = responses
            output = _invoke_vision_model([image], "创建应用", settings, token_output.append)

        self.assertEqual(output.pages[0].pageId, "order_list")
        self.assertEqual(client.post.call_count, 2)
        first_request = client.post.call_args_list[0].kwargs["json"]
        repair_request = client.post.call_args_list[1].kwargs["json"]
        self.assertTrue(
            any(part["type"] == "image_url" for part in first_request["messages"][1]["content"])
        )
        self.assertIsInstance(repair_request["messages"][1]["content"], str)
        self.assertIn("visible_information", repair_request["messages"][1]["content"])
        self.assertEqual(len(token_output), 1)

    def test_screenshot_model_configuration_can_fallback_or_override(self) -> None:
        """视觉模型配置应默认复用主服务，并允许独立覆盖地址、密钥和模型。"""

        inherited = Settings(
            model_base_url="https://main.example/v1/",
            model_api_key="main-key",
            model_name="main-model",
        )
        self.assertEqual(inherited.screenshot_model_base_url, "https://main.example/v1")
        self.assertEqual(inherited.screenshot_model_api_key, "main-key")
        self.assertEqual(inherited.screenshot_model_api_name, "main-model")
        overridden = Settings(
            model_base_url="https://main.example/v1",
            model_api_key="main-key",
            model_name="main-model",
            screenshot_base_url="https://vision.example/v1/",
            screenshot_api_key="vision-key",
            screenshot_model_name="vision-model",
        )
        self.assertEqual(overridden.screenshot_model_base_url, "https://vision.example/v1")
        self.assertEqual(overridden.screenshot_model_api_key, "vision-key")
        self.assertEqual(overridden.screenshot_model_api_name, "vision-model")

    def test_screenshot_model_configuration_rejects_partial_override(self) -> None:
        """独立视觉模型只配置部分字段时不得混用主模型地址或凭据。"""

        partial = Settings(
            model_base_url="https://main.example/v1",
            model_api_key="main-key",
            model_name="main-model",
            screenshot_base_url="https://vision.example/v1",
        )
        with self.assertRaisesRegex(RuntimeError, "必须同时配置或同时留空"):
            _ = partial.screenshot_model_base_url

        anthropic = Settings(
            model_base_url="https://anthropic.example/v1",
            model_api_key="main-key",
            model_name="claude-model",
            model_provider="anthropic",
        )
        with self.assertRaisesRegex(RuntimeError, "截图模式需要配置"):
            _ = anthropic.screenshot_model_api_name

    def test_qwen_multimodal_uses_json_object_without_thinking_and_long_timeout(self) -> None:
        """千问多模态应使用官方兼容的非思考 JSON Object 与独立长超时。"""

        settings = Settings(
            model_base_url="https://main.example/v1",
            model_api_key="main-key",
            model_name="main-model",
            screenshot_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
            screenshot_api_key="vision-key",
            screenshot_model_name="qwen3-vl-plus",
            screenshot_response_format="auto",
            screenshot_timeout_seconds=300,
        )

        self.assertEqual(screenshot_response_format(settings), "json_object")
        self.assertEqual(screenshot_request_options(settings), {"enable_thinking": False})
        self.assertEqual(screenshot_http_timeout(settings).read, 300)

    def test_workflow_request_carries_screenshot_input(self) -> None:
        """AG-UI 请求应把合法截图清单传入 application planning 状态。"""

        digest = "a" * 64
        inputs = workflow_run_inputs(
            {
                "threadId": "thread-1",
                "messages": [{"role": "user", "content": "根据截图生成"}],
                "forwardedProps": {
                    "workflowScope": "application_planning",
                    "workspaceRoot": "C:/workspace/demo",
                    "requirementInput": {
                        "mode": "screenshot",
                        "screenshots": [
                            {
                                "relativePath": ".xcodeagent/inputs/screenshots/run/page.png",
                                "name": "page.png",
                                "mimeType": "image/png",
                                "size": 100,
                                "sha256": digest,
                            }
                        ],
                    },
                },
            }
        )
        self.assertEqual(inputs["resume_values"]["requirement_input"]["mode"], "screenshot")

    def test_embedded_agent_returns_existing_analysis_contract(self) -> None:
        """视觉输出应转换为现有 requirements 节点消费的分析结构。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            screenshot = root / ".xcodeagent" / "inputs" / "screenshots" / "run" / "page.png"
            screenshot.parent.mkdir(parents=True)
            raw = _png_bytes()
            screenshot.write_bytes(raw)
            requirement_input = RequirementInput.model_validate(
                {
                    "mode": "screenshot",
                    "screenshots": [
                        {
                            "relativePath": screenshot.relative_to(root).as_posix(),
                            "name": screenshot.name,
                            "mimeType": "image/png",
                            "size": len(raw),
                            "sha256": hashlib.sha256(raw).hexdigest(),
                        }
                    ],
                }
            ).model_dump(by_alias=True)
            with (
                patch(
                    "app.agents.screenshot_requirements.agent.Settings.from_env",
                    return_value=SimpleNamespace(screenshot_model_api_name="vision-test"),
                ),
                patch(
                    "app.agents.screenshot_requirements.agent._invoke_vision_model",
                    return_value=_model_output(),
                ),
            ):
                result = analyze_requirements_from_screenshots(
                    "涉及权限控制：否。",
                    requirement_input,
                    workspace=str(root),
                )
        spec = result["requirement_spec"]
        self.assertEqual(spec["app_info"]["name"], "订单中心")
        self.assertEqual(spec["analysis_source"], "screenshot_vision_model")
        self.assertEqual(spec["pages"][0]["visible_controls"], ["状态筛选", "导出订单"])
        self.assertIn("截图可见信息：订单编号、订单状态", spec["pages"][0]["description"])
        self.assertIn("截图可交互控件：状态筛选、导出订单", spec["pages"][0]["description"])
        self.assertEqual(result["clarification"]["status"], "clear")
        self.assertNotEqual(spec.get("confirmation_status"), "confirmed")

    def test_requirements_node_replaces_existing_draft_automatically(self) -> None:
        """截图分析完成后应由原 requirements 节点覆盖草稿 JSON，无需人工替换。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_application_config(root)
            old_spec = create_requirement_spec("创建旧版库存管理系统")
            old_spec.update(
                {
                    "confirmation_status": "pending_user_confirmation",
                    "clarification_status": "clear",
                    "clarification_questions": [],
                }
            )
            write_requirement_spec_draft_document({"workspace": str(root)}, old_spec)

            screenshot = root / ".xcodeagent" / "inputs" / "screenshots" / "run" / "page.png"
            screenshot.parent.mkdir(parents=True)
            raw = _png_bytes()
            screenshot.write_bytes(raw)
            requirement_input = {
                "mode": "screenshot",
                "screenshots": [
                    {
                        "relativePath": screenshot.relative_to(root).as_posix(),
                        "name": screenshot.name,
                        "mimeType": "image/png",
                        "size": len(raw),
                        "sha256": hashlib.sha256(raw).hexdigest(),
                    }
                ],
            }
            with (
                patch(
                    "app.agents.screenshot_requirements.agent.Settings.from_env",
                    return_value=SimpleNamespace(screenshot_model_api_name="vision-test"),
                ),
                patch(
                    "app.agents.screenshot_requirements.agent._invoke_vision_model",
                    return_value=_model_output(),
                ),
            ):
                result = requirements_node(
                    {
                        "request": "根据截图生成需求",
                        "workspace": str(root),
                        "workflow_scope": "application_planning",
                        "requirement_input": requirement_input,
                        "application_planning_interaction": {
                            "action": "revise",
                            "request": "以截图内容重新生成需求",
                        },
                    }
                )

            stored = load_requirement_spec_json(
                requirement_spec_draft_json_path({"workspace": str(root)})
            )
            self.assertEqual(result["status"], "clear")
            self.assertFalse(result["requirements_confirmed"])
            self.assertEqual(stored["app_info"]["name"], "订单中心")
            self.assertEqual(stored["analysis_source"], "screenshot_vision_model")
            self.assertEqual(stored["confirmation_status"], "pending_user_confirmation")


if __name__ == "__main__":
    unittest.main()
