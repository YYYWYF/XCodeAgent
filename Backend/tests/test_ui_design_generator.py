from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.config import Settings
from app.services.ui_design_generator import (
    _auto_fix_missing_imports,
    _build_ui_design_prompt,
    _extract_tsx_code,
    _find_undefined_refs,
    _is_likely_truncated,
    _merge_truncated_code,
    generate_page_react_code,
)


class UiDesignSettingsTests(unittest.TestCase):
    def test_settings_expose_ui_design_defaults(self) -> None:
        """UI 设计生成配置缺省时应提供足够的输出上限、一次修复机会与并发度。"""

        environment = {
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_API_KEY": "test-key",
            "MODEL_NAME": "test-model",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.ui_design_max_tokens, 32768)
        self.assertEqual(settings.ui_design_max_retries, 1)
        self.assertEqual(settings.ui_design_concurrency, 3)

    def test_settings_read_ui_design_environment_overrides(self) -> None:
        """UI 设计生成配置应允许通过独立环境变量覆盖默认值。"""

        environment = {
            "MODEL_BASE_URL": "https://example.test/v1",
            "MODEL_API_KEY": "test-key",
            "MODEL_NAME": "test-model",
            "XCODEAGENT_UI_DESIGN_MAX_TOKENS": "12288",
            "XCODEAGENT_UI_DESIGN_MAX_RETRIES": "3",
            "XCODEAGENT_UI_DESIGN_CONCURRENCY": "5",
        }
        with patch.dict(os.environ, environment, clear=True):
            settings = Settings.from_env()

        self.assertEqual(settings.ui_design_max_tokens, 12288)
        self.assertEqual(settings.ui_design_max_retries, 3)
        self.assertEqual(settings.ui_design_concurrency, 5)


class UiDesignGeneratorTests(unittest.TestCase):
    def test_prompt_declares_product_plan_as_only_product_fact_source(self) -> None:
        """UI 提示词必须明确禁止新增业务字段、操作、指标和正式路由。"""

        prompt = _build_ui_design_prompt(
            {
                "pageId": "orders",
                "name": "订单页",
                "information_items": [{"itemId": "orders-list", "label": "订单列表"}],
                "actions": [{"actionId": "search-orders", "name": "搜索订单"}],
            },
            "Orders",
        )

        self.assertIn("ProductPlan is the ONLY source of product facts", prompt)
        self.assertIn("Do not invent additional metrics", prompt)
        self.assertIn('data-preview-only="true"', prompt)

    def test_generation_binds_dedicated_ui_design_token_limit(self) -> None:
        """单页设计稿生成必须绑定 UI 设计专用输出上限。"""

        model = MagicMock()
        model.bind.return_value.invoke.return_value = SimpleNamespace(
            content="const MoviePage = () => <div>电影</div>; export default MoviePage;"
        )
        settings = SimpleNamespace(
            ui_design_max_tokens=12288,
            ui_design_max_retries=2,
        )
        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=settings,
        ), patch(
            "app.services.ui_design_generator.create_chat_model",
            return_value=model,
        ), patch(
            "app.services.ui_design_generator.validate_page_code",
            return_value=(True, ""),
        ):
            code = generate_page_react_code(
                {"pageId": "movie_page", "name": "电影页"},
                "MoviePage",
            )

        model.bind.assert_called_once_with(max_tokens=12288)
        self.assertIn("export default MoviePage", code)

    def test_generation_retries_transient_model_invocation_failure(self) -> None:
        """模型连接瞬断后应在同一页面生成调用内自动恢复。"""

        model = MagicMock()
        bound_model = model.bind.return_value
        bound_model.invoke.side_effect = [
            RuntimeError("Connection error."),
            SimpleNamespace(
                content="const MoviePage = () => <div>电影</div>; export default MoviePage;"
            ),
        ]
        settings = SimpleNamespace(
            ui_design_max_tokens=8192,
            ui_design_max_retries=2,
        )
        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=settings,
        ), patch(
            "app.services.ui_design_generator.create_chat_model",
            return_value=model,
        ), patch(
            "app.services.ui_design_generator.validate_page_code",
            return_value=(True, ""),
        ), patch("app.services.ui_design_generator.time.sleep"):
            code = generate_page_react_code(
                {"pageId": "movie_page", "name": "电影页"},
                "MoviePage",
            )

        self.assertEqual(bound_model.invoke.call_count, 2)
        self.assertIn("export default MoviePage", code)

    def test_truncated_output_is_continued_not_regenerated(self) -> None:
        """首次输出因 token 耗尽缺 export default 时，应断点续写而非整页重生成。

        回归：glm-5.2 的 thinking 与正文共享 max_tokens，复杂 ProTable 页常在
        写完前截断，网关又把截断伪装成 stop_reason=end_turn。续写把已生成的
        前半部分回喂让模型补完，比整页重生成省 thinking 预算且更可能成功。
        """

        partial = (
            "import React from 'react';\n"
            "import { ProTable } from '@ant-design/pro-components';\n"
            "const columns = [" + "'a'," * 80 + "];\n"  # 撑长度过 200 阈值
            + "const MoviePage = () => <div><ProTable columns={columns}"
        )
        continuation = " /></div>;\nexport default MoviePage;"
        model = MagicMock()
        bound_model = model.bind.return_value
        bound_model.invoke.side_effect = [
            SimpleNamespace(content=partial),       # 首次：截断，无 export default
            SimpleNamespace(content=continuation),  # 续写：补全
        ]
        settings = SimpleNamespace(ui_design_max_tokens=32768, ui_design_max_retries=1)
        with patch(
            "app.services.ui_design_generator.Settings.from_env",
            return_value=settings,
        ), patch(
            "app.services.ui_design_generator.create_chat_model",
            return_value=model,
        ), patch(
            "app.services.ui_design_generator.validate_page_code",
            side_effect=[(False, "缺少 export default"), (True, "")],
        ) as validate:
            code = generate_page_react_code(
                {"pageId": "movie_page", "name": "电影页"},
                "MoviePage",
            )

        # 只调 2 次：首次生成 + 一次续写，不触发整页 repair。
        self.assertEqual(bound_model.invoke.call_count, 2)
        self.assertIn("export default MoviePage", code)
        self.assertIn("<ProTable", code)
        # 续写的 prompt 必须带"从断点续写"指令与已生成的部分代码。
        continuation_prompt = bound_model.invoke.call_args_list[1][0][0]
        self.assertIn("CUT OFF", continuation_prompt)
        self.assertIn("MoviePage", continuation_prompt)
        # 续写成功后 validate 第二次收到的是拼接后的完整代码。
        completed_code_seen = validate.call_args_list[1][0][1]
        self.assertIn("export default MoviePage", completed_code_seen)


