import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'
import rsbuildLoader from './agent_ui_rsbuild_loader.cjs'

// 使用设计消费者已有 TypeScript，只分析语法树，不加载或执行应用模块。
const input = JSON.parse(readFileSync(0, 'utf8'))
const ts = createRequire(path.resolve(input.projectRoot, 'package.json'))('typescript')
const result = {}
for (const [name, code] of Object.entries(input.files)) {
  const source = ts.createSourceFile(name, code, ts.ScriptTarget.Latest, true)
  const imports = []
  const errors = source.parseDiagnostics.map(() => 'TypeScript syntax error')
  const adapterErrors = []
  /** 记录完整字符串范围，使用 Unicode 码点偏移与 Python 保持一致。 */
  function record(node, kind = 'module') {
    if (!node || !ts.isStringLiteralLike(node)) {
      errors.push('computed module reference')
      return
    }
    imports.push({ specifier: node.text, kind,
      start: Array.from(code.slice(0, node.getStart(source))).length,
      end: Array.from(code.slice(0, node.getEnd())).length })
  }
  /** 精确检查 import/export、类型导入和 glob；注释、展示字符串不参与重写。 */
  function visit(node) {
    if (ts.isStringLiteralLike(node) && /^(?:https?:\/\/)?(?:localhost|127\.0\.0\.1|0\.0\.0\.0)(?::|\/|$)/i.test(node.text)) {
      errors.push('design-local endpoint must not enter the target')
    }
    if (ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) {
      if (node.moduleSpecifier) record(node.moduleSpecifier)
      if (node.moduleSpecifier?.text?.endsWith('?raw')) {
        try {
          if (!ts.isImportDeclaration(node)) throw new Error('raw re-export needs explicit adaptation')
          rsbuildLoader.rawImport(ts, node)
        } catch (error) { adapterErrors.push(error.message) }
      }
    } else if (ts.isImportEqualsDeclaration(node)) {
      errors.push('import-equals is unsupported')
    } else if (ts.isImportTypeNode(node)) {
      record(node.argument.literal)
    } else if (ts.isCallExpression(node)) {
      if (node.expression.kind === ts.SyntaxKind.ImportKeyword) record(node.arguments[0])
      else if (ts.isIdentifier(node.expression) && node.expression.text === 'require') {
        errors.push('require is unsupported')
      } else if (node.expression.getText(source) === 'import.meta.glob') {
        try { rsbuildLoader.globOptions(ts, node) } catch (error) { adapterErrors.push(error.message) }
        const argument = node.arguments[0]
        if (argument && ts.isArrayLiteralExpression(argument)) {
          for (const element of argument.elements) record(element, 'glob')
        } else record(argument, 'glob')
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  result[name] = { imports, errors: [...new Set(errors)], adapterErrors: [...new Set(adapterErrors)] }
}
process.stdout.write(JSON.stringify(result))
