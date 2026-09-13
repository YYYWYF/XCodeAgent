"""应用配置变更 Schema 的边界与序列化验证。"""

import unittest

from pydantic import ValidationError

from app.domain.application_config_change import (
    ApplicationConfigChange,
    NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG,
)


def _change_payload(**overrides: object) -> dict:
    """构造包含原始用户证据的标准配置变更输入。"""

    return {
        "path": "auth.enable",
        "operation": "set",
        "from": False,
        "to": True,
        "reason": "用户要求增加登录功能",
        "evidence": " 我想添加登录模块 ",
        **overrides,
    }


class ApplicationConfigChangeTests(unittest.TestCase):
    """确保后续服务接收明确、受限且不会隐式转换的配置提案。"""

    def test_allowed_paths_round_trip_in_both_directions(self) -> None:
        """四个开关均支持启用与停用，并保留外部别名及证据原文。"""

        for path in NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG:
            for enabled in (True, False):
                with self.subTest(path=path, enabled=enabled):
                    payload = _change_payload(path=path, **{"from": not enabled, "to": enabled})
                    change = ApplicationConfigChange.model_validate(payload)
                    self.assertEqual(change.model_dump(by_alias=True), payload)
                    self.assertEqual(
                        ApplicationConfigChange.model_validate_json(change.model_dump_json(by_alias=True)),
                        change,
                    )

    def test_rejects_unapproved_fields_operations_and_values(self) -> None:
        """禁止任意配置路径、JSON Patch 操作、类型强转、无变化和空证据。"""

        invalid = [
            {"path": path}
            for path in ("schemaVersion", "appName", "datasource.type", "auth", "/auth/enable")
        ]
        invalid += [{"operation": "remove"}, {"unexpected": True}, {"to": False}]
        invalid += [{field: value} for field in ("from", "to") for value in (0, 1, "true", None, {})]
        invalid += [{field: value} for field in ("reason", "evidence") for value in ("", " \n", 42)]
        for overrides in invalid:
            with self.subTest(overrides=overrides), self.assertRaises(ValidationError):
                ApplicationConfigChange.model_validate(_change_payload(**overrides))

    def test_all_contract_fields_are_required(self) -> None:
        """不允许缺少操作、当前值、目标值或来源说明的提案进入后续流程。"""

        for field in _change_payload():
            payload = _change_payload()
            del payload[field]
            with self.subTest(field=field), self.assertRaises(ValidationError):
                ApplicationConfigChange.model_validate(payload)

    def test_json_schema_exposes_public_contract(self) -> None:
        """导出的 JSON Schema 使用 from/to 和封闭字段白名单。"""

        schema = ApplicationConfigChange.model_json_schema(by_alias=True)
        self.assertEqual(set(schema["required"]), set(_change_payload()))
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]["path"]["enum"]), NATURAL_LANGUAGE_MUTABLE_APPLICATION_CONFIG)
        self.assertEqual(schema["properties"]["operation"]["const"], "set")
        self.assertEqual(schema["properties"]["from"]["type"], "boolean")
        self.assertEqual(schema["properties"]["to"]["type"], "boolean")