class TruncationDetectionTests(unittest.TestCase):
    def test_detects_truncation_by_missing_export_with_substantial_code(self) -> None:
        """有实质内容（import + 足够长度）但缺 export default 判截断。"""

        truncated = (
            "import React from 'react';\nimport { ProTable } from 'pro';\n"
            + "const data = [" + "1," * 100 + "];\nconst P = () => <div>"
        )
        self.assertTrue(_is_likely_truncated(truncated))

    def test_complete_code_is_not_truncated(self) -> None:
        """含 export default 的完整代码不判截断。"""

        complete = "import React from 'react';\n" + "const x = 1;\n" * 50 + "export default P;"
        self.assertFalse(_is_likely_truncated(complete))

    def test_short_or_empty_output_is_not_truncated(self) -> None:
        """空或过短输出不判截断（走 repair 而非续写）。"""

        self.assertFalse(_is_likely_truncated(""))
        self.assertFalse(_is_likely_truncated("import x"))
        self.assertFalse(_is_likely_truncated("a" * 500))  # 无 import/JSX 结构


class MergeTruncatedCodeTests(unittest.TestCase):
    def test_appends_continuation_and_dedupes_overlap(self) -> None:
        """正常续写应拼接，且去掉首尾重叠的重复字符。"""

        partial = "import React from 'react';\nconst P = () => <div><ProTable"
        tail = "ProTable columns={[]} /></div>;\nexport default P;"
        merged = _merge_truncated_code(partial, tail)
        self.assertEqual(merged.count("<ProTable"), 1)
        self.assertIn("export default P;", merged)

    def test_prefers_tail_when_model_rewrote_full_file(self) -> None:
        """续写模型若无视指令重整份重写（自带 import+export），直接采用尾部。"""

        partial = "import React from 'react';\nconst P = () => <div><ProTab"
        rewritten = "import React from 'react';\nconst P = () => <div />;\nexport default P;"
        self.assertEqual(_merge_truncated_code(partial, rewritten), rewritten)

    def test_empty_tail_returns_partial(self) -> None:
        """续写为空时保留原 partial，不破坏后续 repair 的输入。"""

        partial = "import React from 'react';\nconst P = () => <div>"
        self.assertEqual(_merge_truncated_code(partial, ""), partial)


