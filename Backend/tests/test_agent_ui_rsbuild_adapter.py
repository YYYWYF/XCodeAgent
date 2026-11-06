"""验证 Rsbuild 构建适配读取目标实时资源，并保持有限 eager glob 的语义。"""

from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest

from app.services.agent_ui_dependency_plan import REPOSITORY


_LOADER = REPOSITORY / "Backend/app/services/agent_ui_rsbuild_loader.cjs"
_HARNESS = r"""
const fs = require('node:fs');
const path = require('node:path');
const {createRequire} = require('node:module');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const dependencies = [], missing = [];
try {
  const loader = require(input.loader);
  const transformed = loader.call({
    rootContext: input.root, resourcePath: input.filename,
    cacheable() {}, addDependency(value) {dependencies.push(value)},
    addMissingDependency(value) {missing.push(value)},
    emitError(error) {throw error},
  }, input.code);
  let value;
  if (input.evaluate) {
    const ts = createRequire(path.join(input.root, 'package.json'))('typescript');
    const seen = new Map();
    function execute(code, filename) {
      const output = ts.transpileModule(code, {compilerOptions: {
        module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020,
      }}).outputText;
      const module = {exports: {}};
      const localRequire = (specifier) => {
        const resolved = path.resolve(path.dirname(filename), specifier);
        if (resolved.endsWith('.json')) return JSON.parse(fs.readFileSync(resolved, 'utf8'));
        if (!seen.has(resolved)) seen.set(resolved, execute(fs.readFileSync(resolved, 'utf8'), resolved));
        return seen.get(resolved);
      };
      new Function('require', 'module', 'exports', output)(localRequire, module, module.exports);
      return module.exports;
    }
    value = execute(transformed, input.filename).value;
  }
  process.stdout.write(JSON.stringify({transformed, dependencies, missing, value}));
} catch (error) {
  process.stdout.write(JSON.stringify({error: error.message, dependencies, missing}));
}
"""


