"""验证当前唯一支持的 Template Reconcile V2 协议。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from pydantic import ValidationError

from app.services.template_reconcile.protocol_v2 import (
    StrategyUpdatePackageV2,
    TemplateStateV2,
    TemplateReconcileProtocolV2Error,
    assert_reconcile_state_invariant_v2,
)
from app.services.template_reconcile.digest_v2 import template_state_digest_v2
from app.services.template_reconcile.state_v2 import (
    load_template_state_v2,
    write_template_state_v2,
)
from app.services.template_reconcile.strategy_update_package import (
    validate_strategy_update_package,
)
from app.services.workspace_bootstrap.models import ArchiveLimits, TemplatePackageError


def _state() -> dict[str, object]:
    """构造包含 login 的最小 V2 State fixture。"""

    return {
        "schemaVersion": 2,
        "templateRevision": "2026.09.10.1",
        "requested": {"login": {"enabled": True, "config": {}}},
        "effective": {"login": {"enabled": True, "config": {}}},
        "appliedAdditions": {},
    }


def _package() -> dict[str, object]:
    """构造能覆盖 login 后置条件的最小 RECONCILE Package fixture。"""

    return {
        "protocolVersion": "2",
        "packageId": "pkg-login-reconcile",
        "mode": "RECONCILE",
        "sourceRevision": "2026.09.10.1",
        "currentStateDigest": template_state_digest_v2(TemplateStateV2.model_validate(_state())),
        "nextStateDigest": template_state_digest_v2(TemplateStateV2.model_validate(_state())),
        "strategies": [
            {
                "strategyId": "login-route",
                "index": 0,
                "schemaVersion": 1,
                "type": "ENSURE_ROUTE",
                "target": "frontend/src/routes.tsx",
                "precondition": {},
                "payloadRef": None,
            }
        ],
        "validationPlan": [
            {
                "validationId": "login-route-postcondition",
                "index": 0,
                "type": "CAPABILITY_POSTCONDITION",
                "capabilityId": "login",
                "workingDirectory": "frontend",
                "checks": [{"type": "FILE_EXISTS", "path": "src/routes.tsx"}],
                "blocking": True,
                "timeoutSeconds": 30,
                "executionMode": "REAL_WORKSPACE",
            }
        ],
        "payloadManifest": {},
        "nextTemplateState": _state(),
        "diagnostics": [],
    }


class TemplateReconcileProtocolV2Tests(unittest.TestCase):
    """确保旧协议和不完整的 V2 Package 都不会越过本地边界。"""

    def test_accepts_current_v2_template_state(self) -> None:
        """确认 State 仅接受 schemaVersion=2 的当前结构。"""

        state = TemplateStateV2.model_validate(_state())
        self.assertEqual(state.schemaVersion, 2)
        self.assertNotIn("managedFiles", state.model_dump())

    def test_rejects_removed_release_digest(self) -> None:
        """确认已删除字段不能以额外字段重新进入 V2 State。"""

        payload = _state()
        payload["release" + "Digest"] = "sha256:" + "a" * 64
        with self.assertRaises(ValidationError):
            TemplateStateV2.model_validate(payload)

    def test_accepts_applied_addition_without_origin(self) -> None:
        """确认 Addition 只记录目标事实，不要求无业务用途的来源字段。"""

        payload = _state()
        payload["appliedAdditions"] = {
            "login.login-page": {
                "capabilityId": "login",
                "target": "frontend/src/pages/Login/index.tsx",
                "installedRevision": "2026.09.10.1",
            }
        }
        state = TemplateStateV2.model_validate(payload)
        self.assertEqual(
            state.appliedAdditions["login.login-page"].installedRevision,
            "2026.09.10.1",
        )

    def test_rejects_removed_applied_addition_origin(self) -> None:
        """确认已删除的 origin 不得以额外字段形式重返当前 State。"""

        payload = _state()
        payload["appliedAdditions"] = {
            "login.login-page": {
                "capabilityId": "login",
                "target": "frontend/src/pages/Login/index.tsx",
                "installedRevision": "2026.09.10.1",
                "origin": "GENERATED",
            }
        }
        with self.assertRaises(ValidationError):
            TemplateStateV2.model_validate(payload)

    def test_rejects_legacy_template_state(self) -> None:
        """确认旧 managedFiles State 没有兼容入口。"""

        with self.assertRaises(ValidationError):
            TemplateStateV2.model_validate(
                {
                    "templateRevision": "legacy",
                    "managedFiles": {},
                    "requested": {},
                    "effective": {},
                }
            )

    def test_reconcile_requires_postcondition_for_each_effective_capability(self) -> None:
        """确认 RECONCILE 不能跳过 effective Capability 的验收。"""

        package = StrategyUpdatePackageV2.model_validate(_package())
        self.assertEqual(package.mode, "RECONCILE")
        invalid = _package()
        invalid["validationPlan"] = []
        with self.assertRaises(ValidationError):
            StrategyUpdatePackageV2.model_validate(invalid)

    def test_reconcile_rejects_state_digest_change(self) -> None:
        """确认 RECONCILE 不允许推进 TemplateState identity。"""

        invalid = _package()
        invalid["nextStateDigest"] = "sha256:" + "b" * 64
        with self.assertRaises(ValidationError):
            StrategyUpdatePackageV2.model_validate(invalid)

    def test_reconcile_state_invariant_rejects_config_change_even_with_digest_claim(self) -> None:
        """验证协调器逐字段比较 Capability 状态，而不只相信 Package 自报 digest。"""

        current = TemplateStateV2.model_validate(_state())
        changed = current.model_copy(deep=True)
        changed.requested["login"].config["mode"] = "changed"
        with self.assertRaises(TemplateReconcileProtocolV2Error):
            assert_reconcile_state_invariant_v2(current, changed)

    def test_rejects_non_contiguous_strategy_order(self) -> None:
        """确认策略数组顺序必须是零起连续的唯一执行顺序。"""

        invalid = _package()
        strategies = invalid["strategies"]
        assert isinstance(strategies, list)
        strategies[0]["index"] = 2
        with self.assertRaises(ValidationError):
            StrategyUpdatePackageV2.model_validate(invalid)

    def test_state_reader_rejects_legacy_workspace_without_migration(self) -> None:
        """确认 V2 Reader 不为旧 State 提供迁移或兼容分支。"""

        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / ".xcodeagent" / "template-state.json"
            state_path.parent.mkdir()
            state_path.write_text(
                json.dumps(
                    {
                        "templateRevision": "legacy",
                        "managedFiles": {},
                        "requested": {},
                        "effective": {},
                    }
                ),
                encoding="utf-8",
            )
            with self.assertRaises(ValueError):
                load_template_state_v2(directory)

    def test_state_writer_round_trips_v2_state(self) -> None:
        """确认 V2 Writer 只落盘已通过严格校验的当前 State。"""

        with tempfile.TemporaryDirectory() as directory:
            state = TemplateStateV2.model_validate(_state())
            write_template_state_v2(directory, state)
            self.assertEqual(load_template_state_v2(directory), state)

    def test_package_validator_checks_payload_manifest(self) -> None:
        """确认 ZIP payload 的字节大小和摘要必须与 Package Manifest 一致。"""

        with tempfile.TemporaryDirectory() as directory:
            archive_path = Path(directory) / "package.zip"
            content = b"export const Login = () => null;\n"
            package = _package()
            package["mode"] = "APPLY"
            package["nextStateDigest"] = template_state_digest_v2(
                TemplateStateV2.model_validate(_state())
            )
            package["payloadManifest"] = {
                "payload/login.tsx": {
                    "size": len(content),
                    "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                }
            }
            strategies = package["strategies"]
            assert isinstance(strategies, list)
            strategies[0]["payloadRef"] = "payload/login.tsx"
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("strategy-update-package.json", json.dumps(package))
                archive.writestr("payload/login.tsx", content)
            validated = validate_strategy_update_package(
                archive_path, ArchiveLimits(1024 * 1024, 100, 1024 * 1024)
            )
            self.assertEqual(validated.package.packageId, "pkg-login-reconcile")
            package["payloadManifest"]["payload/login.tsx"]["size"] = 1
            with zipfile.ZipFile(archive_path, "w") as archive:
                archive.writestr("strategy-update-package.json", json.dumps(package))
                archive.writestr("payload/login.tsx", content)
            with self.assertRaises(TemplatePackageError):
                validate_strategy_update_package(
                    archive_path, ArchiveLimits(1024 * 1024, 100, 1024 * 1024)
                )
