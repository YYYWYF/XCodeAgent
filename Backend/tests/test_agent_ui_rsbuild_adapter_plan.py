"""验证 Rsbuild 适配计划只读、保护宿主配置，并可与已有类型声明合并。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from app.services.agent_ui_dependency_layout import _configuration, _fields
from app.services.agent_ui_dependency_plan import REPOSITORY
from app.services.agent_ui_rsbuild_adapter import LOADER_PATH, prepare_rsbuild_adapter


class RsbuildAdapterPlanTests(unittest.TestCase):
    """使用临时目标验证受控计划，不初始化或修改用户正式工程。"""

    def setUp(self) -> None:
        """准备含 Unicode 注释和独立 rspack 配置的目标。"""
        self.temp = TemporaryDirectory(prefix='Rsbuild 计划 中文 ')
        self.target = Path(self.temp.name).resolve()
        self.package = self.target / 'package.json'
        self.package.write_text(json.dumps({'devDependencies': {'typescript': '5.7.3'}}))
        self.config = self.target / 'rsbuild.config.ts'
        self.rspack = 'rspack: (_config) => { /* 中文😀：宿主源码插件 */ }'
        self.original = (
            "import { defineConfig } from '@rsbuild/core';\n"
            '// 中文😀：保留已有构建行为\n'
            "export default defineConfig({source: {entry: {index: './src/index.tsx'}}, "
            'tools: {' + self.rspack + '}});\n')
        self.config.write_text(self.original, encoding='utf-8')

    def tearDown(self) -> None:
        """释放测试创建的隔离目录。"""
        self.temp.cleanup()

    def snapshot(self) -> dict[str, bytes]:
        """比较目标全部普通文件字节，确认计划失败或成功都没有执行写入。"""
        return {path.relative_to(self.target).as_posix(): path.read_bytes()
                for path in self.target.rglob('*') if path.is_file()}

    def apply_test_plan(self, files: dict[str, str]) -> None:
        """只在临时夹具模拟已审阅计划落盘，以验证再次规划的幂等性。"""
        for relative, content in files.items():
            path = self.target / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding='utf-8')

    def test_read_only_plan_adds_three_reviewable_files_and_preserves_rspack(self) -> None:
        """新增独立编译链，不覆写已有 rspack 或把生成应用层直接改写。"""
        before = self.snapshot()
        files = prepare_rsbuild_adapter(self.target, 'src/助手')
        self.assertEqual(set(files), {'rsbuild.config.ts', LOADER_PATH, 'src/助手/rsbuild.d.ts'})
        self.assertEqual(self.snapshot(), before)
        output = files['rsbuild.config.ts']
        self.assertIn('// 中文😀：保留已有构建行为', output)
        self.assertIn(self.rspack, output)
        self.assertIn('"./src/助手/"', output)
        self.assertIn('fileURLToPath', output)
        tools = _fields(_fields(_configuration(output, 'rsbuild'))['tools'])
        self.assertEqual(set(tools), {'rspack', 'bundlerChain'})
        self.assertIn(".enforce('pre')", tools['bundlerChain'].text.decode())

    def test_applied_exact_plan_is_idempotent(self) -> None:
        """精确已审阅配置再次规划不重复插入 import、规则或声明。"""
        first = prepare_rsbuild_adapter(self.target, 'src/助手')
        self.apply_test_plan(first)
        before = self.snapshot()
        second = prepare_rsbuild_adapter(self.target, 'src/助手')
        self.assertEqual(second, first)
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(second['rsbuild.config.ts'].count('import { fileURLToPath'), 1)

    def test_absent_tools_creates_only_the_required_tools_field(self) -> None:
        """原配置没有 tools 时增加独立对象，保留其他配置字段。"""
        self.config.write_text("export default {output: {distPath: {root: 'build'}}};")
        before = self.snapshot()
        files = prepare_rsbuild_adapter(self.target, 'src/助手')
        fields = _fields(_configuration(files['rsbuild.config.ts'], 'rsbuild'))
        self.assertEqual(set(fields), {'output', 'tools'})
        self.assertEqual(fields['output'].text, b"{distPath: {root: 'build'}}")
        self.assertEqual(set(_fields(fields['tools'])), {'bundlerChain'})
        self.assertEqual(self.snapshot(), before)

    def test_existing_custom_chain_loader_or_declarations_are_not_taken_over(self) -> None:
        """未知用户修改保留原字节，不能当成已拥有文件而强行覆盖。"""
        cases = (
            ('rsbuild.config.ts', 'export default {tools: {bundlerChain: chain => chain}};'),
            (LOADER_PATH, '// user owned loader\n'),
            ('src/助手/rsbuild.d.ts', '// user owned declarations\n'),
        )
        for relative, content in cases:
            with self.subTest(relative=relative):
                self.config.write_text(self.original, encoding='utf-8')
                path = self.target / relative
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(content, encoding='utf-8')
                before = self.snapshot()
                with self.assertRaisesRegex(ValueError, '审阅'):
                    prepare_rsbuild_adapter(self.target, 'src/助手')
                self.assertEqual(self.snapshot(), before)
                if relative != 'rsbuild.config.ts':
                    path.unlink()

    def test_missing_typescript_is_rejected_without_adding_dependency(self) -> None:
        """适配依赖目标已有 TypeScript，不擅自安装新的生产或开发依赖。"""
        self.package.write_text('{}')
        before = self.snapshot()
        with self.assertRaisesRegex(ValueError, 'TypeScript'):
            prepare_rsbuild_adapter(self.target, 'src/助手')
        self.assertEqual(self.snapshot(), before)

    def test_declared_metadata_merges_with_existing_rsbuild_import_meta(self) -> None:
        """真实 tsc 检查宿主环境声明与适配声明，避免 skipLibCheck 掩盖类型冲突。"""
        files = prepare_rsbuild_adapter(self.target, 'src/助手')
        # 宿主已有 ImportMetaEnv 和 ImportMeta，适配只能声明合并而不能替换属性类型。
        (self.target / 'host-env.d.ts').write_text('''interface ImportMetaEnv {
  readonly DEV: boolean;
  readonly PROD: boolean;
  readonly MODE: string;
}
interface ImportMeta { readonly env: ImportMetaEnv; }
''')
        declaration = self.target / 'src/助手/rsbuild.d.ts'
        declaration.parent.mkdir(parents=True)
        declaration.write_text(files['src/助手/rsbuild.d.ts'])
        (self.target / 'consumer.ts').write_text('''import raw from './model.json?raw';
export const value: string = raw;
export const dev: boolean = import.meta.env.DEV;
export const prod: boolean = import.meta.env.PROD;
export const revisions = import.meta.glob<string>('./revision.json', {
  eager: true, query: '?raw', import: 'default',
});
''')
        (self.target / 'tsconfig.json').write_text(json.dumps({'compilerOptions': {
            'strict': True, 'skipLibCheck': False, 'target': 'ES2022', 'module': 'ESNext',
            'moduleResolution': 'Bundler', 'types': [], 'noEmit': True,
        }, 'files': ['host-env.d.ts', 'src/助手/rsbuild.d.ts', 'consumer.ts']}))
        node = shutil.which('node')
        self.assertIsNotNone(node)
        tsc = REPOSITORY / 'Frontend/node_modules/typescript/bin/tsc'
        result = subprocess.run([node, str(tsc), '-p', str(self.target / 'tsconfig.json')],
                                text=True, capture_output=True, timeout=30, check=False)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)

