from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.workspace_bootstrap.models import TemplateConfigError
from app.services.workspace_bootstrap.requested_config import compile_template_requested_config


class RequestedConfigTests(unittest.TestCase):
    """验证 TechnicalPlan 到 Engine RequestedConfig 的唯一映射边界。"""

    def test_compiles_authorization_without_dependency_inference(self) -> None:
        """确认请求只读取 TechnicalPlan 的显式 Desired Capability。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=True, authorization=True)
            self.assertEqual(
                compile_template_requested_config(root)["capabilities"],
                {
                    "authorization": {"enabled": True, "config": {}},
                },
            )

    def test_rejects_manifest_and_template_capability_drift(self) -> None:
        """确认权限 Manifest 与模板能力漂移会在 Engine 调用前失败。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=False, authorization=True, plan_enabled=False)
            with self.assertRaises(TemplateConfigError):
                compile_template_requested_config(root)
            self._write(root, login=True, authorization=True, template_capabilities={"authorization": {"enabled": True, "config": {"mode": "custom"}}})
            with self.assertRaises(TemplateConfigError):
                compile_template_requested_config(root)

    def _write(self, root: Path, *, login: bool, authorization: bool, plan_enabled: bool | None = None, template_capabilities: dict[str, object] | None = None) -> None:
        """写入最小且正式的 Application 与 confirmed TechnicalPlan fixture。"""

        xcodeagent = root / ".xcodeagent"
        (xcodeagent / "plans").mkdir(parents=True, exist_ok=True)
        (xcodeagent / "application.json").write_text(
            json.dumps({"auth": {"enable": login}, "authorization": {"enabled": authorization}}),
            encoding="utf-8",
        )
        (xcodeagent / "plans/technical-plan.json").write_text(
            json.dumps({"artifact_type": "technical-plan", "confirmation_status": "confirmed", "authorization_manifest": {"enabled": authorization if plan_enabled is None else plan_enabled}, "template_capabilities": template_capabilities if template_capabilities is not None else ({"authorization": {"enabled": True, "config": {}}} if authorization else {})}),
            encoding="utf-8",
        )
