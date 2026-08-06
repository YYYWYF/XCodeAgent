from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.data_source_policy import (
    DataSourcePolicyError,
    apply_authoritative_datasource_type,
    datasource_type_from_artifact,
    ensure_requirements_datasource_type,
    read_application_datasource_type,
)
from app.services.requirement_spec import create_requirement_spec


class DataSourcePolicyTests(unittest.TestCase):
    """验证应用配置类型是 RequirementSpec 的唯一数据源大类来源。"""

    def _write_application(self, workspace: str, datasource_type: str, **extra: object) -> None:
        """写入包含敏感字段的最小应用配置，验证策略只读取数据源类型。"""

        application_dir = Path(workspace) / ".xcodeagent"
        application_dir.mkdir(parents=True, exist_ok=True)
        application_dir.joinpath("application.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "datasource": {
                        "type": datasource_type,
                        "db": {"plantMode": {"pwd": "do-not-expose"}},
                    },
                    **extra,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

    def test_reads_canonical_type_without_exposing_database_fields(self) -> None:
        """读取 Static 时不依赖也不返回数据库凭据。"""

        with tempfile.TemporaryDirectory() as workspace:
            self._write_application(workspace, "static")
            self.assertEqual(read_application_datasource_type(workspace), "static")

    def test_rejects_legacy_datasource_values_without_normalization(self) -> None:
        """旧枚举值直接失败，不做 DataBase、Api 或 mock 兼容转换。"""

        for legacy_type in ("DataBase", "Api", "Static", "mock"):
            with self.subTest(legacy_type=legacy_type), tempfile.TemporaryDirectory() as workspace:
                self._write_application(workspace, legacy_type)
                with self.assertRaises(DataSourcePolicyError):
                    read_application_datasource_type(workspace)

    def test_external_api_is_canonical_but_disabled_for_requirements(self) -> None:
        """External API 可以被识别为正式类型，但当前需求流程必须拒绝它。"""

        with tempfile.TemporaryDirectory() as workspace:
            self._write_application(workspace, "external_api")
            datasource_type = read_application_datasource_type(workspace)
            with self.assertRaises(DataSourcePolicyError):
                ensure_requirements_datasource_type(datasource_type)

    def test_projection_overwrites_only_datasource_type(self) -> None:
        """权威投影保留用户业务字段和稳定 id，只覆盖数据源类型。"""

        spec = {
            "data_sources": [
                {
                    "id": "orders_source",
                    "name": "订单数据",
                    "description": "用户编辑后的说明",
                    "entities": ["Order"],
                    "type": "mock",
                }
            ],
            "hidden": {"preserved": True},
        }
        projected = apply_authoritative_datasource_type(spec, "static")
        self.assertEqual(projected["data_sources"][0]["type"], "static")
        self.assertEqual(projected["data_sources"][0]["name"], "订单数据")
        self.assertEqual(projected["data_sources"][0]["entities"], ["Order"])
        self.assertEqual(projected["hidden"], {"preserved": True})
        self.assertEqual(spec["data_sources"][0]["type"], "mock")

    def test_requirement_builder_does_not_infer_type_from_request(self) -> None:
        """Static 应用即使需求文本提到数据库，也只能生成 static 类型。"""

        spec = create_requirement_spec(
            "需要数据库字段和订单查询",
            datasource_type="static",
        )
        self.assertTrue(spec["data_sources"])
        self.assertTrue(all(source["type"] == "static" for source in spec["data_sources"]))
        self.assertNotIn("mock", json.dumps(spec, ensure_ascii=False))

    def test_requirement_builder_rejects_legacy_type_instead_of_writing_it(self) -> None:
        """RequirementSpec 生成器不接受 mock 等非正式数据源类型。"""

        with self.assertRaises(DataSourcePolicyError):
            create_requirement_spec("创建订单系统", datasource_type="mock")  # type: ignore[arg-type]

    def test_formal_artifact_rejects_mixed_or_legacy_types(self) -> None:
        """ProjectPlan 等正式工件不能混用类型，也不能读取 mock。"""

        with self.assertRaises(DataSourcePolicyError):
            datasource_type_from_artifact(
                {"data_sources": [{"type": "database"}, {"type": "static"}]}
            )
        with self.assertRaises(DataSourcePolicyError):
            datasource_type_from_artifact({"data_sources": [{"type": "mock"}]})


if __name__ == "__main__":
    unittest.main()
