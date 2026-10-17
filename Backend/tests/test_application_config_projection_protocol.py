from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from app.protocols.application_config_projection import (
    application_config_projection_capabilities,
    build_application_config_projection_ag_ui_stream,
)


def _write_application(root: Path, *, revision: int = 1) -> None:
    """写入投影协议读取的最小 canonical application.json。"""

    target = root / ".devagentstudio/application.json"
    target.parent.mkdir(parents=True)
    target.write_text(
        json.dumps(
            {
                "schemaVersion": 6,
                "configRevision": revision,
                "appName": "订单中心",
                "appIcon": "AppstoreOutlined",
                "theme": {"primaryColor": "#6b3cf0"},
                "auth": {"enable": False},
                "authorization": {
                    "enabled": False,
                    "initialAdministratorSubjects": [],
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


async def _collect(root: Path, *, revision: int = 1) -> str:
    """消费一次应用配置投影的完整 AG-UI 事件流。"""

    stream = build_application_config_projection_ag_ui_stream(
        payload={
            "threadId": "projection-thread",
            "runId": "projection-run",
            "forwardedProps": {
                "applicationConfigProjection": {
                    "action": "project",
                    "workspaceRoot": str(root),
                    "expectedConfigRevision": revision,
                }
            },
        }
    )
    return "".join([frame async for frame in stream])


class ApplicationConfigProjectionProtocolTests(unittest.TestCase):
    """验证设置保存使用独立 AG-UI 投影端点及明确业务结果。"""

    def test_capabilities_publish_dedicated_ag_ui_endpoint(self) -> None:
        """能力元数据必须声明独立端点、动作和状态键。"""

        capability = application_config_projection_capabilities()

        self.assertEqual(capability["endpoint"], "/application-config/run")
        self.assertEqual(capability["transport"], "ag-ui-sse")
        self.assertEqual(capability["actions"], ["project"])
        self.assertEqual(capability["stateSnapshotKey"], "applicationConfigProjection")

    def test_missing_template_returns_explicit_skipped_result(self) -> None:
        """新建应用尚无模板根时保存成功，投影动作返回 SKIPPED。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_application(root)
            frames = asyncio.run(_collect(root))

        self.assertIn('"status":"SKIPPED_NO_TEMPLATE"', frames)
        self.assertIn('"configRevision":1', frames)
        self.assertIn('"type":"RUN_FINISHED"', frames)

    def test_existing_template_projects_or_returns_explicit_old_template_error(self) -> None:
        """新模板返回 CHANGED，旧模板缺目标返回稳定失败码。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_application(root)
            target = root / "frontend/src/config/app.generated.ts"
            target.parent.mkdir(parents=True)
            target.write_text("// default\n", encoding="utf-8")
            frames = asyncio.run(_collect(root))
            self.assertIn('"status":"CHANGED"', frames)
            self.assertIn('"status":"completed"', frames)

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _write_application(root)
            (root / "frontend").mkdir()
            frames = asyncio.run(_collect(root))
            self.assertIn("APP_CONFIG_TARGET_MISSING", frames)
            self.assertIn('"status":"failed"', frames)


if __name__ == "__main__":
    unittest.main()
