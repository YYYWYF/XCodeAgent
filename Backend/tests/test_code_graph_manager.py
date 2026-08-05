from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from app.services.code_graph.manager import CodeGraphManager
from app.services.code_graph.models import CodeGraphIndexResult, CodeGraphQuery


class CodeGraphManagerTests(unittest.TestCase):
    """验证嵌入式代码图的工作区隔离、缓存和增量更新。"""

    def test_full_cache_incremental_and_delete_use_relative_paths(self) -> None:
        """首次全量、缓存命中以及新增修改删除都只操作用户工作区。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "app.py"
            source.write_text("def login():\n    return True\n", encoding="utf-8")
            manager = CodeGraphManager()

            first = manager.ensure_index(root, ["app.py"], revision="r1", timeout_seconds=5)
            self.assertEqual(first.status, "ready")
            self.assertEqual(first.build_type, "full")
            self.assertGreaterEqual(len(first.nodes_by_kind), 1)
            self.assertGreaterEqual(len(first.relations_by_kind), 1)
            self.assertLessEqual(len(first.sample_symbols), 8)
            self.assertTrue(all(not str(item["path"]).startswith("/") for item in first.sample_symbols))
            graph_dir = root / ".xcodeagent" / "cache" / "code-graph" / "v1"
            self.assertTrue((graph_dir / "graph.sqlite3").is_file())
            self.assertTrue((graph_dir / "index.json").is_file())
            index = json.loads((graph_dir / "index.json").read_text(encoding="utf-8"))
            self.assertEqual(index["files"], ["app.py"])
            self.assertIn("nodesByKind", index)
            self.assertIn("relationsByKind", index)
            self.assertIn("warningCount", index)

            cache_hit = manager.ensure_index(
                root,
                ["app.py"],
                revision="r1",
                timeout_seconds=5,
            )
            self.assertEqual(cache_hit.status, "cache_hit")
            self.assertEqual(cache_hit.nodes_by_kind, first.nodes_by_kind)
            self.assertEqual(cache_hit.relations_by_kind, first.relations_by_kind)

            added = root / "auth.py"
            added.write_text("def authenticate():\n    return True\n", encoding="utf-8")
            incremental = manager.ensure_index(
                root,
                ["app.py", "auth.py"],
                revision="r2",
                timeout_seconds=5,
            )
            self.assertEqual(incremental.build_type, "incremental")
            self.assertEqual(incremental.files_indexed, 2)

            source.write_text("def login():\n    return False\n", encoding="utf-8")
            modified = manager.ensure_index(
                root,
                ["app.py", "auth.py"],
                revision="r3",
                timeout_seconds=5,
            )
            self.assertEqual(modified.status, "ready")

            added.unlink()
            deleted = manager.ensure_index(
                root,
                ["app.py"],
                revision="r4",
                timeout_seconds=5,
            )
            self.assertEqual(deleted.status, "ready")
            self.assertEqual(deleted.files_indexed, 1)
            summary = manager.query(
                root,
                CodeGraphQuery(operation="file_summary", query="app.py"),
            ).as_dict()
            self.assertEqual(summary["status"], "ready")
            self.assertNotIn(str(root), json.dumps(summary, ensure_ascii=False))

    def test_query_is_bounded_and_never_returns_absolute_paths(self) -> None:
        """大量符号查询必须满足字符上限且不泄露宿主机绝对路径。"""

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "symbols.py"
            source.write_text(
                "\n\n".join(
                    f"def symbol_{index}():\n    return {index}" for index in range(120)
                ),
                encoding="utf-8",
            )
            manager = CodeGraphManager()
            manager.ensure_index(root, ["symbols.py"], revision="r1", timeout_seconds=5)
            result = manager.query(
                root,
                CodeGraphQuery(operation="search_symbols", query="symbol", max_results=40),
            ).as_dict()
            serialized = json.dumps(result, ensure_ascii=False)
            self.assertLessEqual(len(serialized), 16_384)
            self.assertNotIn(str(root), serialized)
            self.assertTrue(all(not str(item["path"]).startswith("/") for item in result["matches"]))

    def test_timeout_returns_indexing_without_failing_the_workflow(self) -> None:
        """前台超时只返回降级状态，后台索引任务仍可完成。"""

        class SlowAdapter:
            """为超时分支提供可控的阻塞适配器。"""

            def available(self) -> bool:
                """声明测试适配器可用。"""

                return True

            def version(self) -> str:
                """返回测试版本。"""

                return "test"

            def stats(self, db_path: Path) -> dict[str, object]:
                """返回缓存命中所需的最小统计。"""

                return {"files": 1, "nodes": 1, "edges": 0, "languages": ["python"]}

            def build_full(self, *args: object, **kwargs: object) -> CodeGraphIndexResult:
                """延迟后返回成功结果，模拟解析器阻塞。"""

                time.sleep(0.08)
                return CodeGraphIndexResult(
                    status="ready",
                    build_type="full",
                    files_indexed=1,
                    symbols_indexed=1,
                )

            def update_incremental(self, *args: object, **kwargs: object) -> CodeGraphIndexResult:
                """为接口完整性返回成功结果。"""

                return CodeGraphIndexResult(status="ready", build_type="incremental")

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.py").write_text("def app():\n    pass\n", encoding="utf-8")
            manager = CodeGraphManager(adapter=SlowAdapter())  # type: ignore[arg-type]
            result = manager.ensure_index(
                root,
                ["app.py"],
                revision="r1",
                timeout_seconds=0.001,
            )
            self.assertEqual(result.status, "indexing")
            self.assertNotIn("filesIndexed", result.as_dict())
            self.assertNotIn("nodesByKind", result.as_dict())
            time.sleep(0.12)
            self.assertTrue(manager._states[str(root.resolve())][1].done())

    def test_cached_warnings_never_expose_host_paths(self) -> None:
        """历史 metadata 中的异常正文也不能把宿主机路径投影到 UI。"""

        manager = CodeGraphManager()
        warnings = manager._safe_warnings(["app.py: /Users/secret/workspace/file.py"])
        self.assertEqual(warnings, ["app.py: warning 已脱敏"])
        self.assertNotIn("/Users", json.dumps(warnings))


if __name__ == "__main__":
    unittest.main()
