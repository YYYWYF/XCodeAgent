"""通过单个 MockTransport 固定统一客户端的请求和错误合同。"""

import json
import ssl
import unittest
from unittest.mock import Mock, patch

import httpx
from ag_ui.core import CustomEvent, RunErrorEvent, RunFinishedEvent, RunStartedEvent, StateSnapshotEvent
from ag_ui.encoder import EventEncoder

from app.config import Settings
from app.services.builtin_authorization_seed_client import SeedRemoteError
from app.services.builtin_authorization_task_store import input_guard
from app.services.managed_database_client import (
    ManagedDatabaseClient,
    ManagedDatabaseRegistrationError,
    ManagedDatabaseStartupError,
)
from app.services.managed_table_client import ManagedTableError, ManagedTableQueryTransientError


KEY = "12ab34cd56ef"
IDEMPOTENCY_KEY = "a" * 64
PREFIX = "/managed-database/datasources"
RECEIPT = {"operationId": "123", "status": "RUNNING", "purpose": "AUTHORIZATION_SEED_V1"}
MISSING = {"returnCode": "RESOURCE_NOT_FOUND", "errorMsg": "fixture-only", "body": None}


def bee(body, error_message=""):
    """构造普通 Bee 成功包装，默认使用当前空字符串形式。"""

    return {"returnCode": "SUC0000", "errorMsg": error_message, "body": body}


def table_data_events(request, result):
    """使用请求原身份构建完整且一致的 AG-UI 生命周期。"""

    body = json.loads(request.content)
    ids = {"thread_id": body["threadId"], "run_id": body["runId"]}
    return [
        RunStartedEvent(**ids),
        CustomEvent(name="TABLE_DATA_RESULT", value=result),
        StateSnapshotEvent(snapshot={"tableData": result}),
        RunFinishedEvent(**ids, result=result),
    ]


def event_response(events):
    """以标准 AG-UI 编码器构造一次 SSE 响应。"""

    return httpx.Response(200, text="".join(EventEncoder().encode(event) for event in events),
                          headers={"Content-Type": "text/event-stream"})


