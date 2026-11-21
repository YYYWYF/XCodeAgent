"""防止已删除的 V1 Reconcile 协议重新进入生产依赖图。"""

from __future__ import annotations

import re
import unittest
from pathlib import Path


_LEGACY_MODULES = frozenset({
    "applier", "diagnostics", "health", "models", "preflight", "recovery",
    "runtime_state", "update_package",
})


class TemplateReconcileNoLegacyImportsTests(unittest.TestCase):
    """以静态门禁阻止 V1 模块或 managedFiles 契约重返生产路径。"""

    def test_production_code_does_not_import_or_define_legacy_reconcile_contract(self) -> None:
        """当前生产树只能引用 V2 模块，且不得重新引入 V1 managedFiles State。"""

        app_root = Path(__file__).parents[1] / "app"
        violations: list[str] = []
        for path in app_root.rglob("*.py"):
            relative = path.relative_to(app_root).as_posix()
            text = path.read_text(encoding="utf-8")
            if "managedFiles" in text:
                violations.append(f"{relative}: managedFiles")
            # 精确匹配模块名：当前协议模块叫 update_package_v3 / update_package_v4，
            # 按子串判断会把它们误判成已删除的 V1 `update_package`。
            for module in _LEGACY_MODULES:
                if re.search(rf"template_reconcile\.{module}\b(?!_)", text):
                    violations.append(f"{relative}: template_reconcile.{module}")
        self.assertEqual([], violations, "V1 Reconcile 不得重新进入生产代码：" + "；".join(violations))
