"""PageId、PageKey 和页面入口路径的 canonical 规则回归。"""

from __future__ import annotations

import unittest

from app.services.page_identity import canonical_page_entry_path, page_id_to_page_key


class PageIdentityTests(unittest.TestCase):
    """验证页面身份转换只由正式 pageId 决定。"""

    def test_page_id_to_page_key_uses_canonical_pascal_case(self) -> None:
        """下划线和短横线 pageId 均转换为动态 PascalCase PageKey。"""

        self.assertEqual(page_id_to_page_key("product_detail"), "ProductDetail")
        self.assertEqual(page_id_to_page_key("order_list"), "OrderList")
        self.assertEqual(page_id_to_page_key("user-profile"), "UserProfile")

    def test_page_entry_path_is_derived_from_page_id(self) -> None:
        """页面入口路径必须由 canonical PageKey 动态生成。"""

        self.assertEqual(
            canonical_page_entry_path("product_detail"),
            "frontend/src/pages/ProductDetail/index.tsx",
        )

    def test_invalid_page_id_fails_closed(self) -> None:
        """非法页面身份不得回退成默认 Page 或被静默清洗。"""

        for value in ("", " product_detail", "product/detail", None):
            with self.subTest(value=value), self.assertRaises(ValueError):
                page_id_to_page_key(value)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
