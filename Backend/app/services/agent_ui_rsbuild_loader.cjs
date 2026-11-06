const fs = require('node:fs')
const path = require('node:path')
const { createRequire } = require('node:module')

/** 只接受明确的相对文件名，不展开模式或加载目标之外的资源。 */
function relativeFile(value) {
  if (typeof value !== 'string' || !value.startsWith('./') && !value.startsWith('../') ||
      /[\\*?{}[\]\x00-\x1f]/u.test(value)) throw new Error('Agent UI adapter requires an explicit relative file')
  return value
}

/** 公开薄应用层只需有限 eager glob；其他选项仍要求单独审阅。 */
function globOptions(ts, node) {
  const [pattern, options] = node.arguments
  const patterns = pattern && ts.isStringLiteralLike(pattern) ? [pattern] :
    pattern && ts.isArrayLiteralExpression(pattern) ? [...pattern.elements] : []
  if (node.arguments.length !== 2 || !patterns.length || !options || !ts.isObjectLiteralExpression(options)) {
    throw new Error('Agent UI adapter requires literal eager glob options')
  }
  const values = {}
  for (const item of options.properties) {
    if (!ts.isPropertyAssignment(item) || !ts.isIdentifier(item.name) && !ts.isStringLiteral(item.name)) {
      throw new Error('Agent UI adapter rejects computed glob options')
    }
    const key = item.name.text
    if (!['eager', 'query', 'import'].includes(key) || Object.hasOwn(values, key)) {
      throw new Error('Agent UI adapter rejects unknown or duplicate glob options')
    }
    values[key] = item.initializer.kind === ts.SyntaxKind.TrueKeyword ? true :
      ts.isStringLiteral(item.initializer) ? item.initializer.text : null
  }
  if (values.eager !== true || values.query !== undefined && values.query !== '?raw' ||
      values.import !== undefined && values.import !== 'default') throw new Error('Agent UI adapter rejects glob options')
  return { patterns: patterns.map(item => {
    if (!ts.isStringLiteralLike(item)) throw new Error('Agent UI adapter rejects computed glob paths')
    return relativeFile(item.text)
  }), raw: values.query === '?raw', defaultImport: values.import === 'default' }
}

/** 只支持原始文本默认导入，避免改变命名导出或动态导入语义。 */
function rawImport(ts, node) {
  const clause = node.importClause
  if (!clause?.name || clause.isTypeOnly || clause.namedBindings || node.attributes) {
    throw new Error('Agent UI adapter requires a raw default import')
  }
  return relativeFile(node.moduleSpecifier.text.slice(0, -4))
}

/** 在每次目标构建时读取当前文件，并跟踪新增/修改，绝不冻结设计工程的生成结果。 */
function agentUIRsbuildLoader(code) {
  this.cacheable?.()
  const ts = createRequire(path.join(this.rootContext, 'package.json'))('typescript')
  const source = ts.createSourceFile(this.resourcePath, code, ts.ScriptTarget.Latest, true)
  if (source.parseDiagnostics.length) throw new Error('Agent UI adapter received invalid TypeScript')
  const edits = [], imports = [], identifiers = new Set()
  let sequence = 0
  /** 避免生成 import 变量与用户现有标识符重名。 */
  function collect(node) {
    if (ts.isIdentifier(node)) identifiers.add(node.text)
    ts.forEachChild(node, collect)
  }
  collect(source)
  const boundary = fs.realpathSync(path.join(this.rootContext, 'src')) + path.sep
  /** 文件只能来自当前源码目录；缺少的可选资源加入编译器依赖跟踪。 */
  const locate = (specifier, optional) => {
    const filename = path.resolve(path.dirname(this.resourcePath), relativeFile(specifier))
    if (!filename.startsWith(boundary)) throw new Error('Agent UI resource escapes target source root')
    let current = filename
    while (!fs.existsSync(current)) current = path.dirname(current)
    if (!fs.realpathSync(current).startsWith(boundary) && fs.realpathSync(current) !== boundary.slice(0, -1)) {
      throw new Error('Agent UI resource resolves outside target source root')
    }
    if (!fs.existsSync(filename)) {
      if (!optional) throw new Error('Required Agent UI raw resource is missing')
      this.addMissingDependency(filename)
      return undefined
    }
    if (!fs.realpathSync(filename).startsWith(boundary) || !fs.statSync(filename).isFile()) {
      throw new Error('Agent UI resource is not a target source file')
    }
    this.addDependency(filename)
    return filename
  }
  /** 原始字节语义保留给官方组合摘要，不对 JSON 重排或重新序列化。 */
  const rawText = filename => {
    if (fs.statSync(filename).size > 200000) throw new Error('Agent UI raw resource is too large')
    return JSON.stringify(fs.readFileSync(filename, 'utf8'))
  }
  /** 修改 AST 对应的字符范围，展示文字、注释和 Unicode 不参与替换。 */
  const replace = (node, after) => edits.push({ start: node.getStart(source), end: node.getEnd(), after })
  /** 把已审阅的有限构建语法转换为 Rsbuild 可消费的标准模块。 */
  const visit = node => {
    if (ts.isImportDeclaration(node) && ts.isStringLiteral(node.moduleSpecifier) && node.moduleSpecifier.text.endsWith('?raw')) {
      replace(node, `const ${node.importClause?.name?.text} = ${rawText(locate(rawImport(ts, node), false))};`)
      return
    }
    if (ts.isCallExpression(node) && node.expression.getText(source) === 'import.meta.glob') {
      const options = globOptions(ts, node)
      const entries = []
      for (const specifier of [...new Set(options.patterns)]) {
        const filename = locate(specifier, true)
        if (!filename) continue
        let value
        if (options.raw) {
          value = options.defaultImport ? rawText(filename) : `{ default: ${rawText(filename)} }`
        } else {
          do { value = `__agentUIResource${sequence++}` } while (identifiers.has(value))
          imports.push(`import ${options.defaultImport ? value : `* as ${value}`} from ${JSON.stringify(specifier)};`)
        }
        entries.push(`${JSON.stringify(specifier)}: ${value}`)
      }
      replace(node, `({${entries.join(', ')}})`)
      return
    }
    if (ts.isPropertyAccessExpression(node)) {
      const text = node.getText(source)
      if (text === 'import.meta.env.DEV') { replace(node, String(this.mode === 'development')); return }
      if (text === 'import.meta.hot') {
        // Webpack 首次加载没有 hot.data；公开 Runtime 需要 Vite 的稳定对象及 dispose 端口。
        replace(node, '(import.meta.webpackHot ? { data: import.meta.webpackHot.data ?? {}, dispose: (callback) => import.meta.webpackHot.dispose(callback) } : undefined)')
        return
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
  let output = code
  for (const edit of edits.sort((a, b) => b.start - a.start)) output = output.slice(0, edit.start) + edit.after + output.slice(edit.end)
  return imports.join('\n') + '\n' + output
}

module.exports = Object.assign(agentUIRsbuildLoader, { globOptions, rawImport })
