"""独立数据源 AG-UI 路由的当前契约测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routes.data_sources import data_sources_router
from app.protocols.data_sources import data_sources_capabilities
from app.services.data_sources import public_catalog


class DataSourceRouteTests(unittest.TestCase):
    """覆盖六个固定动作端点的注册、生命周期和边界行为。"""

    def setUp(self) -> None:
        """为每个测试创建隔离工作区和最小 FastAPI 应用。"""

        self.temporary_directory = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary_directory.name)
        self.app = FastAPI()
        self.app.include_router(data_sources_router)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        """清理测试工作区。"""

        self.temporary_directory.cleanup()

    def payload(self, **data: object) -> dict[str, object]:
        """构造符合 AG-UI forwardedProps 约定的请求体。"""

        return {
            "threadId": "thread-data-source-test",
            "runId": "run-data-source-test",
            "forwardedProps": {
                "dataSources": {"workspaceRoot": str(self.workspace), **data}
            },
        }

    def test_registers_six_action_endpoints_without_run_or_patch(self) -> None:
        """路由应只暴露六个明确动作，不再暴露宽泛 run 或 PATCH。"""

        routes = {
            (path, method.upper())
            for path, operations in self.app.openapi()["paths"].items()
            for method in operations
        }
        for action in ("list", "create", "update", "delete", "validate", "detail"):
            self.assertIn((f"/data-sources/{action}", "POST"), routes)
        self.assertNotIn(("/data-sources/run", "POST"), routes)
        self.assertFalse(
            any(path.startswith("/data-sources") and method == "PATCH" for path, method in routes)
        )
        self.assertEqual(data_sources_capabilities()["stateDirectory"], ".xcodeagent/datasource")

    def test_list_returns_streaming_ag_ui_lifecycle(self) -> None:
        """list 端点应返回标准 AG-UI SSE 生命周期。"""

        response = self.client.post("/data-sources/list", json=self.payload())

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/event-stream"))
        for event_type in (
            "RUN_STARTED",
            "TEXT_MESSAGE_START",
            "CUSTOM",
            "STATE_SNAPSHOT",
            "TEXT_MESSAGE_CONTENT",
            "TEXT_MESSAGE_END",
            "RUN_FINISHED",
        ):
            self.assertIn(event_type, response.text)
        self.assertIn('"action":"list"', response.text)

    def test_create_requires_source_and_cannot_be_overridden_by_body_action(self) -> None:
        """create 缺少 source 时失败，且请求体不能覆盖路由动作。"""

        missing_source = self.client.post("/data-sources/create", json=self.payload())
        self.assertIn('"status":"failed"', missing_source.text)
        self.assertIn('"action":"create"', missing_source.text)

        overridden = self.client.post(
            "/data-sources/list",
            json=self.payload(action="delete", sourceId="unexpected"),
        )
        self.assertIn('"status":"failed"', overridden.text)
        self.assertIn('"action":"list"', overridden.text)

    def test_field_types_detail_and_failed_updates_keep_ag_ui_lifecycle(self) -> None:
        """类型字段经由详情完整返回，参数或类型校验失败也返回完整 AG-UI 生命周期。"""
        source = {
            "id": "domain-typed", "type": "external_api", "name": "类型域名", "baseUrl": "api.example.com",
            "directories": [{"id": "directory-typed", "name": "业务", "operations": [
                {
                    "id": "op-one", "name": "一", "method": "GET", "path": "/one/{id}",
                    "pathParameters": [{"name": "id", "type": "integer", "required": True}],
                    "queryParameters": [{"name": "keyword", "type": "string", "required": False}],
                    "requestSample": {"id": 0}, "responseSample": {"id": "0"},
                    "requestFieldTypes": {'$["id"]': "number"}, "responseFieldTypes": {'$["id"]': "string"},
                },
                {
                    "id": "op-two", "name": "二", "method": "GET", "path": "/two/{id}",
                    "pathParameters": [{"name": "id", "required": True}], "queryParameters": [],
                },
            ]}],
        }
        created = self.client.post("/data-sources/create", json=self.payload(source=source))
        self.assertIn('"status":"completed"', created.text)
        detail = self.client.post("/data-sources/detail", json=self.payload(sourceId="domain-typed", operationId="op-one"))
        self.assertIn('"status":"completed"', detail.text)
        self.assertTrue(detail.headers["content-type"].startswith("text/event-stream"))
        self.assertIn('"pathParameters":[', detail.text)
        self.assertIn('"queryParameters":[', detail.text)
        self.assertNotIn('"parameters":', detail.text)
        self.assertNotIn('"location":', detail.text)
        operation = source["directories"][0]["operations"][0]
        for field in ("requestFieldTypes", "responseFieldTypes"):
            self.assertIn(f'"{field}":' + json.dumps(operation[field], separators=(",", ":")), detail.text)
        invalid = json.loads(json.dumps(source))
        invalid["directories"][0]["operations"][0]["pathParameters"][0]["required"] = False
        failed = self.client.post("/data-sources/update", json=self.payload(source=invalid))
        self.assertIn('"status":"failed"', failed.text)
        self.assertIn("Path", failed.text)
        invalid["directories"][0]["operations"][0]["pathParameters"][0]["required"] = True
        invalid["directories"][0]["operations"][0]["requestFieldTypes"] = {'$["id"]': "date"}
        invalid_type = self.client.post("/data-sources/update", json=self.payload(source=invalid))
        self.assertIn('"status":"failed"', invalid_type.text)
        for response in (detail, failed, invalid_type):
            for event in ("RUN_STARTED", "TEXT_MESSAGE_START", "TEXT_MESSAGE_CONTENT", "TEXT_MESSAGE_END", "CUSTOM", "STATE_SNAPSHOT", "RUN_FINISHED"):
                self.assertIn(event, response.text)
        still_saved = public_catalog(self.workspace, source_id="domain-typed", operation_id="op-one")
        self.assertEqual(still_saved.sources[0].directories[1].operations[0].request_field_types, {'$["id"]': "number"})

    def test_create_update_delete_and_validate_use_separate_endpoints(self) -> None:
        """创建、更新、删除和校验应通过各自端点完成。"""

        source = {
            "type": "external_api",
            "name": "示例 API",
            "baseUrl": "api.example.com",
            "directories": [{"name": "目录", "operations": [{"name": "列表", "method": "GET", "path": "/items"}]}],
        }
        created = self.client.post(
            "/data-sources/create", json=self.payload(source=source)
        )
        self.assertIn('"action":"create"', created.text)
        created_source = public_catalog(self.workspace).sources[0]
        source_id = created_source.id
        self.assertEqual(created_source.directories[0].name, "默认目录")

        update_source = {**source, "id": source_id, "name": "更新 API"}
        updated = self.client.post(
            "/data-sources/update", json=self.payload(source=update_source)
        )
        self.assertIn('"action":"update"', updated.text)

        validated = self.client.post(
            "/data-sources/validate", json=self.payload(source=source)
        )
        self.assertIn('"action":"validate"', validated.text)

        detailed = self.client.post(
            "/data-sources/detail",
            json=self.payload(sourceId=source_id, operationId=created_source.directories[1].operations[0].id),
        )
        self.assertIn('"action":"detail"', detailed.text)
        self.assertIn('"path":"/items"', detailed.text)

        deleted = self.client.post(
            "/data-sources/delete", json=self.payload(sourceId=source_id)
        )
        self.assertIn('"action":"delete"', deleted.text)


if __name__ == "__main__":
    unittest.main()