class ExtractTsxCodeTests(unittest.TestCase):
    def test_extracts_pure_code_with_multiple_imports(self) -> None:
        """纯代码（多 import）应完整保留，不能从最后一个 import 截断。"""

        text = (
            "import React from 'react';\n"
            "import { Button } from 'antd';\n"
            "const App = () => <Button />;\n"
            "export default App;\n"
        )

        code = _extract_tsx_code(text)

        self.assertIn("import React from 'react';", code)
        self.assertIn("import { Button } from 'antd';", code)
        self.assertIn("export default App;", code)
        self.assertTrue(code.lstrip().startswith("import React"))

    def test_extracts_multiline_imports_without_truncation(self) -> None:
        """多行 import（import {\\n  Button,\\n} from 'antd';）不能截断前面的 import。"""

        text = (
            "import React, { useState } from 'react';\n"
            "import { ProCard } from '@ant-design/pro-components';\n"
            "import {\n"
            "  Button,\n"
            "  Row,\n"
            "  Col,\n"
            "  Space,\n"
            "  Tag,\n"
            "  Typography,\n"
            "  List,\n"
            "  Avatar,\n"
            "  Empty,\n"
            "  Result,\n"
            "  Segmented,\n"
            "  Card,\n"
            "  Skeleton,\n"
            "  Statistic,\n"
            "} from 'antd';\n"
            "import {\n"
            "  PlusOutlined,\n"
            "  ProjectOutlined,\n"
            "  FileTextOutlined,\n"
            "} from '@ant-design/icons';\n"
            "\n"
            "type ProjectStatus = '进行中' | '已完成';\n"
            "\n"
            "const DashboardPage: React.FC = () => {\n"
            "  return <div><Button /><ProCard /></div>;\n"
            "};\n"
            "export default DashboardPage;\n"
        )

        code = _extract_tsx_code(text)

        # All three import blocks must be present
        self.assertIn("import React, { useState } from 'react';", code)
        self.assertIn("import { ProCard } from '@ant-design/pro-components';", code)
        self.assertIn("} from 'antd';", code)
        self.assertIn("} from '@ant-design/icons';", code)
        # Components from the first multi-line import must be present
        self.assertIn("Button,", code)
        self.assertIn("Statistic,", code)
        # Icons from the second multi-line import must be present
        self.assertIn("PlusOutlined,", code)
        self.assertIn("FileTextOutlined,", code)
        # Code body and export
        self.assertIn("export default DashboardPage;", code)
        self.assertIn("<Button />", code)
        # Must NOT start partway through (e.g. from the last import only)
        self.assertTrue(code.lstrip().startswith("import React"))

    def test_extracts_code_after_reasoning_prose(self) -> None:
        """推理模型先输出思考过程再输出代码时，应取末尾的真代码而非思考里的 import。"""

        text = (
            "1. 分析：需要导入 React 与 antd，组件导出为 export default MyButton。\n"
            "2. 结构：\n"
            "   import React from 'react';\n"
            "最终代码：\n"
            "import { Button } from 'antd';\n"
            "const MyButton = () => <Button />;\n"
            "export default MyButton;\n"
        )

        code = _extract_tsx_code(text)

        self.assertIn("export default MyButton;", code)
        self.assertNotIn("分析", code)
        self.assertNotIn("最终代码", code)
        self.assertTrue(code.lstrip().startswith("import { Button }"))

    def test_prefers_last_export_default_fence_block(self) -> None:
        """思考里的示例围栏在前、真代码无围栏在后时，应取末尾含 export default 的真代码。"""

        text = (
            "思路：\n"
            "```tsx\n"
            "import Fake from 'fake';\n"
            "export default FakeComp;\n"
            "```\n"
            "最终代码：\n"
            "import Real from 'real';\n"
            "const R = () => 1;\n"
            "export default R;\n"
        )

        code = _extract_tsx_code(text)

        self.assertIn("export default R;", code)
        self.assertNotIn("FakeComp", code)
        self.assertNotIn("Fake", code)

    def test_returns_fence_block_when_real_code_is_fenced(self) -> None:
        """真代码本身包在围栏里时，应返回围栏内的代码。"""

        text = "Here is the code:\n```tsx\nimport React from 'react';\nexport default A;\n```\n"

        code = _extract_tsx_code(text)

        self.assertIn("export default A;", code)
        self.assertNotIn("Here is the code", code)

    def test_falls_back_when_no_export_default(self) -> None:
        """缺少 export default（如被截断）时走兜底，仍返回以 import 开头的代码。"""

        text = "import React from 'react';\nconst A = () => 1;\n"

        code = _extract_tsx_code(text)

        self.assertTrue(code.lstrip().startswith("import React"))


