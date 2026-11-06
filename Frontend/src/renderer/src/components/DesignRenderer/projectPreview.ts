export type AgentUiProjectPreview = {
  kind: 'agent-ui-project'
  workspace: string
  pageId: string
  revision: string
  launchId: string
  sourceDigest: string
  buildDigest: string
  source: 'official-mock'
  url: string
}

export type AgentUiDesignAsset = {
  kind: 'agent-ui-project'
  revision: string
  pageIds: string[]
  draft: string
  sourceDigest: string
  buildDigest: string
}

export type DesignPreviewPage = {
  pageId?: string
  name?: string
  code?: string
  status?: string
  error?: string
  design_asset?: AgentUiDesignAsset
  project_preview?: AgentUiProjectPreview
  preview_error?: string
  bindings?: { agent_surfaces?: unknown[] }
}

/** 后端状态优先；本地刚提交只代表等待接收，不能宣称模型已经开始执行。 */
export function designPageGenerationLabel(page: DesignPreviewPage, submitting = false): string | undefined {
  if (page.status === 'generating') return '生成中'
  if (page.status === 'queued') return '排队中'
  return submitting ? '提交中' : undefined
}

/** 只把当前 AG-UI launch 转成 iframe 地址，不接受文件、任意远端或旧版本链接。 */
export function projectPreviewUrl(
  value: unknown,
  expected: {
    workspace: string
    pageId: string
    revision: string
    sourceDigest: string
    buildDigest: string
  }
): string | undefined {
  if (!value || typeof value !== 'object') return undefined
  const preview = value as Partial<AgentUiProjectPreview>
  if (
    preview.kind !== 'agent-ui-project' ||
    preview.source !== 'official-mock' ||
    preview.workspace !== expected.workspace ||
    preview.pageId !== expected.pageId ||
    preview.revision !== expected.revision ||
    preview.sourceDigest !== expected.sourceDigest ||
    preview.buildDigest !== expected.buildDigest ||
    !/^[a-f0-9]{64}$/.test(preview.revision || '') ||
    !/^[a-f0-9]{32}$/.test(preview.launchId || '') ||
    !/^[a-f0-9]{64}$/.test(preview.sourceDigest || '') ||
    !/^[a-f0-9]{64}$/.test(preview.buildDigest || '') ||
    typeof preview.url !== 'string'
  )
    return undefined
  try {
    const url = new URL(preview.url)
    if (
      url.protocol !== 'http:' ||
      url.hostname !== '127.0.0.1' ||
      !url.port ||
      url.username ||
      url.password ||
      url.search ||
      url.hash ||
      url.pathname !== `/${preview.launchId}/` ||
      url.href !== preview.url
    )
      return undefined
    url.hash = new URLSearchParams({ pageId: expected.pageId }).toString()
    return url.href
  } catch {
    return undefined
  }
}

/** 消息还须由组件核对 event.source 和精确 origin；载荷不能授予任何文件或 IPC 权限。 */
export function projectPreviewMessageMatches(
  value: unknown,
  preview: Pick<AgentUiProjectPreview, 'launchId' | 'revision' | 'pageId'>
): boolean {
  if (!value || typeof value !== 'object') return false
  const message = value as Record<string, unknown>
  return (
    (message.type === 'agent-ui-design-ready' || message.type === 'agent-ui-design-error') &&
    message.launchId === preview.launchId &&
    message.revision === preview.revision &&
    message.pageId === preview.pageId
  )
}

/** 页面有工程或 Agent Surface 时绝不回到单文件编译器，即使磁盘还存在旧 TSX。 */
export function isAgentProjectPage(page: DesignPreviewPage): boolean {
  return Boolean(page.design_asset || page.project_preview || page.bindings?.agent_surfaces?.length)
}

/** 预览可用性同时覆盖普通单页与工程，不把 ready 误当正式确认。 */
export function hasDesignPreview(page: DesignPreviewPage): boolean {
  return isAgentProjectPage(page) ? Boolean(page.design_asset) : Boolean(page.code)
}

/** ready 工程可供用户正式确认；生成成功和正式确认仍保持独立状态。 */
export function isDesignReadyForConfirmation(page: DesignPreviewPage): boolean {
  return page.status === 'confirmed' || (
    page.status === 'ready' && page.design_asset?.kind === 'agent-ui-project' &&
    Boolean(page.pageId && page.design_asset.pageIds.includes(page.pageId))
  )
}
