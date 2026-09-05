from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.services.workspace_bootstrap.models import TemplateConfigError
from app.services.workspace_bootstrap.requested_config import compile_template_requested_config


class RequestedConfigTests(unittest.TestCase):
    """验证 Application 到 Engine RequestedConfig 的直接映射边界。"""

    def test_compiles_authorization_without_dependency_inference(self) -> None:
        """确认 login 与 authorization 各自只读取 Application 对应字段。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=True, authorization=True)
            self.assertEqual(
                compile_template_requested_config(root)["capabilities"],
                {
                    "login": {"enabled": True, "config": {}},
                    "authorization": {"enabled": True, "config": {}},
                },
            )

    def test_rejects_invalid_application_and_plan_drift(self) -> None:
        """确认非法 Application 组合和正式计划漂移会在 Engine 调用前失败。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self._write(root, login=False, authorization=True)
            with self.assertRaises(TemplateConfigError):
                compile_template_requested_config(root)
            self._write(root, login=True, authorization=True, plan_enabled=False)
            with self.assertRaises(TemplateConfigError):
                compile_template_requested_config(root)

    def _write(self, root: Path, *, login: bool, authorization: bool, plan_enabled: bool | None = None) -> None:
        """写入最小且正式的 Application 与 confirmed TechnicalPlan fixture。"""

        xcodeagent = root / ".xcodeagent"
        (xcodeagent / "plans").mkdir(parents=True, exist_ok=True)
        (xcodeagent / "application.json").write_text(
            json.dumps({"auth": {"enable": login}, "authorization": {"enabled": authorization}}),
            encoding="utf-8",
        )
        (xcodeagent / "plans/technical-plan.json").write_text(
            json.dumps({"artifact_type": "technical-plan", "confirmation_status": "confirmed", "authorization_manifest": {"enabled": authorization if plan_enabled is None else plan_enabled}}),
            encoding="utf-8",
        )
