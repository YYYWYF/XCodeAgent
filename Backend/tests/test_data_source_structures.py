"""请求体与响应体完整结构的生成、保存及详情回填测试。"""

import json
import tempfile
import unittest
from pathlib import Path

from app.services.data_source_json_fields import build_json_structure
from app.services.data_sources import DataSourceError, mutate_catalog, public_catalog


class DataSourceStructureTests(unittest.TestCase):
    """验证结构只写入接口文件，并与最终样例和字段元数据保持一致。"""

    def setUp(self) -> None:
        """创建隔离工作区，测试不访问运行中的用户应用。"""
        self.temporary = tempfile.TemporaryDirectory()
        self.workspace = Path(self.temporary.name)

    def tearDown(self) -> None:
        """清理本测试的临时目录。"""
        self.temporary.cleanup()

    def source(self) -> dict:
        """构造请求和响应同路径但类型与说明不同的接口。"""
        return {
            "id": "domain-structure", "type": "external_api", "name": "结构测试", "baseUrl": "api.example.com",
            "directories": [{"id": "directory-structure", "name": "默认目录", "operations": [{
                "id": "operation-structure", "name": "查询", "method": "GET", "path": "/items",
                "requestSample": {"id": 0}, "responseSample": {"id": "1"},
                "requestStructure": {"type": "object", "properties": {"id": {"type": "number", "description": "请求编号"}}},
                "responseStructure": {"type": "object", "properties": {"id": {"type": "string", "description": "响应编号"}}},
            }]}],
        }

    def test_round_trip_preserves_effective_types_descriptions_and_light_index(self) -> None:
        """结构包含最终类型与说明，落盘后详情回填，索引不包含结构。"""
        created = mutate_catalog(self.workspace, action="create", source=self.source())
        operation = created.sources[0].directories[0].operations[0]
        request_field = operation.request_structure.properties["id"]
        response_field = operation.response_structure.properties["id"]
        self.assertEqual((request_field.type, request_field.description), ("number", "请求编号"))
        self.assertEqual((response_field.type, response_field.description), ("string", "响应编号"))
        directory = self.workspace / ".xcodeagent" / "datasource"
        operation_file = directory / "external-apis" / "domain-structure" / "operations" / "operation-structure.json"
        persisted = json.loads(operation_file.read_text("utf-8"))
        self.assertEqual(persisted["requestStructure"], operation.request_structure.model_dump())
        self.assertEqual(persisted["responseStructure"], operation.response_structure.model_dump())
        detail = public_catalog(self.workspace, source_id="domain-structure", operation_id="operation-structure")
        self.assertEqual(detail.sources[0].directories[0].operations[0].request_structure, operation.request_structure)
        index = json.loads((directory / "index.json").read_text("utf-8"))
        self.assertEqual(set(index["sources"][0]["directories"][0]["operations"][0]), {"id", "name", "method", "path"})

    def test_save_rebuilds_structure_and_failed_save_preserves_saved_tree(self) -> None:
        """修改样例后服务端重建旧结构，保存失败不改变已保存结构。"""
        source = mutate_catalog(self.workspace, action="create", source=self.source()).sources[0].model_dump(by_alias=True)
        operation = source["directories"][0]["operations"][0]
        operation["requestSample"] = {"enabled": True}
        operation["responseSample"] = None
        updated = mutate_catalog(self.workspace, action="update", source=source).sources[0].directories[0].operations[0]
        self.assertEqual(set(updated.request_structure.properties), {"enabled"})
        self.assertEqual(updated.request_structure.properties["enabled"].type, "boolean")
        self.assertIsNone(updated.request_structure.properties["enabled"].description)
        self.assertIsNone(updated.response_structure)
        operation["method"] = "PATCH"
        with self.assertRaises(ValueError):
            mutate_catalog(self.workspace, action="update", source=source)
        self.assertEqual(public_catalog(self.workspace, source_id="domain-structure").sources[0].directories[0].operations[0].request_structure, updated.request_structure)

    def test_full_structure_includes_late_array_fields_and_deep_wide_fields(self) -> None:
        """完整结构不受前端数组、节点、深度预览限制，特殊字段名使用安全路径。"""
        deep = {"leaf": True}
        for _ in range(12):
            deep = {"child": deep}
        sample = {"items": [{"id": value} for value in range(35)], "deep": deep, "wide": {f"f-{i}": i for i in range(350)}, "a.b": {"q\"[]": None}}
        sample["items"][-1]["id"] = "last"
        sample["items"][-1]["late"] = [False]
        source = self.source()
        operation = source["directories"][0]["operations"][0]
        operation["requestSample"] = sample
        operation["requestStructure"] = {"type": "object", "properties": {"items": {"type": "array", "items": {"type": "object", "properties": {"late": {"type": "array", "description": "后部字段"}}}}}}
        saved = mutate_catalog(self.workspace, action="create", source=source).sources[0].directories[0].operations[0].request_structure
        item = saved.properties["items"].items
        self.assertEqual(item.properties["id"].type, ["integer", "string"])
        self.assertEqual(item.properties["late"].description, "后部字段")
        self.assertEqual(item.properties["late"].items.type, "boolean")
        deep_node = saved.properties["deep"]
        for _ in range(12):
            deep_node = deep_node.properties["child"]
        self.assertEqual(deep_node.properties["leaf"].type, "boolean")
        self.assertEqual(len(saved.properties["wide"].properties), 350)
        self.assertEqual(saved.properties["a.b"].properties['q"[]'].type, "null")

    def test_empty_and_primitive_samples(self) -> None:
        """无样例或 null 没有结构，空容器和基础值仍有真实根结构。"""
        self.assertIsNone(build_json_structure(None))
        for sample, field_type in (({}, "object"), ([], "array"), (False, "boolean"), (0, "integer"), (1.25, "number"), ("", "string")):
            with self.subTest(sample=sample):
                expected = {"type": field_type}
                if field_type == "object":
                    expected["properties"] = {}
                self.assertEqual(build_json_structure(sample), expected)
        source = self.source()
        operation = source["directories"][0]["operations"][0]
        operation.pop("requestSample")
        operation["responseSample"] = None
        saved = mutate_catalog(self.workspace, action="create", source=source).sources[0].directories[0].operations[0]
        self.assertIsNone(saved.request_structure)
        self.assertIsNone(saved.response_structure)

    def test_stored_structure_mismatch_is_not_silently_rebuilt(self) -> None:
        """读取时不修改磁盘结构，损坏或不一致内容明确报错。"""
        mutate_catalog(self.workspace, action="create", source=self.source())
        operation_file = self.workspace / ".xcodeagent" / "datasource" / "external-apis" / "domain-structure" / "operations" / "operation-structure.json"
        persisted = json.loads(operation_file.read_text("utf-8"))
        persisted["requestStructure"]["properties"]["id"]["type"] = "boolean"
        operation_file.write_text(json.dumps(persisted), encoding="utf-8")
        with self.assertRaisesRegex(DataSourceError, "结构与样例"):
            public_catalog(self.workspace, source_id="domain-structure", operation_id="operation-structure")


if __name__ == "__main__":
    unittest.main()
