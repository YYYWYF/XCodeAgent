import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import path from 'node:path'

const input = JSON.parse(readFileSync(0, 'utf8'))
const ts = createRequire(path.resolve(input.toolRoot, 'package.json'))('typescript')
const { files, entries, contract } = input
const protectedPaths = new Set(Object.keys(contract.evidence.ownership).map((name) => 'frontend/' + name))
const sharedModule = 'frontend/' + contract.evidence.sourceRoot + '/design/SharedApplication'
const errors = new Set()
const trees = new Map()
const imports = new Map()
const reachable = new Set()

/** 只解析当前目标源码的相对路径及模板 @ 别名，不读取或执行模块。 */
function resolve(from, specifier) {
  const base = specifier.startsWith('@/') ? 'frontend/src/' + specifier.slice(2)
    : specifier.startsWith('.') ? path.posix.normalize(path.posix.join(path.posix.dirname(from), specifier)) : ''
  if (!base) return ''
  return [base, ...['.tsx', '.ts', '.jsx', '.js', '/index.tsx', '/index.ts'].map((ext) => base + ext)]
    .find((name) => Object.hasOwn(files, name)) ?? base
}

/** 记录公开值导入，类型、注释和字符串不参与挂载判定。 */
function inspectImports(name, tree) {
  const bindings = new Map()
  for (const node of tree.statements) {
    if (!ts.isImportDeclaration(node) && !ts.isExportDeclaration(node)) continue
    const specifier = node.moduleSpecifier?.text
    if (!specifier || node.isTypeOnly || node.importClause?.isTypeOnly) continue
    const importedNames = node.importClause?.namedBindings
    if (importedNames && ts.isNamedImports(importedNames) && !node.importClause.name &&
        importedNames.elements.length && importedNames.elements.every((item) => item.isTypeOnly)) continue
    const destination = resolve(name, specifier)
    if (['frontend/src/extensions/AgentUiHostProvider', 'frontend/src/extensions/providers',
         'frontend/src/providers/AppProviders', 'frontend/src/App'].includes(destination.replace(/\.(tsx?|jsx?)$/, ''))) {
      errors.add(name + ': 页面不得重新挂载模板或共享 Host Provider')
    }
    if (/AgentConversation|agentConversation|@xcodeagent\/agent-ui-design/.test(specifier) ||
        specifier.startsWith('@za38.03/agent-ui-')) {
      errors.add(name + ': 页面或其他应用源码不得创建旧模板、包 Runtime 或 AG-UI Adapter')
    }
    if (specifier.startsWith('@ag-ui/')) inspectBusinessTransport(name, tree, node, specifier)
    if (destination.startsWith('frontend/' + contract.evidence.sourceRoot + '/')) {
      if (destination.replace(/\.(tsx?|jsx?)$/, '') !== sharedModule || !ts.isImportDeclaration(node)) {
        errors.add(name + ': 只能使用共享页面公开入口，不能导入共享内部实现')
      }
      const clause = node.importClause
      if (!clause?.namedBindings || !ts.isNamedImports(clause.namedBindings) || clause.name) {
        errors.add(name + ': 共享入口要求明确具名导入')
      } else {
        for (const item of clause.namedBindings.elements) {
          if (item.isTypeOnly) continue
          const original = (item.propertyName ?? item.name).text
          if (!['SharedAgentPage', 'useSharedAgentPanel'].includes(original)) errors.add(name + ': 非页面公开入口')
          bindings.set(item.name.text, original)
        }
      }
    }
  }
  imports.set(name, bindings)
}

/** 独立的正式业务 AG-UI 接口仍可使用官方 SDK，不授予共享会话入口发送权限。 */
function inspectBusinessTransport(name, tree, imported, specifier) {
  const bindings = imported.importClause?.namedBindings
  if (name.startsWith('frontend/src/apis/') && specifier === '@ag-ui/core' && bindings &&
      ts.isNamedImports(bindings) && !imported.importClause.name &&
      bindings.elements.every((item) => item.isTypeOnly || (item.propertyName ?? item.name).text === 'EventType')) return
  if (!name.startsWith('frontend/src/apis/') || specifier !== '@ag-ui/client' ||
      !bindings || !ts.isNamedImports(bindings) || imported.importClause.name ||
      bindings.elements.some((item) => !item.isTypeOnly && (item.propertyName ?? item.name).text !== 'HttpAgent')) {
    errors.add(name + ': AG-UI 传输仅允许独立正式业务 API 模块的公开 HttpAgent')
    return
  }
  const aliases = new Set(bindings.elements.filter((item) => !item.isTypeOnly).map((item) => item.name.text))
  let count = 0
  /** 每个业务 SDK 构造必须绑定正式静态 URL，不接受 spread 或动态地址。 */
  function inspect(node) {
    if (ts.isNewExpression(node) && aliases.has(node.expression.getText(tree))) {
      count++
      const config = node.arguments?.[0]
      const url = config && ts.isObjectLiteralExpression(config) && config.properties.find((item) => item.name?.getText(tree) === 'url')
      if (!url || !ts.isPropertyAssignment(url) || !ts.isStringLiteral(url.initializer) ||
          !contract.businessAguiPaths?.includes(url.initializer.text) || config.properties.some(ts.isSpreadAssignment)) {
        errors.add(name + ': 业务 AG-UI URL 未绑定正式业务合同')
      }
    }
    ts.forEachChild(node, inspect)
  }
  inspect(tree)
  if (!count) errors.add(name + ': 无可验证的正式业务 HttpAgent 构造')
}

