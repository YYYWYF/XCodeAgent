"""按 Git 版本读取工作区内容：读的是那个版本当时的样子，且不触碰工作区。

历史版本回看依赖这两个只读原语。核心承诺有两条，测试分别钉住：
1. 读到的内容属于指定版本（而不是当前工作区）；
2. 读取过程不改变工作区（绝不能用 git checkout —— 那会覆盖正在开发的代码）。
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from app.workspace.revision_workspace import revision_file, revision_tree


def _git(root: Path, *arguments: str) -> str:
    completed = subprocess.run(
        ["git", *arguments], cwd=root, capture_output=True, text=True, check=False
    )
    if completed.returncode != 0:
        raise AssertionError(f"git {' '.join(arguments)} failed: {completed.stderr}")
    return completed.stdout


class RevisionWorkspaceTests(unittest.TestCase):
    """在一个带 tag 的真实 git 仓库上验证按版本读取。"""

    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        _git(self.root, "init", "-q")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "t")

        (self.root / "frontend" / "src").mkdir(parents=True)
        (self.root / "frontend" / "src" / "App.tsx").write_text("// v1 content\n", encoding="utf-8")
        (self.root / "docs").mkdir()
        (self.root / "docs" / "spec.md").write_text("# v1 spec\n", encoding="utf-8")
        (self.root / ".env").write_text("SECRET=1\n", encoding="utf-8")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "dep.js").write_text("// dep\n", encoding="utf-8")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "v1")
        _git(self.root, "tag", "v1.0")

        # 版本发布后继续开发：工作区内容与 v1.0 不再相同。
        (self.root / "frontend" / "src" / "App.tsx").write_text("// v2 content\n", encoding="utf-8")
        (self.root / "docs" / "spec.md").write_text("# v2 spec\n", encoding="utf-8")
        (self.root / "frontend" / "src" / "New.tsx").write_text("// added later\n", encoding="utf-8")

    def _status(self) -> str:
        return _git(self.root, "status", "--porcelain")

    def test_file_read_returns_tag_content_not_working_tree(self) -> None:
        """核心承诺：读到的是 v1.0 当时的内容，而不是工作区里改过的内容。"""

        result = revision_file(self.root, "v1.0", "frontend/src/App.tsx")
        self.assertEqual(result["content"], "// v1 content\n")
        self.assertEqual(result["revision"], "v1.0")
        # 工作区里其实是 v2 的内容，必须没被读到。
        self.assertEqual(
            (self.root / "frontend/src/App.tsx").read_text(encoding="utf-8"), "// v2 content\n"
        )

    def test_tree_lists_tag_files_without_later_additions(self) -> None:
        tree = revision_tree(self.root, "v1.0", max_depth=6)
        names = self._flatten(tree["tree"])
        self.assertIn("frontend/src/App.tsx", names)
        self.assertIn("docs/spec.md", names)
        # 发布之后新增的文件不该出现在 v1.0 的树里。
        self.assertNotIn("frontend/src/New.tsx", names)

    def test_tree_root_name_is_workspace_directory_name(self) -> None:
        """根节点显示工作区目录名（与工作区读取口径一致），不能是 "."。"""

        tree = revision_tree(self.root, "v1.0", max_depth=6)
        self.assertEqual(tree["tree"]["name"], self.root.name)
        self.assertEqual(tree["tree"]["path"], ".")
        # 子目录根节点用该目录名。
        scoped = revision_tree(self.root, "v1.0", path="frontend", max_depth=6)
        self.assertEqual(scoped["tree"]["name"], "frontend")

    def test_tree_hides_ignored_and_hidden_entries(self) -> None:
        tree = revision_tree(self.root, "v1.0", max_depth=6)
        names = self._flatten(tree["tree"])
        self.assertNotIn("node_modules/dep.js", names, "依赖目录不应进树")
        self.assertNotIn(".env", names, "隐藏文件默认不进树")

    def test_reads_do_not_touch_the_working_tree(self) -> None:
        """绝不能用 checkout 实现：读取前后工作区必须逐字节不变、且不产生 git 变更。"""

        before_status = self._status()
        before_content = (self.root / "frontend/src/App.tsx").read_text(encoding="utf-8")
        before_head = _git(self.root, "rev-parse", "HEAD").strip()

        revision_tree(self.root, "v1.0", max_depth=6)
        revision_file(self.root, "v1.0", "frontend/src/App.tsx")
        revision_file(self.root, "v1.0", "docs/spec.md")

        self.assertEqual(self._status(), before_status, "读取后 git 状态发生了变化")
        self.assertEqual(
            (self.root / "frontend/src/App.tsx").read_text(encoding="utf-8"),
            before_content,
            "读取后工作区文件被改写",
        )
        self.assertEqual(_git(self.root, "rev-parse", "HEAD").strip(), before_head, "HEAD 被移动")

    def test_unknown_revision_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            revision_file(self.root, "v9.9", "docs/spec.md")
        self.assertIn(raised.exception.status_code, (400, 404))

    def test_revision_cannot_be_smuggled_as_git_option(self) -> None:
        for revision in ("--upload-pack=touch /tmp/x", "-x", ""):
            with self.assertRaises(HTTPException):
                revision_tree(self.root, revision)

    def test_path_traversal_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            revision_file(self.root, "v1.0", "../../etc/passwd")
        self.assertEqual(raised.exception.status_code, 403)

    def test_option_like_path_is_rejected(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            revision_file(self.root, "v1.0", "--output=/tmp/x")
        self.assertEqual(raised.exception.status_code, 400)

    def test_sensitive_file_is_rejected(self) -> None:
        """敏感文件按工作区读取同级拒绝，不因为换了来源就放松。"""

        with self.assertRaises(HTTPException) as raised:
            revision_file(self.root, "v1.0", ".env")
        self.assertEqual(raised.exception.status_code, 403)

    def test_missing_file_in_revision_is_not_found(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            revision_file(self.root, "v1.0", "frontend/src/New.tsx")
        self.assertEqual(raised.exception.status_code, 404)

    def test_tree_scoped_to_subdirectory(self) -> None:
        tree = revision_tree(self.root, "v1.0", path="frontend", max_depth=6)
        names = self._flatten(tree["tree"])
        self.assertIn("frontend/src/App.tsx", names)
        self.assertNotIn("docs/spec.md", names)

    @staticmethod
    def _flatten(node: dict) -> list[str]:
        collected: list[str] = []
        for child in node.get("children") or []:
            if child["kind"] == "directory":
                collected.extend(RevisionWorkspaceTests._flatten(child))
            else:
                collected.append(child["path"])
        return collected


if __name__ == "__main__":
    unittest.main()
