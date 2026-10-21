"""用隔离配置、真实模型 SDK 和模拟 HTTP 验证三档网关及观测一致性。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import create_app
from app.tracing import TraceRecorder
from app.upstream import Upstream
from tests.helpers import chunk, completion, envelope, read_events, settings, sse_response


class GatewayTierTests(unittest.TestCase):
    """验证服务端选档、地址与模型配对、配置失败和调用观测。"""

    def setUp(self):
        """为每项测试准备独立目录，不读取或改写真实部署配置和数据库。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name)

    def gateway_settings(self, tier="fast", **overrides):
        """故意令三档模型名不同于档位，确保请求不会由档位字符串猜测模型。"""
        values = {
            "model_upstream": "gateway", "gateway_tier": tier,
            "gateway_api_key": "test-gateway-secret", "gateway_key": "test-gateway-key",
        }
        for level in ("fast", "pro", "ultra"):
            values[f"gateway_{level}_base_url"] = f"https://gateway.example.test/routes/{level}/v1/"
            values[f"gateway_{level}_model_name"] = f"route-{level}-model"
        values.update(overrides)
        return settings(self.path, **values)

    def test_all_tiers_stream_and_nonstream_end_to_end(self):
        """三档均走真实 SDK，核对地址、认证头、事件、历史及埋点中的实际档位。"""
        for tier in ("fast", "pro", "ultra"):
            for streaming in (False, True):
                with self.subTest(tier=tier, streaming=streaming):
                    captured = []

                    def handler(request):
                        """记录上游请求并提供含工具调用及用量的标准模型响应。"""
                        captured.append(request)
                        if not streaming:
                            return httpx.Response(200, json=completion())
                        return sse_response([
                            chunk({"content": "你好"}),
                            chunk({"tool_calls": [{"index": 0, "id": "call-tier", "type": "function",
                                                   "function": {"name": "search", "arguments": '{"q":"a"}'}}]}),
                            chunk(finish_reason="tool_calls", usage={"total_tokens": 5}),
                        ])

                    config = self.gateway_settings(tier, mock_analytics_enabled=True)
                    app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
                    with TestClient(app) as client:
                        public = client.get("/health").json()
                        run_id = f"run-{tier}-{streaming}"
                        events = read_events(client, envelope(run_id, streaming, {
                            "spaid": "user123", "sessionId": "session-abc", "requestId": run_id,
                            "extensions": {"gatewayTier": "forged", "future": 1},
                        }))
                        info_response = client.post("/model-info/run", json=envelope(f"info-{run_id}"),
                                                    headers={"Authorization": "Bearer local-model-transmit-dev-token"})
                    request = captured[0]
                    self.assertEqual(len(captured), 1)
                    self.assertEqual(str(request.url), f"https://gateway.example.test/routes/{tier}/v1/chat/completions")
                    self.assertEqual(request.headers["authorization"], "Bearer test-gateway-secret")
                    self.assertEqual(request.headers["x-gateway-key"], "test-gateway-key")
                    self.assertEqual(request.headers["x-spaid"], "user123")
                    self.assertEqual(request.headers["x-session-id"], "session-abc")
                    self.assertEqual(request.headers["x-request-id"], run_id)
                    self.assertEqual(json.loads(request.content)["model"], f"route-{tier}-model")
                    self.assertEqual(public["gatewayTier"], tier)
                    self.assertEqual(public["model"], f"route-{tier}-model")
                    self.assertNotIn("example.test", json.dumps(public))
                    self.assertIn(f'"gatewayTier":"{tier}"', info_response.text)
                    self.assertIn(f'"model":"route-{tier}-model"', info_response.text)
                    self.assertNotIn("test-gateway-secret", info_response.text)
                    self.assertEqual(events[0]["type"], "RUN_STARTED")
                    self.assertEqual(events[-1]["type"], "RUN_FINISHED")
                    self.assertTrue(any(event["type"] == "TOOL_CALL_START" for event in events))
                    self.assertTrue(any(event.get("name") == "model-inference.result" for event in events))
                    record = app.state.history.get(run_id)
                    self.assertEqual(record["status"], "completed")
                    self.assertEqual(record["model_name"], public["model"])
                    self.assertEqual(record["input_context"]["model"], public["model"])
                    self.assertEqual(record["extensions"], {"gatewayTier": tier, "future": 1})
                    payload = record["analysis"]["wouldInsert"]["payload"]
                    self.assertEqual(payload["extensions"], record["extensions"])
                    self.assertEqual(payload["model"], public["model"])
                    self.assertFalse(record["analysis"]["receipt"]["inserted"])
                    self.assertNotIn("test-gateway-secret", json.dumps(record))

    def test_only_selected_profile_requires_complete_configuration(self):
        """未选档位允许留空或未完成地址，选中档位必须具备有效地址与非空模型。"""
        for tier in ("fast", "pro", "ultra"):
            for bad_value, field, expected in (
                ("", "base_url", "BASE_URL"),
                ("not-an-url", "base_url", "BASE_URL"),
                ("https://name:secret@example.test/v1", "base_url", "BASE_URL"),
                ("https://example.test/v1?api_key=secret", "base_url", "BASE_URL"),
                ("https://example.test/v1#fragment", "base_url", "BASE_URL"),
                ("  ", "model_name", "MODEL_NAME"),
            ):
                with self.subTest(tier=tier, field=field, value=bad_value):
                    with self.assertRaises(ValidationError) as captured:
                        self.gateway_settings(tier, **{f"gateway_{tier}_{field}": bad_value})
                    diagnostic = str(captured.exception)
                    self.assertIn(f"GATEWAY_{tier.upper()}_{expected}", diagnostic)
                    self.assertNotIn("test-gateway-secret", diagnostic)
                    self.assertNotIn("name:secret", diagnostic)
            with self.subTest(tier=tier, inactive=True):
                inactive = {
                    f"gateway_{other}_{field}": "not-configured" if field == "base_url" else ""
                    for other in ("fast", "pro", "ultra") if other != tier
                    for field in ("base_url", "model_name")
                }
                self.assertEqual(self.gateway_settings(tier, **inactive).gateway_profile.tier, tier)
        for tier in ("pro", "ultra"):
            with self.subTest(tier=tier, unconfigured=True), self.assertRaisesRegex(ValidationError, f"GATEWAY_{tier.upper()}_BASE_URL"):
                settings(self.path, model_upstream="gateway", gateway_tier=tier)

    def test_invalid_tier_and_missing_credentials_are_rejected(self):
        """拒绝拼错的档位及缺失共享凭据，避免静默使用默认模型。"""
        for tier in ("turbo", "FAST", ""):
            with self.subTest(tier=tier), self.assertRaises(ValidationError):
                self.gateway_settings(tier)
        for field in ("gateway_api_key", "gateway_key"):
            with self.subTest(field=field), self.assertRaisesRegex(ValidationError, "GATEWAY_API_KEY 或 GATEWAY_KEY"):
                self.gateway_settings(**{field: " "})

    def test_selected_profile_is_loaded_from_environment_file(self):
        """从独立环境文件加载所选档位，验证部署变量映射而非仅检查构造参数。"""
        config_file = self.path / "test.env"
        config_file.write_text(
            "MODEL_UPSTREAM=gateway\nGATEWAY_TIER=ultra\n"
            "GATEWAY_ULTRA_BASE_URL=https://ultra.example.test/custom/v1/\n"
            "GATEWAY_ULTRA_MODEL_NAME=enterprise-ultra\n"
            "GATEWAY_API_KEY=fake-env-secret\nGATEWAY_KEY=fake-env-gateway-key\n",
            encoding="utf-8",
        )
        with patch.dict("os.environ", {}, clear=True):
            config = Settings(_env_file=config_file)
        self.assertEqual(config.gateway_profile.base_url, "https://ultra.example.test/custom/v1")
        self.assertEqual(config.active_model, "enterprise-ultra")
        self.assertEqual(config.gateway_metadata, {"gatewayTier": "ultra"})

    def test_failure_does_not_switch_tier_and_preserves_history(self):
        """pro 失败时只返回该档位错误，绝不重路由 fast 或 ultra。"""
        captured = []

        def handler(request):
            """模拟明确的网关认证失败，不触发 SDK 网络重试。"""
            captured.append(request)
            return httpx.Response(401, json={"error": {"message": "denied test-gateway-secret"}})

        config = self.gateway_settings("pro", mock_analytics_enabled=True)
        app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
        with TestClient(app) as client:
            events = read_events(client, envelope(metadata={"spaid": "user123"}))
        self.assertEqual(len(captured), 1)
        self.assertIn("/routes/pro/v1/", str(captured[0].url))
        error = next(event["value"] for event in events if event.get("name") == "model-inference.error")
        self.assertEqual(error["code"], "UPSTREAM_AUTH_ERROR")
        self.assertNotIn("test-gateway-secret", json.dumps(events))
        record = app.state.history.get("run-1")
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["extensions"]["gatewayTier"], "pro")
        self.assertEqual(record["analysis"]["wouldInsert"]["payload"]["extensions"]["gatewayTier"], "pro")

    def test_direct_ignores_gateway_profiles_and_removes_forged_tier(self):
        """direct 无需网关凭据或有效档位地址，并清除调用方伪造的网关观测字段。"""
        captured = []

        def handler(request):
            """记录 direct 上游，确保未受到网关配置干扰。"""
            captured.append(request)
            return httpx.Response(200, json=completion())

        config = settings(self.path, gateway_tier="ultra", gateway_ultra_base_url="not-configured")
        app = create_app(config, Upstream(config, httpx.MockTransport(handler)))
        with TestClient(app) as client:
            self.assertNotIn("gatewayTier", client.get("/health").json())
            read_events(client, envelope(streaming=False, metadata={"extensions": {"gatewayTier": "forged", "future": 1}}))
        self.assertEqual(json.loads(captured[0].content)["model"], config.direct_model_name)
        self.assertNotIn("x-gateway-key", captured[0].headers)
        self.assertEqual(app.state.history.get("run-1")["extensions"], {"future": 1})

    def test_langsmith_receives_server_tier_and_keeps_it_after_finish(self):
        """真实 RunTree 配合客户端替身，核对档位经过开始与结束追踪后仍保持一致。"""
        client = MagicMock()
        config = self.gateway_settings("ultra", langsmith_enabled=True, langsmith_api_key="fake-trace-secret")
        tracing = TraceRecorder(config, client=client)

        def handler(request):
            """返回非流式模型响应，LangSmith 不访问任何外部服务。"""
            return httpx.Response(200, json=completion())

        app = create_app(config, Upstream(config, httpx.MockTransport(handler)), tracing)
        with TestClient(app) as api:
            read_events(api, envelope(streaming=False, metadata={"spaid": "user123"}))
        client.create_run.assert_called_once()
        client.update_run.assert_called_once()
        created = client.create_run.call_args.kwargs
        updated = client.update_run.call_args.kwargs
        self.assertEqual(created["extra"]["metadata"]["gatewayTier"], "ultra")
        self.assertEqual(updated["extra"]["metadata"]["gatewayTier"], "ultra")
        self.assertEqual(created["extra"]["metadata"]["model"], config.active_model)
        self.assertEqual(updated["extra"]["metadata"]["status"], "completed")


if __name__ == "__main__":
    unittest.main()
