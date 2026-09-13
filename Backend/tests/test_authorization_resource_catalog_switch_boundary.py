"""验证权限资源目录不重复维护 application.json 的能力开关。"""

import unittest

from app.services.authorization_resource_catalog import compile_frontend_resource_catalog
from app.services.unit_generation_requirement_targets import resource_catalog_fingerprint


class AuthorizationResourceCatalogSwitchBoundaryTests(unittest.TestCase):
    """覆盖 manifest 与应用级开关之间的职责边界。"""

    def test_manifest_without_application_switch_still_compiles_resource_catalog(self) -> None:
        """manifest 不携带应用级开关时，合法资源目录仍可独立编译。"""

        manifest = {
            "schema_version": "authorization-manifest.v2",
            "resources": [
                {
                    "resourceKey": "system_authorization_management",
                    "type": "system",
                    "targetResourceRef": "system:authorization_management",
                }
            ],
        }
        catalog = compile_frontend_resource_catalog(manifest)
        self.assertIsNotNone(catalog)
        self.assertEqual([item.resource_key for item in catalog.resources], ["system_authorization_management"])

    def test_planning_fingerprint_uses_manifest_resources_without_application_switch(self) -> None:
        """Planning 只从权限资源事实计算指纹，不要求 manifest 复制应用开关。"""

        plan = {
            "confirmation_status": "confirmed",
            "authorization_manifest": {
                "schema_version": "authorization-manifest.v2",
                "resources": [
                    {
                        "resourceKey": "system_authorization_management",
                        "type": "system",
                        "targetResourceRef": "system:authorization_management",
                    }
                ],
            },
        }
        fingerprint = resource_catalog_fingerprint(plan)
        self.assertIsInstance(fingerprint, str)
        self.assertEqual(len(fingerprint), 64)

    def test_empty_manifest_resources_have_no_catalog_identity(self) -> None:
        """空资源目录不产生权限资源能力身份，开关仍由 application.json 决定。"""

        plan = {
            "confirmation_status": "confirmed",
            "authorization_manifest": {
                "schema_version": "authorization-manifest.v2",
                "resources": [],
            },
        }
        self.assertIsNone(resource_catalog_fingerprint(plan))


if __name__ == "__main__":
    unittest.main()
