import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const input = JSON.parse(readFileSync(0, 'utf8'))
const ts = createRequire(path.resolve(input.projectRoot, 'package.json'))('typescript')
const source = ts.createSourceFile('providers.ts', input.code, ts.ScriptTarget.Latest, true)
const provider = 'SharedAgentHostProvider'

/** 只承认当前模板真正装配并返回的扩展链，导入名称本身不构成挂载证据。 */
function assertTemplateHook() {
  const app = ts.createSourceFile('AppProviders.tsx', input.appProviders, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  const imports = app.statements.filter(ts.isImportDeclaration)
  /** 必须是未改名的值导入，防止类型导入或同名局部变量冒充公共入口。 */
  const importsValue = (module, name) => imports.some((item) => item.moduleSpecifier.text === module &&
    !item.importClause?.isTypeOnly && ts.isNamedImports(item.importClause?.namedBindings) &&
    item.importClause.namedBindings.elements.some((binding) => binding.name.text === name && !binding.propertyName && !binding.isTypeOnly))
  /** 去掉 JSX 外层的语法括号，不执行表达式。 */
  const unwrap = (node) => node && ts.isParenthesizedExpression(node) ? unwrap(node.expression) : node
  /** 当前模板将一个明确的值作为唯一 JSX 子节点。 */
  const wraps = (node, tag, value) => {
    node = unwrap(node)
    if (!node || !ts.isJsxElement(node) || node.openingElement.tagName.getText(app) !== tag ||
        node.closingElement.tagName.getText(app) !== tag || node.openingElement.attributes.properties.length) return false
    const children = node.children.filter((child) => !ts.isJsxText(child) || child.text.trim())
    return children.length === 1 && ts.isJsxExpression(children[0]) && children[0].expression?.getText(app) === value
  }
  const fn = app.statements.find((item) => ts.isFunctionDeclaration(item) && item.name?.text === 'AppProviders')
  const nodes = fn?.body?.statements ?? []
  const content = nodes.filter(ts.isVariableStatement).flatMap((item) => item.declarationList.declarations)
    .find((item) => ts.isCallExpression(item.initializer) && item.initializer.expression.getText(app) === 'extensionProviders.reduceRight')
  const call = content?.initializer
  const callback = call?.arguments[0]
  const returned = nodes.find(ts.isReturnStatement)?.expression
  if (app.parseDiagnostics.length || !importsValue('@/extensions/providers', 'extensionProviders') ||
      !importsValue('@/platform/identity/IdentityContext', 'IdentityProvider') ||
      fn?.parameters[0]?.name.getText(app).replace(/\s/g, '') !== '{children}' ||
      !content || !ts.isIdentifier(content.name) || call.arguments.length !== 2 || call.arguments[1].getText(app) !== 'children' ||
      !ts.isArrowFunction(callback) || callback.parameters.length !== 2 ||
      !wraps(callback.body, callback.parameters[1].name.getText(app), callback.parameters[0].name.getText(app)) ||
      !wraps(returned, 'IdentityProvider', content.name.text)) {
    throw new Error('模板未按当前公共 AppProviders 合同挂载扩展及身份边界；保留 Router/Shell')
  }
}

/** 只分析模板公开扩展数组，不执行目标模块或修改其他模板文件。 */
function projectRegistry() {
  assertTemplateHook()
  if (source.parseDiagnostics.length) throw new Error('Provider 注册表语法无效')
  const declarations = source.statements.filter(ts.isVariableStatement)
    .flatMap((statement) => statement.declarationList.declarations)
    .filter((node) => ts.isIdentifier(node.name) && node.name.text === 'extensionProviders')
  if (declarations.length !== 1 || !ts.isArrayLiteralExpression(declarations[0].initializer)) {
    throw new Error('extensionProviders 必须是唯一静态数组；保留当前注册表')
  }
  const array = declarations[0].initializer
  if (array.elements.some((node) => !ts.isIdentifier(node))) throw new Error('Provider 数组只支持明确的组件标识符')
  const imports = source.statements.filter(ts.isImportDeclaration)
    .filter((node) => node.moduleSpecifier.text === './AgentUiHostProvider')
  const registered = array.elements.filter((node) => ts.isIdentifier(node) && node.text === provider)
  if (imports.length) {
    const bindings = imports[0].importClause?.namedBindings
    if (imports.length !== 1 || imports[0].importClause.isTypeOnly || !bindings || !ts.isNamedImports(bindings) || bindings.elements.length !== 1 ||
        bindings.elements[0].isTypeOnly ||
        bindings.elements[0].name.text !== provider || bindings.elements[0].propertyName || registered.length !== 1) {
      throw new Error('已有 Host 注册无法唯一核对；保留当前文件')
    }
    return input.code
  }
  /** 拒绝与用户的同名符号冲突，注释和字符串不参与判断。 */
  function assertFree(node) {
    if (ts.isIdentifier(node) && node.text === provider) throw new Error('Host Provider 符号已被使用')
    ts.forEachChild(node, assertFree)
  }
  assertFree(source)
  const offset = array.getStart(source) + 1
  const addition = provider + (array.elements.length ? ', ' : '')
  return `import { ${provider} } from './AgentUiHostProvider'\n` +
    input.code.slice(0, offset) + addition + input.code.slice(offset)
}

try {
  process.stdout.write(JSON.stringify({ after: projectRegistry() }))
} catch (error) {
  process.stdout.write(JSON.stringify({ error: error.message }))
}
