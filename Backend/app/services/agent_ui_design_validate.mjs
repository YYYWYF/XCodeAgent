import { readFileSync } from 'node:fs'
import { createRequire } from 'node:module'
import { resolve } from 'node:path'

// 在实际消费者中解析已有 TypeScript；只分析 AST，不执行模型源码。
const [projectRoot, inputPath] = process.argv.slice(2)
const requireFromTarget = createRequire(resolve(projectRoot, 'package.json'))
const ts = requireFromTarget('typescript')
const files = JSON.parse(readFileSync(inputPath, 'utf8'))
const hooks = new Set(['useState', 'useMemo', 'useCallback', 'useReducer', 'useId', 'Fragment'])
const forbidden = new Set([
  'fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'Worker', 'SharedWorker',
  'window', 'document', 'navigator', 'location', 'globalThis', 'self', 'top', 'parent',
  'localStorage', 'sessionStorage', 'indexedDB', 'eval', 'Function', 'process', 'require',
  'setTimeout', 'setInterval', 'importScripts', '__proto__', 'constructor', 'prototype',
  'dangerouslySetInnerHTML', 'innerHTML', 'outerHTML',
])
const forbiddenTags = new Set(['script', 'iframe', 'object', 'embed', 'link', 'meta', 'base', 'form'])
const errors = []
const panelActions = {}
const panelCallbacks = new Map([['onOpenAgentPanel', 'open'], ['onCloseAgentPanel', 'close']])

/** 使用完整语法树检查 import、浏览器能力与 JSX，拒绝旧虚拟模块和额外消息 owner。 */
function inspect(path, code) {
  const actions = { open: [], close: [], navigate: [] }
  panelActions[path] = actions
  const source = ts.createSourceFile(path, code, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX)
  if (source.parseDiagnostics.length) errors.push(`${path}: TSX syntax error`)
  if (/data-agent-part|AgentChatCore|AgentConversationTemplate|AgentFloatingPanelTemplate/.test(code)) {
    errors.push(`${path}: page-local or old fixed Agent UI is forbidden`)
  }
  /** 递归读取实际节点，注释和字符串不能伪装成 import 或绕过动态导入限制。 */
  function visit(node) {
    if (ts.isImportDeclaration(node)) {
      if (node.moduleSpecifier.text !== 'react') errors.push(`${path}: only react imports are allowed`)
      const clause = node.importClause
      if (clause?.name || (clause?.namedBindings && ts.isNamespaceImport(clause.namedBindings))) {
        errors.push(`${path}: use named React imports only`)
      }
      for (const element of clause?.namedBindings?.elements ?? []) {
        if (!clause.isTypeOnly && !element.isTypeOnly && !hooks.has((element.propertyName ?? element.name).text)) {
          errors.push(`${path}: React runtime import is outside the presentation allowlist`)
        }
      }
    }
    if (ts.isExportDeclaration(node) && node.moduleSpecifier) errors.push(`${path}: re-exports are forbidden`)
    if (ts.isImportEqualsDeclaration(node) || ts.isImportTypeNode(node) || ts.isMetaProperty(node)
        || node.kind === ts.SyntaxKind.ImportKeyword) errors.push(`${path}: dynamic module access is forbidden`)
    if (ts.isIdentifier(node) && forbidden.has(node.text)) errors.push(`${path}: forbidden capability ${node.text}`)
    if (ts.isElementAccessExpression(node)) errors.push(`${path}: computed object access is outside the design subset`)
    if (ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node)) {
      if (forbiddenTags.has(node.tagName.getText(source))) errors.push(`${path}: unsafe element`)
      for (const attribute of node.attributes.properties) {
        if (ts.isJsxSpreadAttribute(attribute)) errors.push(`${path}: JSX spread is outside the design subset`)
        if (ts.isJsxAttribute(attribute) && ['src', 'srcDoc', 'href', 'action', 'formAction'].includes(attribute.name.getText(source))) {
          errors.push(`${path}: external resources and navigation belong to the Host`)
        }
        if (ts.isJsxAttribute(attribute) && attribute.name.getText(source) === 'onClick'
            && attribute.initializer && ts.isJsxExpression(attribute.initializer)
            && attribute.initializer.expression && ts.isIdentifier(attribute.initializer.expression)) {
          const role = panelCallbacks.get(attribute.initializer.expression.text)
          if (role) {
            const action = node.attributes.properties.find((item) => ts.isJsxAttribute(item)
              && item.name.getText(source) === 'data-action-id')?.initializer
            if (node.tagName.getText(source) !== 'button' || !action || !ts.isStringLiteral(action)) {
              errors.push(`${path}: panel callbacks require a button with a static action ID`)
            } else actions[role].push(action.text)
          }
        }
      }
      const click = node.attributes.properties.find((item) => ts.isJsxAttribute(item) && item.name.text === 'onClick')
      const expression = click?.initializer && ts.isJsxExpression(click.initializer) ? click.initializer.expression : undefined
      const call = expression && ts.isArrowFunction(expression) ? expression.body : undefined
      if (call && ts.isCallExpression(call) && ts.isIdentifier(call.expression) && call.expression.text === 'onNavigatePage') {
        const action = node.attributes.properties.find((item) => ts.isJsxAttribute(item) && item.name.text === 'data-action-id')?.initializer
        if (node.tagName.getText(source) !== 'button' || !action || !ts.isStringLiteral(action) ||
            call.arguments.length !== 1 || !ts.isStringLiteral(call.arguments[0])) {
          errors.push(`${path}: navigation needs a button with static action and page IDs`)
        } else actions.navigate.push({ actionId: action.text, pageId: call.arguments[0].text })
      }
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
}

for (const [path, code] of Object.entries(files)) inspect(path, code)
if (errors.length) {
  process.stderr.write([...new Set(errors)].slice(0, 30).join('\n'))
  process.exitCode = 1
} else process.stdout.write(JSON.stringify(panelActions))
