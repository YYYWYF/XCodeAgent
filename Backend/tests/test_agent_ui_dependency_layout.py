"""验证只读构建布局解析使用真实声明并拒绝歧义。"""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest


class DependencyLayoutTests(unittest.TestCase):
    """仅解析静态路径，不执行宿主配置或修改目标工程。"""

    def test_rsbuild_static_template_with_unrelated_dynamic_options(self) -> None:
        """模板其他字段的运行期表达式不改变明确的入口和输出。"""
        from app.services.agent_ui_dependency_layout import inspect_dependency_config
        value = inspect_dependency_config("""
import {defineConfig as config, type RsbuildConfig} from '@rsbuild/core';
export default config({plugins: [pluginReact()], source: {
 entry: {index: './src/index.tsx'}, define: clientDefines,
}, output: {distPath: {root: 'build'}}, server: {proxy: runtimeProxy}});
""", "rsbuild")
        self.assertEqual(value, {"entry": "src/index.tsx", "outputDir": "build"})

    def test_rejects_dynamic_or_ambiguous_config(self) -> None:
        """动态布局、多入口、重复键和非官方配置包装器都必须单独审阅。"""
        from app.services.agent_ui_dependency_layout import inspect_dependency_config
        for content in (
            "export default () => ({source: {entry: getEntry()}})",
            "export default {source: {entry: {a:'src/a.tsx', b:'src/b.tsx'}}}",
            "export default {source: {entry: entries}}",
            "export default {...base, source: {entry: {a:'src/a.tsx'}}}",
            "export default {output: {distPath: process.env.OUTPUT}}",
            "export default {output: {}, output: {}}",
            "export default defineConfig({})",
            "export default {source: {entry: {a: `src/${name}.tsx`}}}",
        ):
            with self.subTest(content=content), self.assertRaises(ValueError):
                inspect_dependency_config(content, "rsbuild")

    def test_vite_reads_html_and_rejects_missing_or_multiple_modules(self) -> None:
        """Vite 使用 HTML 中实际 module script，而不是按源码存在与否猜测。"""
        from app.services.agent_ui_dependency_layout import current_dependency_layout
        with TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'src').mkdir()
            (target / 'src/app.tsx').write_text('// entry')
            (target / 'vite.config.ts').write_text("export default {build: {outDir: 'output'}}")
            html = target / 'index.html'
            html.write_text('<script type="module" src="/src/app.tsx"></script>')
            package = {"scripts": {"build": "vite build"}}
            self.assertEqual(current_dependency_layout(target, package),
                             {"bundler": "vite", "entry": "src/app.tsx", "outputDir": "output"})
            for content in ('', '<script type="module">start()</script>',
                            '<script type="module" src="//external/x.tsx"></script>',
                            '<script type="module" src="/src/app.tsx"></script>' * 2):
                with self.subTest(content=content), self.assertRaises(ValueError):
                    html.write_text(content)
                    current_dependency_layout(target, package)

    def test_output_escape_and_source_overlaps_are_rejected(self) -> None:
        """构建产物不能越界或与源码、依赖目录混用。"""
        from app.services.agent_ui_dependency_layout import current_dependency_layout
        with TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'src').mkdir()
            (target / 'src/index.tsx').write_text('// entry')
            for output in ('../outside', '/outside', 'src', 'src/generated', 'node_modules', '.'):
                with self.subTest(output=output), self.assertRaises(ValueError):
                    (target / 'rsbuild.config.ts').write_text(
                        "export default {output: {distPath: {root: '" + output + "'}}}")
                    current_dependency_layout(target, {"scripts": {"build": "rsbuild build"}})

    def test_postcss_patch_preserves_unicode_and_rejects_custom_options(self) -> None:
        """按 UTF-8 AST 字节修改唯一目标字段，其他配置与未知选项保留审阅边界。"""
        from app.services.agent_ui_dependency_layout import rsbuild_postcss_changes
        with TemporaryDirectory() as directory:
            target = Path(directory)
            (target / 'postcss.config.js').write_text('export default {}')
            config = target / 'rsbuild.config.ts'
            prefix = "// 中文配置\nexport default {tools: {rspack: () => {}, "
            value = "postcss: {postcssOptions: {config: path.resolve(projectRoot, 'postcss.config.js')}}"
            config.write_text(prefix + value + '}};')
            result = rsbuild_postcss_changes(target)['rsbuild.config.ts']
            self.assertEqual(result, "// 中文配置\nexport default {tools: {rspack: () => {}}};")
            config.write_text('export default {tools: {\n    ' + value + ',\n    rspack: () => {}\n}};')
            self.assertEqual(rsbuild_postcss_changes(target)['rsbuild.config.ts'],
                             'export default {tools: {\n    rspack: () => {}\n}};')
            config.write_text(prefix + value.replace('postcssOptions: {', 'postcssOptions: {plugins: [], ') + '}};')
            with self.assertRaisesRegex(ValueError, '审阅'):
                rsbuild_postcss_changes(target)
