from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.nodes.authorization_bootstrap import authorization_bootstrap
from app.services.authorization_bootstrap import run_authorization_bootstrap


def _technical_plan(fingerprint: str = "sha256:abc") -> dict:
    """构造满足平台节点最小前置的已确认权限技术规划。"""

    return {
        "artifact_type": "technical-plan",
        "confirmation_status": "confirmed",
        "authorization_manifest": {"enabled": True, "fingerprint": fingerprint},
    }


class AuthorizationBootstrapTests(unittest.TestCase):
    """验证权限 Bootstrap 的平台调用、缓存和失败边界。"""

    def _workspace(self, directory: str) -> Path:
        """创建模板脚本执行器要求的最小生成项目目录。"""

        root = Path(directory)
        script = root / "backend" / "scripts" / "bootstrap-authorization.sh"
        ddl = root / "backend" / "docs" / "auth" / "sql" / "ddl.sql"
        script.parent.mkdir(parents=True)
        ddl.parent.mkdir(parents=True)
        script.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
        ddl.write_text("create table role (id int);", encoding="utf-8")
        return root

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_success_is_cached_by_manifest_fingerprint(self, run_mock) -> None:
        """同一 manifest 指纹成功后不应重复调用数据库脚本。"""

        run_mock.return_value.returncode = 0
        run_mock.return_value.stdout = b"ok"
        run_mock.return_value.stderr = b""
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            first = run_authorization_bootstrap(root, _technical_plan())
            second = run_authorization_bootstrap(root, _technical_plan())
            self.assertEqual(first["status"], "executed")
            self.assertEqual(second["status"], "reused")
            self.assertEqual(run_mock.call_count, 1)
            marker = root / ".xcodeagent/runtime/authorization-bootstrap/abc/result.json"
            self.assertEqual(
                json.loads(marker.read_text(encoding="utf-8"))["status"], "executed"
            )

    @patch("app.services.authorization_bootstrap.workspace_process_registry.run")
    def test_failed_run_is_not_cached(self, run_mock) -> None:
        """脚本失败必须阻断 Build，且不能写入可复用成功标记。"""

        run_mock.return_value.returncode = 2
        run_mock.return_value.stdout = b""
        run_mock.return_value.stderr = b"invalid input"
        with tempfile.TemporaryDirectory() as directory:
            root = self._workspace(directory)
            result = run_authorization_bootstrap(root, _technical_plan())
            self.assertEqual(result["status"], "failed")
            marker = root / ".xcodeagent/runtime/authorization-bootstrap/abc/result.json"
            self.assertFalse(marker.exists())

    def test_disabled_authorization_is_skipped(self) -> None:
        """权限关闭时平台执行器不应要求模板脚本存在。"""

        with tempfile.TemporaryDirectory() as directory:
            result = run_authorization_bootstrap(
                directory, {"authorization_manifest": {"enabled": False}}
            )
            self.assertEqual(result["status"], "skipped")

    def test_graph_node_rejects_missing_confirmed_build_plan(self) -> None:
        """Graph 节点必须在数据库副作用前拒绝缺失的 Build DAG。"""

        with tempfile.TemporaryDirectory() as directory:
            result = authorization_bootstrap(
                {"workspace": directory, "technical_plan": _technical_plan()}
            )
            self.assertEqual(result["status"], "failed")
            self.assertEqual(
                result["authorization_bootstrap_result"]["failure_category"],
                "build_plan_invalid",
            )