/** 跟随当前页面静态依赖，动态模块不能提供可验证的共享入口。 */
function visit(name) {
  if (reachable.has(name) || protectedPaths.has(name)) return
  const tree = trees.get(name)
  if (!tree) return
  reachable.add(name)
  for (const node of tree.statements) {
    if ((ts.isImportDeclaration(node) && !node.importClause?.isTypeOnly ||
         ts.isExportDeclaration(node) && !node.isTypeOnly) && node.moduleSpecifier) {
      visit(resolve(name, node.moduleSpecifier.text))
    }
  }
}

/** 属性必须是当前 pageId 的静态字符串，不能用 spread 或动态表达式绕过绑定。 */
function pageIdMatches(node) {
  const attrs = node.attributes.properties
  if (attrs.some(ts.isJsxSpreadAttribute) || attrs.length !== 1) return false
  const attr = attrs[0]
  const value = attr.initializer
  if (!value) return false
  return attr.name.text === 'pageId' && (ts.isStringLiteral(value) && value.text === contract.pageId ||
    ts.isJsxExpression(value) && value.expression && ts.isStringLiteral(value.expression) && value.expression.text === contract.pageId)
}

/** 独立会话已经提供完整布局，只检查槽的祖先，普通业务卡片和浮窗不受影响。 */
function inspectStandaloneLayout(name, tree, slot) {
  if (contract.surface !== 'standalone_page') return
  const frames = new Set(['Card', 'ProCard', 'PageContainer'])
  for (const declaration of tree.statements) {
    if (!ts.isImportDeclaration(declaration)) continue
    const clause = declaration.importClause
    if (clause?.name && frames.has(clause.name.text)) frames.add(clause.name.text)
    const named = clause?.namedBindings
    if (named && ts.isNamedImports(named)) {
      for (const item of named.elements) {
        if (frames.has((item.propertyName ?? item.name).text)) frames.add(item.name.text)
      }
    }
  }
  /** 仅解析当前文件的静态对象引用，不执行样式表达式。 */
  function styleObject(expression) {
    if (!expression) return undefined
    if (ts.isObjectLiteralExpression(expression)) return expression
    if (!ts.isIdentifier(expression)) return undefined
    let value
    /** 定位局部样式常量；动态 CSS 的最终效果仍须 Electron 验收。 */
    function find(node) {
      if (ts.isVariableDeclaration(node) && ts.isIdentifier(node.name) &&
          node.name.text === expression.text && node.initializer && ts.isObjectLiteralExpression(node.initializer)) value = node.initializer
      ts.forEachChild(node, find)
    }
    find(tree)
    return value
  }
  for (let parent = slot.parent; parent; parent = parent.parent) {
    if (!ts.isJsxElement(parent) || parent.openingElement === slot) continue
    const opening = parent.openingElement
    const tag = opening.tagName.getText(tree).split('.').at(-1)
    if (frames.has(tag)) errors.add(name + ': 独立会话槽不得嵌入 Card/ProCard/PageContainer；请直接铺满应用内容区')
    for (const attr of opening.attributes.properties) {
      if (!ts.isJsxAttribute(attr)) continue
      const value = attr.initializer
      if (attr.name.text === 'className' && value && ts.isStringLiteral(value) &&
          value.text.split(/\s+/).some((token) => /^(?:max-w-|shadow(?:-|$)|rounded(?:-|$))/.test(token))) {
        errors.add(name + ': 独立会话槽外层不得使用限宽、阴影或圆角卡片样式')
      }
      if (attr.name.text !== 'style' || !value || !ts.isJsxExpression(value)) continue
      const object = styleObject(value.expression)
      if (!object) continue
      for (const property of object.properties) {
        if (!ts.isPropertyAssignment(property)) continue
        const key = property.name.getText(tree).replace(/['"]/g, '')
        const literal = property.initializer
        if (!ts.isStringLiteral(literal) && !ts.isNumericLiteral(literal)) continue
        if (['maxWidth', 'boxShadow', 'borderRadius'].includes(key) && !['0', 'none', 'unset', 'initial', '100%'].includes(literal.text)) {
          errors.add(name + ': 独立会话槽外层不得设置限宽、阴影或圆角卡片样式')
        }
      }
    }
  }
}

/** 检查真实 AST 调用与 JSX 引用，只提供静态接入证据，不宣称运行行为通过。 */
function inspectPage(name, tree) {
  const bindings = imports.get(name)
  const controls = new Set()
  const panels = new Set()
  let slots = 0, launchers = 0, businessTags = 0
  const agentPath = /\/(?:agents|agent|mock-agent)(?:\/|['"])/.test(files[name])
  /** 先收集公开 hook 的实际调用及解构别名。 */
  function collect(node) {
    if (ts.isVariableDeclaration(node) && node.initializer && ts.isCallExpression(node.initializer) &&
        bindings.get(node.initializer.expression.getText(tree)) === 'useSharedAgentPanel') {
      if (ts.isIdentifier(node.name)) panels.add(node.name.text)
      if (ts.isObjectBindingPattern(node.name)) {
        for (const item of node.name.elements) {
          if ((item.propertyName ?? item.name).getText(tree) === 'openPanel') controls.add(item.name.getText(tree))
        }
      }
    }
    ts.forEachChild(node, collect)
  }
  /** 分析事件表达式中对公开显隐控制器的引用。 */
  function hasOpen(node) {
    if (ts.isIdentifier(node) && controls.has(node.text)) return true
    if (ts.isPropertyAccessExpression(node) && panels.has(node.expression.getText(tree)) && node.name.text === 'openPanel') return true
    return ts.forEachChild(node, hasOpen) ?? false
  }
  /** 拒绝页面自建传输、动态加载和错误共享占位。 */
  function walk(node) {
    if (ts.isCallExpression(node) || ts.isNewExpression(node)) {
      const callee = node.expression.getText(tree)
      if (callee === 'import' || callee === 'require' || callee === 'import.meta.glob') errors.add(name + ': 动态模块需要明确审阅，不能静态验收')
      if ((entries.includes(name) || agentPath) && /(?:fetch|axios|XMLHttpRequest|WebSocket|EventSource|sendBeacon|HttpAgent)/.test(callee)) {
        errors.add(name + ': 页面不得自行创建 Agent 网络传输')
      }
    }
    if (ts.isJsxSelfClosingElement(node) || ts.isJsxOpeningElement(node)) {
      const tag = node.tagName.getText(tree)
      if (bindings.get(tag) === 'SharedAgentPage') {
        if (!pageIdMatches(node)) errors.add(name + ': 共享占位必须精确绑定当前 pageId')
        inspectStandaloneLayout(name, tree, node)
        slots++
      } else if (/^[a-z]/.test(tag) && !['button', 'span'].includes(tag)) businessTags++
      for (const attr of node.attributes.properties) {
        if (ts.isJsxAttribute(attr) && /^on[A-Z]/.test(attr.name.text) && attr.initializer && hasOpen(attr.initializer)) launchers++
      }
    }
    ts.forEachChild(node, walk)
  }
  collect(tree)
  walk(tree)
  return { slots, launchers, businessTags }
}

try {
  for (const [name, code] of Object.entries(files)) {
    if (protectedPaths.has(name)) continue
    const tree = ts.createSourceFile(name, code, ts.ScriptTarget.Latest, true, /x$/.test(name) ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
    trees.set(name, tree)
    if (tree.parseDiagnostics.length) errors.add(name + ': 源码语法无效')
    inspectImports(name, tree)
    /** 其他路由也不能通过动态包导入或复制旧会话实现避开唯一 owner。 */
    function checkApplication(node) {
      if ((ts.isCallExpression(node) && ['import', 'require'].includes(node.expression.getText(tree))) &&
          node.arguments.some((arg) => ts.isStringLiteral(arg) &&
            /@za38\.03\/agent-ui-|@ag-ui\/|AgentConversation|agentConversation/.test(arg.text))) {
        errors.add(name + ': 不得动态加载第二个会话 Runtime 或 Adapter')
      }
      if ((ts.isVariableDeclaration(node) || ts.isFunctionDeclaration(node) || ts.isClassDeclaration(node)) &&
          node.name && /^(AgentChatCore|AgentConversationAdapter|agentConversationMock)$/.test(node.name.getText(tree))) {
        errors.add(name + ': 不得复制旧会话实现')
      }
      ts.forEachChild(node, checkApplication)
    }
    checkApplication(tree)
  }
  entries.forEach(visit)
  const counts = [...reachable].map((name) => inspectPage(name, trees.get(name)))
  const slots = counts.reduce((sum, item) => sum + item.slots, 0)
  if (contract.surface === 'standalone_page' && slots !== 1) errors.add('独立页必须使用一个可达的共享页面占位')
  if (contract.surface === 'floating_panel' && (!counts.some((item) => item.launchers) || !counts.some((item) => item.businessTags) || slots)) {
    errors.add('浮窗页面必须保留业务主体并将用户事件接到共享显隐控制器')
  }
  process.stdout.write(JSON.stringify({ errors: [...errors], slots, files: [...reachable].sort() }))
} catch {
  process.stdout.write(JSON.stringify({ errors: ['共享页面 AST 分析失败，不能判定通过'] }))
}
