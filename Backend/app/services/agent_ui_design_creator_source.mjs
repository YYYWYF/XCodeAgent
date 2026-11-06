/** 区分候选代码越界与检查工具自身不可用，避免把基础设施错误归咎于用户需求。 */
class CreatorSourceBoundaryError extends Error {
  /** 为固定的设计范围失败附加稳定错误类型。 */
  constructor(message, code = 'source_boundary_failed') {
    super(message)
    this.code = code
  }
}

/** 只检查实际改动的应用层语法树，拒绝新增传输、存储或第二个聊天 Runtime。 */
export function validateCreatorSource(ts, file, code) {
  const source = ts.createSourceFile(file, code, ts.ScriptTarget.Latest, true,
    file.endsWith('.tsx') ? ts.ScriptKind.TSX : ts.ScriptKind.TS)
  if (source.parseDiagnostics.length) throw new CreatorSourceBoundaryError('invalid application source', 'source_syntax_failed')
  const forbidden = new Set(['fetch', 'XMLHttpRequest', 'WebSocket', 'EventSource', 'Worker',
    'SharedWorker', 'HttpAgent', 'ConversationRuntimeProvider', 'AgentApplication', 'AgentUIProvider',
    'eval', 'Function', 'require',
    'process', 'localStorage', 'sessionStorage', 'indexedDB', 'dangerouslySetInnerHTML'])
  /** 声明、别名导入和表达式都检查，注释或字符串不会触发误判。 */
  function visit(node) {
    if (ts.isIdentifier(node) && forbidden.has(node.text)) throw new CreatorSourceBoundaryError('non-presentation capability')
    if ((ts.isImportDeclaration(node) || ts.isExportDeclaration(node)) && node.moduleSpecifier) {
      const name = node.moduleSpecifier.text
      if (/^(?:node:|@ag-ui\/|@za38\.03\/agent-ui-creator)/.test(name)) throw new CreatorSourceBoundaryError('tool or transport import')
    }
    if (node.kind === ts.SyntaxKind.ImportKeyword || ts.isImportEqualsDeclaration(node)) {
      throw new CreatorSourceBoundaryError('dynamic application capability')
    }
    if ((ts.isJsxOpeningElement(node) || ts.isJsxSelfClosingElement(node))
        && ['style', 'script', 'link', 'iframe', 'object', 'embed'].includes(node.tagName.getText(source))) {
      throw new CreatorSourceBoundaryError('unscoped or executable application element')
    }
    ts.forEachChild(node, visit)
  }
  visit(source)
}
