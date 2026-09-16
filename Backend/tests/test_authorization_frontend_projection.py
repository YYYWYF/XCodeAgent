import tempfile
import unittest
from pathlib import Path

from app.services.authorization_frontend_projection import apply_authorization_frontend_projection, compile_frontend_authorization_projection, verify_authorization_frontend_projection


class AuthorizationFrontendProjectionTests(unittest.TestCase):
    """验证权限投影只提供资源与 route decoration。"""

    def test_generates_optional_page_resource_routes(self) -> None:
        """通用路由覆盖全部页面，权限仅为受控页附加 resourceKey。"""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plan = self._plan()
            projection = compile_frontend_authorization_projection(
                plan, application_config={"authorization": {"enabled": True}}
            )
            apply_authorization_frontend_projection(root, projection)
            self.assertTrue(verify_authorization_frontend_projection(root, projection)["verified"])

    def _plan(self) -> dict:
        """构造同时包含受控和公开页面的最小计划。"""
        return {"artifact_type": "technical-plan", "pages": [{"pageId": "page_personal_assets", "name": "个人资产", "path": "/personal-assets"}, {"pageId": "page_public_assets", "name": "公共资产", "path": "/public-assets"}], "authorization_manifest": {"resources": [{"resourceKey": "system_authorization_management", "type": "system", "targetResourceRef": "system:authorization_management"}, {"resourceKey": "page_personal_assets", "type": "page", "targetResourceRef": "page:page_personal_assets"}], "bindings": {"pages": [{"pageId": "page_personal_assets", "resourceKey": "page_personal_assets"}], "actions": [], "endpoints": []}}}


if __name__ == "__main__":
    unittest.main()
