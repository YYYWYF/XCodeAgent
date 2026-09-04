"""T2.5A 完整资源目录的确定性、源身份、重复和既有常量规则回归。"""

from copy import deepcopy
from dataclasses import FrozenInstanceError
from itertools import permutations
import json
from pathlib import Path
import tempfile
import unittest

from app.services.authorization_frontend_projection import (
    AuthorizationFrontendProjectionError,
    apply_authorization_frontend_projection,
    compile_frontend_authorization_projection,
)
from app.services.authorization_resource_catalog import (
    ResourceCatalog,
    compile_frontend_resource_catalog,
    resource_catalog_fingerprint,
)
from app.services.unit_generation_requirement_targets import resource_catalog_fingerprint as plan_fingerprint


def _manifest() -> dict:
    """提供包含全部三类已编译资源的确认 manifest 源数据。"""

    return {
        "enabled": True,
        "resources": [
            {"resourceKey": "system_authorization_management", "type": "system", "targetResourceRef": "system:authorization_management"},
            {"resourceKey": "page_orders", "type": "page", "targetResourceRef": "page:page_orders"},
            {"resourceKey": "page_orders_approve", "type": "operation", "targetResourceRef": "action:page_orders:approve"},
        ],
        "bindings": {"pages": [{"pageId": "page_orders", "resourceKey": "page_orders"}]},
    }


def _fingerprint(manifest: dict) -> str:
    """从 manifest 源数据经过正式 catalog API 取得身份。"""

    return resource_catalog_fingerprint(compile_frontend_resource_catalog(manifest))