class RsbuildAdapterTests(unittest.TestCase):
    """在临时目标中调用真实 loader，不执行用户应用单测或写入正式工程。"""

    def setUp(self) -> None:
        """使用项目已有 Node/TypeScript 安装创建独立构建上下文。"""
        self.temp = TemporaryDirectory(prefix="Rsbuild 转入 中文 ")
        self.root = Path(self.temp.name).resolve()
        (self.root / "package.json").write_text('{}')
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules/typescript").symlink_to(
            REPOSITORY / "Frontend/node_modules/typescript", target_is_directory=True)
        self.filename = self.root / 'src/助手/application/project-config.tsx'
        self.filename.parent.mkdir(parents=True)

    def tearDown(self) -> None:
        """释放本用例的临时目标。"""
        self.temp.cleanup()

    def write_target(self, relative: str, content: str) -> Path:
        """按当前 target sourceRoot 创建构建时才读取的资源。"""
        path = self.filename.parent / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
        return path.resolve()

    def load(self, code: str, *, evaluate: bool = True) -> dict:
        """传递真实 loader 上下文并取回转换结果、依赖记录和可执行导出。"""
        node = shutil.which('node')
        if not node:
            self.fail('The existing Node runtime is required for Rsbuild adapter verification.')
        completed = subprocess.run([node, '-e', _HARNESS], input=json.dumps({
            'loader': str(_LOADER), 'root': str(self.root), 'filename': str(self.filename),
            'code': code, 'evaluate': evaluate,
        }), text=True, capture_output=True, timeout=20, check=True)
        return json.loads(completed.stdout)

    def test_raw_reads_current_target_bytes_and_registers_watch_dependency(self) -> None:
        """raw 字符串保留目标当前 UTF-8 字节，不冻结设计侧旧 JSON 或丢失空白。"""
        path = self.write_target('../app-ui.json', '{\n "label": "中文😀"\n}\n')
        code = "import model from '../app-ui.json?raw'; export const value = model;"
        first = self.load(code)
        self.assertNotIn('error', first, first)
        self.assertEqual(first['value'], path.read_text())
        self.assertIn(str(path), first['dependencies'])
        path.write_text('{ "label": "目标的新配置" }\n')
        second = self.load(code)
        self.assertNotIn('error', second, second)
        self.assertEqual(second['value'], path.read_text())
        self.assertNotEqual(first['value'], second['value'])

    def test_finite_eager_glob_preserves_keys_namespaces_and_optional_resources(self) -> None:
        """目标生成文件晚于转入出现时仍被消费，glob 键和 namespace export 保持原义。"""
        revision = self.write_target('../composition-revision.generated.json', '{"revision":"target"}\n')
        resource = '../agent-contract/frontend-tools.generated.ts'
        code = ("const revisions = import.meta.glob<string>('../composition-revision.generated.json', "
                "{eager: true, query: '?raw', import: 'default'});\n"
                f"const resources = import.meta.glob([{json.dumps(resource)}], {{eager: true}});\n"
                "export const value = {revisions, resources};")
        first = self.load(code)
        self.assertNotIn('error', first, first)
        self.assertEqual(first['value'], {'revisions': {
            '../composition-revision.generated.json': revision.read_text()}, 'resources': {}})
        resource_path = (self.filename.parent / resource).resolve()
        self.assertIn(str(resource_path), first['missing'])
        self.write_target(resource, 'export const marker = "目标生成"; export default "默认导出";')
        second = self.load(code)
        self.assertNotIn('error', second, second)
        self.assertEqual(second['value']['resources'], {
            resource: {'marker': '目标生成', 'default': '默认导出'}})
        self.assertIn(str(resource_path), second['dependencies'])

    def test_dynamic_wildcard_lazy_and_unknown_options_are_rejected(self) -> None:
        """有限 eager 适配不能顺便接管动态模式、通配、lazy 或未知查询语义。"""
        self.write_target('../app-ui.json', '{}')
        expressions = (
            "import.meta.glob(pattern, {eager: true})",
            "import.meta.glob('../*.json', {eager: true})",
            "import.meta.glob('../app-ui.json', {eager: false})",
            "import.meta.glob('../app-ui.json', {eager: true, custom: true})",
            "import.meta.glob('../app-ui.json', {...options, eager: true})",
            "import.meta.glob('../app-ui.json', {eager: true, query: '?url'})",
        )
        for expression in expressions:
            with self.subTest(expression=expression):
                result = self.load('export const value = ' + expression, evaluate=False)
                self.assertIn('error', result, expression)

    def test_comments_and_display_strings_are_not_treated_as_module_operations(self) -> None:
        """Unicode 字节偏移、注释及展示用字符串不会被误替换或产生文件依赖。"""
        self.write_target('../app-ui.json', '{"value":"正确"}')
        prefix = ('// 中文😀 import fake from "../missing?raw"; import.meta.glob("../fake", {eager:true})\n'
                  'const label = "中文😀 import.meta.glob ?raw";\n')
        result = self.load(prefix + "import model from '../app-ui.json?raw'; "
                           "export const value = {label, model};")
        self.assertNotIn('error', result, result)
        self.assertIn(prefix, result['transformed'])
        self.assertEqual(result['value'], {
            'label': '中文😀 import.meta.glob ?raw', 'model': '{"value":"正确"}'})
        self.assertEqual(result['missing'], [])

    def test_vite_environment_and_hot_access_are_adapted(self) -> None:
        """正式应用层不能残留目标构建器无法提供的 Vite 环境访问。"""
        result = self.load('export const value = {development: import.meta.env.DEV, hot: import.meta.hot};',
                           evaluate=False)
        self.assertNotIn('error', result, result)
        self.assertNotIn('import.meta.env.DEV', result['transformed'])
        self.assertNotIn('import.meta.hot', result['transformed'])

