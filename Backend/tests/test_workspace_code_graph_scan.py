from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.graph.direct_modification_workflow import direct_next_node_name
from app.graph.nodes.workspace_inspection import inspect_workspace as inspect_node
from app.services.workspace_inspector import (
    _entrypoints,
    inspect_workspace,
    source_files_for_code_graph,
)


class WorkspaceCodeGraphScanTests(unittest.TestCase):
    """验证扫描入口只绑定显式用户工作区并保留降级边界。"""

    def test_explicit_workspace_provider_receives_only_source_files(self) -> None:
        """代码图 Provider 只能收到用户工作区内的安全源码清单。"""

        class RecordingProvider:
            """记录扫描参数的最小 Provider。"""

            def __init__(self) -> None:
                self.received: list[str] = []

            def available(self) -> bool:
                """声明 Provider 可用。"""

                return True

            def inspect(self, root: Path, files: list[str], **kwargs: object) -> dict[str, object]:
                """记录 root 和文件清单并返回安全摘要。"""

                self.received = list(files)
                self.root = root
                return {"provider": "test", "available": True, "filesIndexed": len(files)}

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "main.py").write_text("def main():\n    pass\n", encoding="utf-8")
            (root / "notes.txt").write_text("not source", encoding="utf-8")
            (root / "fake.py").write_bytes(b"def fake():\0\xff")
            (root / ".env").write_text("TOKEN=secret", encoding="utf-8")
            provider = RecordingProvider()
            snapshot, _, _ = inspect_workspace(
                root,
                cache_root=root / ".snapshot-cache",
                code_graph_provider=provider,  # type: ignore[arg-type]
            )
            self.assertEqual(provider.root, root.resolve())
            self.assertEqual(provider.received, ["main.py"])
            self.assertEqual(snapshot["code_graph"]["provider"], "test")
            self.assertEqual(source_files_for_code_graph(root, ["main.py", "notes.txt"]), ["main.py"])

    def test_missing_explicit_workspace_does_not_invoke_code_graph(self) -> None:
        """没有 workspaceRoot 时不得把 XCodeAgent 当前目录交给代码图。"""

        snapshot = {
            "schema_version": "1.1.0",
            "workspace_revision": "r1",
            "tech_stack": [],
            "entrypoints": [],
            "project_roots": [],
            "file_manifest": {},
            "code_graph": {"provider": "none", "available": False},
        }
        with patch(
            "app.graph.nodes.workspace_inspection.inspect_workspace_service",
            return_value=(snapshot, "/tmp/snapshot.json", False),
        ), patch(
            "app.graph.nodes.workspace_inspection.get_code_graph_manager"
        ) as manager_factory:
            result = inspect_node({"project_id": "without-workspace"})
        manager_factory.assert_not_called()
        self.assertEqual(result["workspace_snapshot_summary"]["code_graph"]["provider"], "none")
        self.assertNotIn("workspace_code_navigation_context", result)

    def test_agent_repository_root_is_never_accepted_by_code_graph(self) -> None:
        """代码图管理器拒绝把 XCodeAgent 自己的工程目录当作用户工作区。"""

        from app.services.code_graph.manager import CodeGraphManager

        result = CodeGraphManager().ensure_index(
            Path(__file__).resolve().parents[2],
            ["Backend/app/main.py"],
            revision="agent-root",
        )
        self.assertEqual(result.status, "skipped")
        self.assertIn("XCodeAgent", result.message)

    def test_direct_scan_enters_classification_before_execution(self) -> None:
        """快速修改必须先扫描，再分类并进入对应执行节点。"""

        scanned = {"status": "completed"}
        classified = {"status": "in_progress", "direct_modification_owner": "frontend"}
        self.assertEqual(
            direct_next_node_name("scan_workspace_code", scanned),
            "classify_intent",
        )
        self.assertEqual(
            direct_next_node_name("classify_intent", classified),
            "execute_frontend",
        )

    def test_entrypoints_include_user_frontend_and_spring_application_files(self) -> None:
        """确定性入口识别覆盖用户前端和 Spring Boot 主类。"""

        entries = _entrypoints(
            [
                "frontend/src/main.tsx",
                "frontend/src/routes/index.tsx",
                "frontend/vite.config.ts",
                "backend/src/main/java/com/example/Application.java",
            ]
        )
        self.assertEqual(
            {item["path"] for item in entries},
            {
                "frontend/src/main.tsx",
                "frontend/src/routes/index.tsx",
                "frontend/vite.config.ts",
                "backend/src/main/java/com/example/Application.java",
            },
        )
        self.assertEqual(
            next(item["kind"] for item in entries if item["path"].endswith("Application.java")),
            "spring_application",
        )

if __name__ == "__main__":
    unittest.main()