class AutoFixImportsTests(unittest.TestCase):
    """程序化补 import：glm-5.2 长代码任务 thinking 占满 token，import 语句常被截断丢失。

    校验发现"组件未 import"时按映射表确定性补回，不消耗模型 token，避免 repair 重试
    再次因 thinking 截断而失败。
    """

    def test_adds_missing_antd_imports(self) -> None:
        """代码完整但漏 antd 组件 import 时，程序化补回缺失组件。"""

        code = (
            "import React from 'react';\n"
            "const Home = () => <div><Card><Button>ok</Button><Tag>x</Tag></Card></div>;\n"
            "export default Home;\n"
        )
        self.assertEqual(_find_undefined_refs(code), ["Button", "Card", "Tag"])

        fixed, unresolved = _auto_fix_missing_imports(code)

        self.assertEqual(_find_undefined_refs(fixed), [])
        self.assertEqual(unresolved, [])
        self.assertIn("import { Button, Card, Tag } from 'antd';", fixed)

    def test_separates_antd_and_pro_components(self) -> None:
        """混合来源的缺失组件按 antd / pro-components 分别补到对应 import。"""

        code = (
            "import React from 'react';\n"
            "const P = () => <div><ProTable /><Button>x</Button></div>;\n"
            "export default P;\n"
        )
        fixed, unresolved = _auto_fix_missing_imports(code)

        self.assertEqual(_find_undefined_refs(fixed), [])
        self.assertEqual(unresolved, [])
        self.assertIn("from 'antd'", fixed)
        self.assertIn("Button", fixed)
        self.assertIn("from '@ant-design/pro-components'", fixed)
        self.assertIn("ProTable", fixed)

    def test_merges_into_existing_import_block(self) -> None:
        """已有同来源 import 块时，缺失组件合并进去而非新建 import。"""

        code = (
            "import React from 'react';\n"
            "import { Button } from 'antd';\n"
            "const X = () => <div><Button /><Input /><Tag /></div>;\n"
            "export default X;\n"
        )
        fixed, _ = _auto_fix_missing_imports(code)

        # 只有一行 antd import，且包含全部三个组件。
        antd_imports = [
            line for line in fixed.splitlines() if "from 'antd'" in line and "import" in line
        ]
        self.assertEqual(len(antd_imports), 1)
        for name in ("Button", "Input", "Tag"):
            self.assertIn(name, antd_imports[0])

    def test_unmapped_components_left_unresolved(self) -> None:
        """映射表未命中的组件（自定义组件、写错的图标名）留给模型 repair。"""

        code = (
            "import React from 'react';\n"
            "const X = () => <div><ReviewItem /><CloseCircle /></div>;\n"
            "export default X;\n"
        )
        fixed, unresolved = _auto_fix_missing_imports(code)

        # ReviewItem 是自定义组件、CloseCircle 是写错的图标名（应为 CloseCircleOutlined），
        # 都不在映射表，不自动补，留给模型 repair。
        self.assertIn("ReviewItem", unresolved)
        self.assertIn("CloseCircle", unresolved)

    def test_no_undefined_returns_unchanged(self) -> None:
        """代码无未定义引用时原样返回。"""

        code = (
            "import React from 'react';\n"
            "import { Button } from 'antd';\n"
            "const X = () => <Button />;\n"
            "export default X;\n"
        )
        fixed, unresolved = _auto_fix_missing_imports(code)
        self.assertEqual(fixed, code)
        self.assertEqual(unresolved, [])


if __name__ == "__main__":
    unittest.main()
