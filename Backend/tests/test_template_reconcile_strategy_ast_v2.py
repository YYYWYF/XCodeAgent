"""覆盖冻结 astSelector 的受限定位与插入语义。"""

from __future__ import annotations

import unittest

from app.services.template_reconcile.strategy_ast_v2 import (
    StrategyAstV2Error,
    insert_after_last_import,
    insert_at_selector,
)


class StrategyAstV2Tests(unittest.TestCase):
    """确保 Template 输出的 astSelector 均使用当前 Executor 已支持的字段和语义。"""

    def test_selector_without_name_supports_before_and_after(self) -> None:
        """确认未提供 name 时仍可在唯一节点前后插入。"""

        source = "const routes = [];\n"
        before = insert_at_selector("routes.ts", source, {"nodeType": "lexical_declaration", "position": "before"}, "// before\n")
        after = insert_at_selector("routes.ts", source, {"nodeType": "lexical_declaration", "position": "after"}, "\n// after")
        self.assertEqual("// before\nconst routes = [];\n", before)
        self.assertEqual("const routes = [];\n// after\n", after)

    def test_selector_with_name_and_before_end_are_supported(self) -> None:
        """确认 name 精确收窄候选，beforeEnd 定位在唯一容器闭合符之前。"""

        source = "const first = 1;\nconst routes = [];\n"
        named = insert_at_selector("routes.ts", source, {"nodeType": "variable_declarator", "position": "after", "name": "routes"}, "// named")
        container = insert_at_selector("routes.ts", source, {"nodeType": "array", "position": "beforeEnd"}, " { path: '/login' }")
        self.assertIn("routes = []// named;", named)
        self.assertIn("[ { path: '/login' }]", container)

    def test_selector_rejects_invalid_position_and_non_unique_name_match(self) -> None:
        """确认非法 position 与无法唯一命中的 name 都无法被猜测执行。"""

        source = "const first = 1;\nconst second = 2;\n"
        with self.assertRaisesRegex(StrategyAstV2Error, "AST_SELECTOR_INVALID"):
            insert_at_selector("routes.ts", source, {"nodeType": "lexical_declaration", "position": "inside"}, "x")
        with self.assertRaisesRegex(StrategyAstV2Error, "AST_SELECTOR_AMBIGUOUS"):
            insert_at_selector("routes.ts", source, {"nodeType": "variable_declarator", "position": "after", "name": "missing"}, "x")

    def test_insert_after_last_import_supports_java_typescript_and_tsx(self) -> None:
        """确认各语言使用各自的 import AST 节点，并在最后一条 import 后插入。"""

        cases = (
            ("Config.java", "package example;\nimport java.util.List;\nclass Config {}\n", "import java.util.Map;\n", "import java.util.List;"),
            ("routes.ts", "import { createRoute } from './route';\nexport const routes = [];\n", "import { loginRoute } from './login';\n", "import { createRoute } from './route';"),
            ("capabilityProviders.tsx", "import type { PropsWithChildren } from 'react';\nexport const Provider = () => <div />;\n", "import { GlobalContextProvider } from '@/providers';\n", "import type { PropsWithChildren } from 'react';"),
        )
        for source_path, source, insertion, last_import in cases:
            with self.subTest(source_path=source_path):
                actual = insert_after_last_import(source_path, source, insertion)
                self.assertIn(last_import + "\n" + insertion, actual)

    def test_insert_after_last_import_rejects_source_without_import(self) -> None:
        """确认所有支持语言在确实没有 import 时仍以安全错误终止。"""

        for source_path, source in (("Config.java", "class Config {}\n"), ("routes.ts", "export const routes = [];\n"), ("Page.tsx", "export const Page = () => <div />;\n")):
            with self.subTest(source_path=source_path):
                with self.assertRaisesRegex(StrategyAstV2Error, "AST_IMPORT_TARGET_MISSING"):
                    insert_after_last_import(source_path, source, "import value from 'module';\n")
