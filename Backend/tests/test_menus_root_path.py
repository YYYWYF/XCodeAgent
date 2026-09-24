"""页面路由拼接 `menus.rootPath` 必须幂等。

模型会看到上一轮的 RequirementSpec，于是常把已带前缀的路径（`/page/welcome-home`）
原样吐回来。无条件再拼一次就成了 `/page/page/welcome-home`，且每轮多叠一层 ——
既让路由错掉，也让"这一页的需求条目变没变"永远比较失败（治本方案依赖这个比较）。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from app.graph.nodes.requirements import _apply_menus_root_path_to_pages

_ROOT_PATH = "/page"


class _State(dict):
    """最小 ProjectState 替身：只需要 workspace。"""

    def __init__(self, workspace: Path) -> None:
        super().__init__(workspace=str(workspace))


def _workspace(root: Path, *, root_path: str = _ROOT_PATH, enable: bool = True) -> Path:
    devagentstudio = root / ".devagentstudio"
    devagentstudio.mkdir(parents=True, exist_ok=True)
    (devagentstudio / "application.json").write_text(
        json.dumps({"menus": {"enable": enable, "rootPath": root_path}}),
        encoding="utf-8",
    )
    return root


def _spec(*paths: str) -> dict:
    return {"pages": [{"pageId": f"page_{index}", "path": path} for index, path in enumerate(paths)]}


class MenusRootPathIdempotencyTests(unittest.TestCase):
    def _apply(self, spec: dict, *, root_path: str = _ROOT_PATH, enable: bool = True) -> dict:
        with tempfile.TemporaryDirectory() as raw:
            workspace = _workspace(Path(raw), root_path=root_path, enable=enable)
            _apply_menus_root_path_to_pages(spec, _State(workspace))
        return spec

    def test_prefixes_unprefixed_path(self) -> None:
        spec = self._apply(_spec("/welcome-home"))

        self.assertEqual(spec["pages"][0]["path"], "/page/welcome-home")

    def test_does_not_double_prefix(self) -> None:
        """核心回归：已带前缀的路径原样保留，不再叠加。"""

        spec = self._apply(_spec("/page/welcome-home"))

        self.assertEqual(spec["pages"][0]["path"], "/page/welcome-home")

    def test_is_idempotent_across_rounds(self) -> None:
        """连续跑多轮（模拟多轮迭代）路径保持不变。"""

        spec = _spec("/welcome-home")
        for _ in range(3):
            self._apply(spec)
        self.assertEqual(spec["pages"][0]["path"], "/page/welcome-home")

    def test_sibling_prefix_is_not_mistaken_for_prefixed(self) -> None:
        """`/page-something` 不是 `/page` 前缀，必须照常拼接。

        判据若只用 startswith(root_path)，这里会误判成"已带前缀"而漏拼。
        """

        spec = self._apply(_spec("/page-something"))

        self.assertEqual(spec["pages"][0]["path"], "/page/page-something")

    def test_exact_root_path_is_kept(self) -> None:
        spec = self._apply(_spec("/page"))

        self.assertEqual(spec["pages"][0]["path"], "/page")

    def test_relative_path_is_prefixed(self) -> None:
        spec = self._apply(_spec("welcome-home"))

        self.assertEqual(spec["pages"][0]["path"], "/page/welcome-home")

    def test_root_slash_means_no_prefixing(self) -> None:
        spec = self._apply(_spec("/welcome-home"), root_path="/")

        self.assertEqual(spec["pages"][0]["path"], "/welcome-home")

    def test_menu_enabled_root_page_gets_leaf_path(self) -> None:
        """启用菜单时首页类页面（path="/"）走叶子路径，不受本次改动影响。"""

        spec = self._apply(_spec("/"))

        self.assertTrue(spec["pages"][0]["path"].startswith("/page/"))
        self.assertNotEqual(spec["pages"][0]["path"], "/page")