class AuthorizationResourceCatalogTests(unittest.TestCase):
    """验证源数据身份与前端投影共享目录，但不依赖文件、路由或描述。"""

    def test_deterministic_catalog_is_complete_and_immutable(self) -> None:
        """重复编译保持稳定，完整保留三类资源且不修改输入或暴露可变身份。"""

        manifest = _manifest()
        original = deepcopy(manifest)
        catalog = compile_frontend_resource_catalog(manifest)
        self.assertEqual(catalog, compile_frontend_resource_catalog(deepcopy(manifest)))
        self.assertEqual({item.resource_type for item in catalog.resources}, {"page", "operation", "system"})
        self.assertEqual(_fingerprint(manifest), _fingerprint(deepcopy(manifest)))
        self.assertRegex(_fingerprint(manifest), r"^[a-f0-9]{64}$")
        self.assertEqual(manifest, original)
        with self.assertRaises(FrozenInstanceError):
            catalog.resources[0].resource_key = "changed"
        catalog.frontend_resources()[0]["name"] = "CHANGED"
        manifest["resources"][0]["resourceKey"] = "system_changed"
        self.assertEqual(resource_catalog_fingerprint(catalog), _fingerprint(original))

    def test_normalizes_resource_and_object_key_order(self) -> None:
        """遍历全部资源排列和对象字段反序，canonical catalog 及指纹保持一致。"""

        manifest = _manifest()
        expected = compile_frontend_resource_catalog(manifest)
        for items in permutations(manifest["resources"]):
            reordered = {**manifest, "resources": [dict(reversed(list(item.items()))) for item in items]}
            self.assertEqual(compile_frontend_resource_catalog(reordered), expected)
            self.assertEqual(_fingerprint(reordered), resource_catalog_fingerprint(expected))
        reversed_catalog = ResourceCatalog(tuple(reversed(expected.resources)))
        self.assertEqual(resource_catalog_fingerprint(reversed_catalog), resource_catalog_fingerprint(expected))

    def test_page_resource_addition_changes_fingerprint(self) -> None:
        """Scope 外新页面资源同样进入完整目录并改变身份。"""

        manifest = _manifest()
        before = _fingerprint(manifest)
        manifest["resources"].append({"resourceKey": "page_users", "type": "page", "targetResourceRef": "page:page_users"})
        self.assertNotEqual(_fingerprint(manifest), before)
        self.assertIn("page_users", {item.resource_key for item in compile_frontend_resource_catalog(manifest).resources})

    def test_operation_resource_addition_changes_fingerprint(self) -> None:
        """未被当前页面引用的新操作仍然改变完整资源目录身份。"""

        manifest = _manifest()
        before = _fingerprint(manifest)
        manifest["resources"].append({"resourceKey": "page_users_export", "type": "operation", "targetResourceRef": "action:page_users:export"})
        self.assertNotEqual(_fingerprint(manifest), before)

    def test_system_addition_and_resource_removal_change_fingerprint(self) -> None:
        """系统资源新增和任一类别资源移除都改变职责版本。"""

        manifest = _manifest()
        before = _fingerprint(manifest)
        added = deepcopy(manifest)
        added["resources"].append({"resourceKey": "system_audit", "type": "system", "targetResourceRef": "system:audit"})
        self.assertNotEqual(_fingerprint(added), before)
        for index in range(3):
            removed = deepcopy(manifest)
            removed["resources"].pop(index)
            self.assertNotEqual(_fingerprint(removed), before)

    def test_each_source_identity_field_changes_fingerprint(self) -> None:
        """逐类验证键、类型和目标原文变化，即使常量符号相同也不可复用旧身份。"""

        manifest = _manifest()
        before = _fingerprint(manifest)
        for index in range(3):
            for field, value in (
                ("resourceKey", manifest["resources"][index]["resourceKey"] + "_new"),
                ("type", "operation" if index == 0 else "system"),
                ("targetResourceRef", manifest["resources"][index]["targetResourceRef"] + "_new"),
            ):
                with self.subTest(index=index, field=field):
                    changed = deepcopy(manifest)
                    changed["resources"][index][field] = value
                    self.assertNotEqual(_fingerprint(changed), before)
        changed = deepcopy(manifest)
        changed["resources"][2]["targetResourceRef"] = "action:page_orders:APPROVE"
        self.assertEqual(compile_frontend_resource_catalog(changed).frontend_resources(), compile_frontend_resource_catalog(manifest).frontend_resources())
        self.assertNotEqual(_fingerprint(changed), before)

    def test_non_identity_metadata_does_not_change_fingerprint(self) -> None:
        """描述、来源规则、授权绑定和 manifest 自身摘要不属于资源目录身份。"""

        manifest = _manifest()
        before = _fingerprint(manifest)
        manifest["fingerprint"] = "unrelated-manifest-digest"
        manifest["resources"][1].update(name="订单", description="新描述", sourceRuleIds=["rule_b", "rule_a"])
        manifest["bindings"] = {"endpoints": [{"endpointId": "orders.list"}]}
        manifest["defaultRoleAuthorization"] = {"roleResourceGrants": [{"roleSeedKey": "admin", "resourceKeys": ["page_orders"]}]}
        self.assertEqual(_fingerprint(manifest), before)
        self.assertEqual(_fingerprint(json.loads(json.dumps(manifest, indent=4))), before)

    def test_duplicates_are_rejected_instead_of_merged(self) -> None:
        """完全重复、同键异目标和同键跨类型都显式报错。"""

        for changes in ({}, {"targetResourceRef": "page:page_users"}, {"type": "operation", "targetResourceRef": "action:page_orders:view"}):
            with self.subTest(changes=changes):
                manifest = _manifest()
                manifest["resources"].append({**manifest["resources"][1], **changes})
                with self.assertRaisesRegex(AuthorizationFrontendProjectionError, "常量名冲突|resourceKey 重复"):
                    compile_frontend_resource_catalog(manifest)

    def test_constant_collisions_keep_existing_rule(self) -> None:
        """大小写和标点归一化后的同组同名常量仍报原有冲突错误。"""

        for index, changes, symbol in (
            (0, {"resourceKey": "system_AUTHORIZATION_MANAGEMENT"}, "SYSTEM.AUTHORIZATION_MANAGEMENT"),
            (1, {"resourceKey": "other_page", "targetResourceRef": "page:page_ORDERS"}, "PAGE.ORDERS"),
            (2, {"resourceKey": "other_operation", "targetResourceRef": "action:page_orders:approve!"}, "OPERATION.ORDERS_APPROVE"),
        ):
            with self.subTest(symbol=symbol):
                manifest = _manifest()
                manifest["resources"].append({**manifest["resources"][index], **changes})
                with self.assertRaisesRegex(AuthorizationFrontendProjectionError, f"RESOURCES 常量名冲突：{symbol}"):
                    compile_frontend_authorization_projection({"authorization_manifest": manifest})

    def test_same_constant_name_in_different_groups_remains_allowed(self) -> None:
        """既有冲突检测只在同组内生效，跨组同名不误报。"""

        manifest = _manifest()
        manifest["resources"].append({"resourceKey": "system_orders", "type": "system", "targetResourceRef": "system:orders"})
        references = compile_frontend_resource_catalog(manifest).frontend_resources()
        self.assertEqual({item["group"] for item in references if item["name"] == "ORDERS"}, {"PAGE", "SYSTEM"})

    def test_invalid_input_cannot_be_silently_dropped(self) -> None:
        """非法项、空目录和非精确身份不能静默缩小目录或变成其他资源。"""

        for items in (None, [], [None], [{"resourceKey": "page_orders"}]):
            with self.subTest(items=items), self.assertRaises(AuthorizationFrontendProjectionError):
                compile_frontend_resource_catalog({"enabled": True, "resources": items})
        for field in ("resourceKey", "type", "targetResourceRef"):
            for value in (None, 123, "", " page_orders "):
                manifest = _manifest()
                manifest["resources"][1][field] = value
                with self.subTest(field=field, value=value), self.assertRaises(AuthorizationFrontendProjectionError):
                    compile_frontend_resource_catalog(manifest)

    def test_formal_caller_uses_same_identity_without_route_compilation(self) -> None:
        """正式调用方使用唯一目录指纹，并保留确认与权限关闭门禁。"""

        manifest = _manifest()
        plan = {"confirmation_status": "confirmed", "authorization_manifest": manifest, "pages": [{"path": "invalid-route"}]}
        self.assertEqual(plan_fingerprint(plan), _fingerprint(manifest))
        with self.assertRaisesRegex(ValueError, "已确认"):
            plan_fingerprint({**plan, "confirmation_status": "pending"})
        self.assertIsNone(compile_frontend_resource_catalog({"enabled": False}))
        self.assertIsNone(plan_fingerprint({**plan, "authorization_manifest": {"enabled": False}}))
        self.assertIsNone(plan_fingerprint({"confirmation_status": "confirmed"}))

    def test_generated_file_whitespace_does_not_change_identity(self) -> None:
        """真实写入再改变 resources.ts 空白，源目录和正式调用方指纹仍一致。"""

        manifest = _manifest()
        plan = {"confirmation_status": "confirmed", "authorization_manifest": manifest, "pages": [{"pageId": "page_orders", "path": "/orders"}]}
        projection = compile_frontend_authorization_projection(plan)
        self.assertEqual(projection["resources"], compile_frontend_resource_catalog(manifest).frontend_resources())
        before = plan_fingerprint(plan)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            routes = root / "frontend/src/constants/routes.tsx"
            routes.parent.mkdir(parents=True)
            routes.write_text("// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_START\n// XCODEAGENT_BUSINESS_ROUTE_IMPORTS_END\n// XCODEAGENT_BUSINESS_ROUTES_START\n// XCODEAGENT_BUSINESS_ROUTES_END\n", encoding="utf-8")
            apply_authorization_frontend_projection(root, projection)
            resources = routes.with_name("resources.ts")
            source = resources.read_text(encoding="utf-8")
            resources.write_text("\n\n" + source.replace("  ", "    ") + "\n", encoding="utf-8")
            self.assertNotEqual(source, resources.read_text(encoding="utf-8"))
            self.assertEqual(plan_fingerprint(plan), before)
            self.assertEqual(_fingerprint(manifest), before)


if __name__ == "__main__":
    unittest.main()