class ManagedDatabaseUnifiedClientTests(unittest.TestCase):
    """直接测试各具名方法，同时沿用已有业务链测试校验落盘与编排。"""

    def setUp(self):
        """配置单个客户端，所有请求由可切换的本地应答接管。"""

        self.calls = []
        self.reply = None
        self.transport = httpx.MockTransport(self.respond)
        self.client = ManagedDatabaseClient(Settings(
            model_base_url="", model_api_key="", model_name="",
            managed_database_base_url="https://managed.invalid/gateway/",
            managed_database_service_key="test-client-key",
            managed_database_timeout_seconds=4,
        ), transport=self.transport)

    def respond(self, request):
        """捕获请求，再按测试当前配置响应，意外请求立即失败。"""

        self.calls.append(request)
        return self.reply(request)

    def reply_json(self, status, payload):
        """固定单次 JSON Mock 响应。"""

        self.reply = lambda _: httpx.Response(status, json=payload)

    def test_all_endpoints_keep_original_wire_contract(self):
        """同一实例访问所有路径，逐项核对请求字节、身份头及连接设置。"""

        registration = {"datasourceKey": KEY, "datasourceId": 2, "status": "REGISTERED"}
        seed_payload = {"z": "权限", "idempotencyKey": IDEMPOTENCY_KEY, "a": 1}
        seed_bytes = ('{"a":1,"idempotencyKey":"' + IDEMPOTENCY_KEY + '","z":"权限"}').encode()
        query = {"pageNumber": 1, "pageSize": 20, "filter": {"logic": "AND", "conditions": []}}
        read_suffix = f"/{KEY}"
        detail = {**RECEIPT, "datasourceKey": KEY, "requestFingerprint": "sha256:" + "b" * 64,
                  "tableNames": ["a", "b", "c", "d"], "createdCount": 0, "existingCount": 0,
                  "verified": False, "errorCode": None}
        cases = [
            (lambda: self.client.register("订单"), "POST", "", '{"appName":"订单"}'.encode(), {**registration, "isDeleted": False}, registration),
            (lambda: self.client.submit_table(KEY, mode="CREATE", sql="DDL"), "POST", read_suffix + "/table-operations",
             b'{"mode":"CREATE","sql":"DDL"}', RECEIPT, RECEIPT),
            (lambda: self.client.submit_table(KEY, mode="REBUILD", sql="DDL", allow_data_loss=True), "POST",
             read_suffix + "/table-operations", b'{"mode":"REBUILD","sql":"DDL","allowDataLoss":true}', RECEIPT, RECEIPT),
            (lambda: self.client.batch_create_tables(KEY, ["b", "a"]), "POST", read_suffix + "/table-operations/batch",
             b'{"mode":"CREATE","sqls":["b","a"]}', RECEIPT, RECEIPT),
            (lambda: self.client.get_table_operation(KEY, "123"), "GET", read_suffix + "/table-operations/123", b"", {}, {}),
            (lambda: self.client.list_table_operations(KEY), "GET", read_suffix + "/table-operations?limit=100", b"", [], []),
            (lambda: self.client.check_tables(KEY, ["DDL"]), "POST", read_suffix + "/tables/check", b'{"sqls":["DDL"]}', {}, {}),
            (lambda: self.client.list_tables(KEY), "GET", read_suffix + "/tables", b"", {}, {}),
            (lambda: self.client.get_table_columns(KEY, "a/表"), "GET", read_suffix + "/tables/a%2F%E8%A1%A8/columns", b"", {}, {}),
            (lambda: self.client.check_runtime_tables(KEY, ("DDL",)), "POST", read_suffix + "/tables/check", b'{"sqls":["DDL"]}', {}, {}),
            (lambda: self.client.get_runtime_connection(KEY), "GET", read_suffix + "/runtime-connection", b"", {}, {}),
            (lambda: self.client.submit_authorization_seed(KEY, seed_payload), "POST",
             read_suffix + "/data-operations/batch", seed_bytes, RECEIPT, RECEIPT),
            (lambda: self.client.get_seed_operation(KEY, "123", IDEMPOTENCY_KEY), "GET",
             read_suffix + "/data-operations/123", b"", detail, detail),
            (lambda: self.client.get_seed_by_key(KEY, IDEMPOTENCY_KEY), "GET",
             read_suffix + "/data-operations/by-key/" + IDEMPOTENCY_KEY, b"", detail, detail),
            (lambda: self.client.query_table_data(KEY, "a/表", query), "POST",
             read_suffix + "/tables/a%2F%E8%A1%A8/data/run", None, {"rows": []}, {"rows": []}),
        ]
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        with patch("app.services.managed_database_client.httpx.Client", wraps=httpx.Client) as factory, \
             patch("app.services.managed_database_client.build_internal_service_ssl_context", return_value=tls) as internal_tls, \
             patch("app.services.user_context.current_user", return_value=("a/b", "张 三")) as actor:
            for call, method, path, request_bytes, body, expected in cases:
                with self.subTest(method=method, path=path, request=request_bytes):
                    if request_bytes is None:
                        self.reply = lambda request: event_response(table_data_events(request, body))
                    else:
                        self.reply_json(200, bee(body))
                    before = len(self.calls)
                    result = call()
                    self.assertEqual(result.model_dump() if hasattr(result, "model_dump") else result, expected)
                    self.assertEqual(len(self.calls), before + 1)
                    request = self.calls[-1]
                    self.assertEqual(request.method, method)
                    self.assertEqual(str(request.url), "https://managed.invalid/gateway" + PREFIX + path)
                    self.assertEqual(request.headers["Authorization"], "Bearer test-client-key")
                    self.assertEqual(request.headers["Accept"], "text/event-stream" if request_bytes is None else "application/json")
                    default_tls = "/data-operations/" in path or request_bytes is None
                    if default_tls:
                        self.assertNotIn("X-Managed-User-Id", request.headers)
                        self.assertNotIn("X-Managed-User-Name", request.headers)
                    else:
                        self.assertEqual(request.headers["X-Managed-User-Id"], "a%2Fb")
                        self.assertEqual(request.headers["X-Managed-User-Name"], "%E5%BC%A0%20%E4%B8%89")
                    if method == "POST":
                        self.assertEqual(request.headers["Content-Type"], "application/json")
                    options = factory.call_args.kwargs
                    self.assertIs(options["transport"], self.transport)
                    self.assertEqual(options["timeout"], 4)
                    self.assertIs(options["follow_redirects"], False)
                    self.assertIs(options["trust_env"], False)
                    if default_tls:
                        self.assertNotIn("verify", options)
                    else:
                        self.assertIs(options["verify"], tls)
                    if request_bytes is None:
                        payload = json.loads(request.content)
                        self.assertNotEqual(payload["runId"], payload["threadId"])
                        self.assertEqual(payload["forwardedProps"], {"action": "QUERY", "query": query})
                    else:
                        self.assertEqual(request.content, request_bytes)
            self.assertEqual(factory.call_count, len(cases))
            self.assertEqual(actor.call_count, 11)
            self.assertEqual(internal_tls.call_count, 11)

    def test_seed_http_200_is_the_only_success_status(self):
        """F01 核对 200 受理与 202 拒绝，每次仅发出一条 POST。"""

        self.reply_json(200, bee(RECEIPT))
        self.assertEqual(self.client.submit_authorization_seed(KEY, {}).status, "RUNNING")
        self.reply_json(202, bee(RECEIPT))
        with self.assertRaises(SeedRemoteError) as caught:
            self.client.submit_authorization_seed(KEY, {})
        self.assertEqual(caught.exception.category, "seed_http_202")
        self.assertEqual([request.method for request in self.calls], ["POST", "POST"])

    def test_table_409_maps_exact_issue(self):
        """F02 从 HTTP 和 Bee 码还原既有表操作异常。"""

        self.reply_json(409, {"returnCode": "STRUCTURE_MISMATCH", "errorMsg": "fixture-only", "body": {}})
        with self.assertRaises(ManagedTableError) as caught:
            self.client.submit_table(KEY, mode="CREATE", sql="private-ddl")
        self.assertEqual(caught.exception.issue["code"], "STRUCTURE_MISMATCH")
        self.assertEqual(caught.exception.issue["action"], "rebuild")
        self.assertFalse(caught.exception.uncertain)
        self.assertNotIn("private-ddl", str(caught.exception))
        self.assertEqual(len(self.calls), 1)

    def test_non_json_table_response_and_guarded_read_failure(self):
        """F03 非 JSON 错误按读写路径分类，受管只读仍可恢复。"""

        self.reply = lambda _: httpx.Response(503, text="private-message")
        guard = Mock()
        token = input_guard.set(guard)
        try:
            for read in (lambda: self.client.check_tables(KEY, ["DDL"]), lambda: self.client.list_table_operations(KEY)):
                with self.assertRaisesRegex(ManagedTableQueryTransientError, "暂时不可用"):
                    read()
            with self.assertRaises(ManagedTableError) as caught:
                self.client.batch_create_tables(KEY, ["DDL"])
            self.assertEqual(caught.exception.issue["code"], "RESULT_UNCONFIRMED")
            self.assertTrue(caught.exception.uncertain)
            self.assertEqual(guard.call_count, 3)
        finally:
            input_guard.reset(token)
        self.reply = lambda _: httpx.Response(200, text="invalid JSON")
        with self.assertRaises(ManagedDatabaseRegistrationError) as invalid:
            self.client.check_tables(KEY, [])
        self.assertTrue(invalid.exception.uncertain)
        self.assertIs(type(invalid.exception), ManagedDatabaseRegistrationError)
        self.assertEqual(len(self.calls), 4)

    def test_non_bee_and_non_success_bodies_are_rejected(self):
        """F04 注册、表历史和 Runtime 不能接收裸 JSON 或失败码的业务体。"""

        resource = {"datasourceKey": KEY, "datasourceId": 2, "status": "REGISTERED", "isDeleted": False}
        for payload in (resource, {**bee(resource), "returnCode": "RESOURCE_BUSY"}, {**bee(resource), "extra": True}):
            self.reply_json(200, payload)
            with self.subTest(payload=payload):
                with self.assertRaises(ManagedDatabaseRegistrationError) as registration:
                    self.client.register("测试")
                self.assertTrue(registration.exception.uncertain)
                with self.assertRaises(ManagedDatabaseRegistrationError) as history:
                    self.client.list_table_operations(KEY)
                self.assertFalse(history.exception.uncertain)
                with self.assertRaisesRegex(ManagedDatabaseStartupError, "结果无效"):
                    self.client.get_runtime_connection(KEY)
        self.assertEqual(len(self.calls), 9)

    def test_runtime_reads_bee_error_code(self):
        """F05 错误码来自 returnCode，仍按原提示和异常分类。"""

        for status, code, expected in (
            (409, "RESOURCE_BUSY", "未完成操作"),
            (404, "RUNTIME_CONNECTION_NOT_CONFIGURED", "尚未配置"),
            (403, "RESOURCE_BUSY", "鉴权失败"),
        ):
            with self.subTest(status=status, code=code):
                self.reply_json(status, {"returnCode": code, "errorMsg": "private-error", "body": None})
                with self.assertRaisesRegex(ManagedDatabaseStartupError, expected) as caught:
                    self.client.get_runtime_connection(KEY)
                self.assertNotIn("private-error", str(caught.exception))
        self.assertEqual(len(self.calls), 3)

    def test_empty_bee_error_text_requires_success_code(self):
        """F06 只允许成功包装携带空字符串或空值。"""

        for empty in ("", None):
            self.reply_json(200, bee([], empty))
            self.assertEqual(self.client.list_table_operations(KEY), [])
            self.reply_json(200, bee(RECEIPT, empty))
            self.assertEqual(self.client.submit_authorization_seed(KEY, {}).operationId, "123")
        for invalid in ("problem", False, []):
            self.reply_json(200, bee(RECEIPT, invalid))
            with self.assertRaises(SeedRemoteError) as caught:
                self.client.submit_authorization_seed(KEY, {})
            self.assertEqual(caught.exception.category, "seed_invalid_response")
        self.reply_json(200, {**bee(RECEIPT), "returnCode": "RESOURCE_BUSY"})
        with self.assertRaises(SeedRemoteError) as caught:
            self.client.submit_authorization_seed(KEY, {})
        self.assertEqual(caught.exception.category, "seed_invalid_response")

    def test_registration_transport_certainty(self):
        """连接失败与请求送出后失败保留确定性，且没有隐式重试。"""

        for error, uncertain in ((httpx.ConnectError, False), (httpx.ConnectTimeout, False),
                                 (httpx.ReadTimeout, True), (httpx.WriteError, True)):
            with self.subTest(error=error):
                def fail(request):
                    """在 MockTransport 上模拟指定网络异常。"""

                    raise error("private-network-error", request=request)

                self.reply = fail
                with self.assertRaises(ManagedDatabaseRegistrationError) as caught:
                    self.client.register("测试")
                self.assertIs(caught.exception.uncertain, uncertain)
                self.assertNotIn("private-network-error", str(caught.exception))
        self.assertEqual([request.method for request in self.calls], ["POST"] * 4)

    def test_seed_conflict_missing_and_unknown(self):
        """只有明确的 Seed 查无记录返回空，冲突与模糊结果保留原分类。"""

        for code, category in (("RESOURCE_BUSY", "seed_resource_busy"),
                               ("IDEMPOTENCY_CONFLICT", "seed_idempotency_conflict")):
            self.reply_json(409, {**MISSING, "returnCode": code})
            with self.assertRaises(SeedRemoteError) as error:
                self.client.submit_authorization_seed(KEY, {})
            self.assertEqual(error.exception.category, category)
        for body in (None, {}):
            self.reply_json(404, {**MISSING, "body": body})
            self.assertIsNone(self.client.get_seed_by_key(KEY, IDEMPOTENCY_KEY))
        for response in ({**MISSING, "body": {"found": False}}, {**MISSING, "returnCode": "OTHER"}):
            self.reply_json(404, response)
            with self.assertRaises(SeedRemoteError) as error:
                self.client.get_seed_operation(KEY, "123", IDEMPOTENCY_KEY)
            self.assertEqual(error.exception.category, "seed_http_404")
        self.assertEqual([request.method for request in self.calls], ["POST", "POST"] + ["GET"] * 4)

    def test_sse_errors_terminal_and_size_limits(self):
        """RUN_ERROR、重复终态和两个大小上限均拒绝已有候选数据。"""

        for scenario, expected in (("run-error", "未正常结束"), ("finished-twice", "结束后仍返回"),
                                   ("event-limit", "响应过大"), ("bytes-limit", "响应过大")):
            with self.subTest(scenario=scenario):
                def reply(request):
                    """在有效开始之后构造单个协议违规场景。"""

                    events = table_data_events(request, {})
                    if scenario == "run-error":
                        events[1] = RunErrorEvent(message="private-event")
                    elif scenario == "finished-twice":
                        events.append(events[-1])
                    elif scenario == "event-limit":
                        events[1:1] = [CustomEvent(name="extra", value={})] * 125
                    else:
                        events[1] = CustomEvent(name="extra", value="x" * (12 * 1024 * 1024))
                    return event_response(events)

                self.reply = reply
                with self.assertRaisesRegex(ValueError, expected) as caught:
                    self.client.query_table_data(KEY, "orders", {})
                self.assertNotIn("private-event", str(caught.exception))
        self.assertEqual(len(self.calls), 4)
