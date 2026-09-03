"""独立数据源参数与 JSON 字段类型的保存和校验测试。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.data_source_json_fields import (
    DataSourceJsonFieldError,
    matches_field_type,
    normalize_field_types,
)
from app.services.data_sources import DataSourceError, mutate_catalog, public_catalog, validate_source


class DataSourceFieldTypeTests(unittest.TestCase):
    """覆盖类型声明、参数位置与分文件存储的完整往返。"""

    def setUp(self) -> None:
        """准备隔离工作区。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        """释放测试工作区。"""
        self.temporary.cleanup()

    def source(self, **operation_changes: object) -> dict:
        """构造带有独立请求和响应类型声明的完整域名。"""
        operation = {
            "id": "operation-typed", "name": "查询", "method": "GET", "path": "/items/{id}",
            "pathParameters": [{"name": "id", "type": "integer", "required": True}],
            "queryParameters": [{"name": "id", "type": "string", "required": False}],
            "requestSample": {"count": 0, "items": [{"id": 1}]},
            "responseSample": {"count": "0"},
            "requestFieldDescriptions": {'$["count"]': "请求数量"},
            "responseFieldDescriptions": {'$["count"]': "响应数量"},
            "requestFieldTypes": {'$["count"]': "number", '$["items"][]["id"]': "integer"},
            "responseFieldTypes": {'$["count"]': "string"},
            **operation_changes,
        }
        return {
            "type": "external_api", "id": "typed-domain", "name": "类型 API", "baseUrl": "api.example.com",
            "directories": [{"id": "typed-directory", "name": "业务", "operations": [operation]}],
        }

    def test_types_round_trip_and_index_stays_minimal(self) -> None:
        """完整类型写入接口文件，详情正确回填，索引只有四字段接口摘要。"""
        created = mutate_catalog(self.workspace, action="create", source=self.source())
        source = created.sources[0]
        detail = public_catalog(self.workspace, source_id=source.id, operation_id="operation-typed")
        operation = detail.sources[0].directories[1].operations[0]
        self.assertEqual(operation.path_parameters[0].type, "integer")
        self.assertEqual(operation.query_parameters[0].type, "string")
        self.assertEqual(operation.request_field_types['$["count"]'], "number")
        self.assertEqual(operation.response_field_types['$["count"]'], "string")
        root = self.workspace / ".xcodeagent" / "datasource"
        persisted = json.loads((root / "external-apis" / source.id / "operations" / "operation-typed.json").read_text("utf-8"))
        self.assertEqual(persisted["requestFieldTypes"], self.source()["directories"][0]["operations"][0]["requestFieldTypes"])
        self.assertEqual(persisted["pathParameters"][0]["type"], "integer")
        self.assertEqual(persisted["queryParameters"][0]["type"], "string")
        self.assertNotIn("parameters", persisted)
        for parameter in persisted["pathParameters"] + persisted["queryParameters"]:
            self.assertEqual(set(parameter), {"name", "type", "required", "description"})
        candidate = source.model_dump(by_alias=True)
        candidate["directories"][1]["operations"][0]["queryParameters"][0]["description"] = "查询编号"
        mutate_catalog(self.workspace, action="update", source=candidate)
        updated = public_catalog(self.workspace, source_id=source.id, operation_id="operation-typed").sources[0].directories[1].operations[0]
        self.assertEqual(updated.path_parameters[0].type, "integer")
        self.assertEqual(updated.query_parameters[0].description, "查询编号")
        index = json.loads((root / "index.json").read_text("utf-8"))
        self.assertEqual(set(index["sources"][0]["directories"][1]["operations"][0]), {"id", "name", "method", "path"})

    def test_parameter_defaults_query_types_and_path_rules(self) -> None:
        """Query 接受通用类型，Path 必填、类型和占位符在两个方向都受约束。"""
        for field_type in ("string", "integer", "number", "boolean", "object", "array", "null"):
            with self.subTest(query_type=field_type):
                source = self.source(path="/items", pathParameters=[], queryParameters=[{"name": "q", "type": field_type}])
                self.assertTrue(validate_source(source)["valid"])
        default = mutate_catalog(self.workspace, action="create", source=self.source(path="/items", pathParameters=[], queryParameters=[{"name": "q"}]))
        self.assertEqual(default.sources[0].directories[1].operations[0].query_parameters[0].type, "string")
        invalid_cases = [
            ("/items/{id}", [{"name": "id", "type": "integer", "required": False}], [], "必填"),
            ("/items/{id}", [{"name": "id", "type": "array", "required": True}], [], "类型"),
            ("/items", [{"name": "id", "required": True}], [], "一一对应"),
            ("/items/{id}", [], [], "一一对应"),
            ("/items/{}", [], [], "一一对应"),
            ("/items", [], [{"name": "q"}, {"name": "Q"}], "重复"),
            ("/items/{id}", [{"name": "id", "required": True}, {"name": "ID", "required": True}], [], "一一对应"),
        ]
        for path, path_parameters, query_parameters, error in invalid_cases:
            with self.subTest(path=path, path_parameters=path_parameters, query_parameters=query_parameters):
                with self.assertRaisesRegex(DataSourceError, error):
                    validate_source(self.source(path=path, pathParameters=path_parameters, queryParameters=query_parameters))
        with self.assertRaises(ValueError):
            validate_source(self.source(path="/items", pathParameters=[], queryParameters=[{"name": "q", "type": "date"}]))
        with self.assertRaisesRegex(DataSourceError, "合计"):
            validate_source(self.source(queryParameters=[{"name": f"q-{index}"} for index in range(50)]))

    def test_parameter_groups_reject_non_contract_fields(self) -> None:
        """只接受当前两个参数数组，不接收统一参数字段或重复的位置字段。"""
        for changes in (
            {"parameters": []},
            {"pathParameters": [{"name": "id", "required": True, "location": "path"}]},
            {"queryParameters": [{"name": "q", "location": "query"}]},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    mutate_catalog(self.workspace, action="create", source=self.source(**changes))
                self.assertEqual(public_catalog(self.workspace).sources, [])

    def test_save_cleans_removed_and_incompatible_types_without_changing_samples(self) -> None:
        """修改样例后只保留仍匹配的声明，清理也不会改变原样例。"""
        created = mutate_catalog(self.workspace, action="create", source=self.source())
        source = created.sources[0].model_dump(by_alias=True)
        operation = source["directories"][1]["operations"][0]
        operation["requestSample"] = {"count": "changed", "items": [{"id": 1}, {"id": "mixed"}]}
        operation["requestFieldTypes"]['$["removed"]'] = "array"
        updated = mutate_catalog(self.workspace, action="update", source=source)
        updated_operation = updated.sources[0].directories[1].operations[0]
        self.assertEqual(updated_operation.request_field_types, {})
        self.assertEqual(updated_operation.request_sample, operation["requestSample"])
        self.assertEqual(updated_operation.request_field_descriptions, {'$["count"]': "请求数量"})
        self.assertEqual(updated_operation.response_field_types, {'$["count"]': "string"})
        operation["responseSample"] = None
        cleared = mutate_catalog(self.workspace, action="update", source=source).sources[0].directories[1].operations[0]
        self.assertEqual(cleared.response_field_types, {})
        self.assertEqual(cleared.response_field_descriptions, {})

    def test_complete_paths_and_boolean_numeric_distinction(self) -> None:
        """字段类型使用完整深层样例并正确处理特殊键和布尔值。"""
        sample = {"items": [{"id": index} for index in range(35)], "a.b": {"q\"[]": 2}}
        field_types = {'$["items"][]["id"]': "integer", '$["a.b"]["q\\\"[]"]': "number"}
        self.assertEqual(normalize_field_types(sample, field_types), field_types)
        sample["items"][34]["id"] = "not integer"
        self.assertNotIn('$["items"][]["id"]', normalize_field_types(sample, field_types))
        deep_sample = {"id": 4}
        for _ in range(12):
            deep_sample = {"child": deep_sample}
        deep_path = '$' + '["child"]' * 12 + '["id"]'
        self.assertEqual(normalize_field_types(deep_sample, {deep_path: "integer"}), {deep_path: "integer"})
        self.assertFalse(matches_field_type(True, "integer"))
        self.assertFalse(matches_field_type(False, "number"))
        self.assertFalse(matches_field_type(9007199254740992, "integer"))
        self.assertTrue(matches_field_type(1, "number"))
        self.assertTrue(matches_field_type(1.0, "integer"))

    def test_invalid_mappings_fail_before_write_and_stored_corruption_is_reported(self) -> None:
        """非法映射写入前失败，损坏的已保存声明读取时不静默丢弃。"""
        for field_types in ([], {'$': "date"}, {'x' * 4097: "string"}, {str(index): "string" for index in range(1001)}):
            with self.subTest(mapping_type=type(field_types).__name__):
                with self.assertRaises(DataSourceJsonFieldError):
                    normalize_field_types(None, field_types)
        created = mutate_catalog(self.workspace, action="create", source=self.source())
        operation_path = self.workspace / ".xcodeagent" / "datasource" / "external-apis" / created.sources[0].id / "operations" / "operation-typed.json"
        before = operation_path.read_bytes()
        with self.assertRaises(ValueError):
            mutate_catalog(self.workspace, action="update", source=self.source(requestFieldTypes={'$["count"]': "invalid"}))
        self.assertEqual(operation_path.read_bytes(), before)
        persisted = json.loads(before)
        persisted["requestFieldTypes"] = {'$["count"]': "string"}
        operation_path.write_text(json.dumps(persisted), encoding="utf-8")
        with self.assertRaisesRegex(DataSourceError, "类型与样例不一致"):
            public_catalog(self.workspace, source_id=created.sources[0].id, operation_id="operation-typed")


if __name__ == "__main__":
    unittest.main()
