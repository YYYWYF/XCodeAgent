import { MIN_ASSISTANT_PANEL_RATIO, MIN_RIGHT_PANEL_RATIO } from './constants'
import type {
  DevelopmentPlanningApiEndpoint,
  DevelopmentPlanningPageOption,
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'
import { normalizePreviewUrl } from '../../utils/previewUrl'

export type WorkflowPreviewTarget = {
  key: string
  url: string
}

export type WorkflowFinalResultPresentation = {
  failed: boolean
  title: string
}

export type GroupedWorkspaceCodeChange = {
  path: string
  additions: number
  deletions: number
  changeType: WorkspaceCodeChangeFile['changeType']
  changes: WorkspaceCodeChangeFile[]
}

export type WorkspaceCodeChangeSummary = {
  files: number
  additions: number
  deletions: number
}

export type WorkspacePathParts = {
  directory: string
  fileName: string
}

type DetailSessionIdentity = {
  apiContractId?: string
  endpointId?: string
  pageId?: string
}

/** 生成页面详情目标键，供临时运行状态按页面隔离。 */
export function pageDetailTargetKey(pageId: string): string {
  return pageId ? `page:${pageId}` : ''
}

/** 生成接口详情目标键，供临时运行状态按接口隔离。 */
export function endpointDetailTargetKey(apiContractId: string, endpointId: string): string {
  return apiContractId && endpointId ? `endpoint:${apiContractId}:${endpointId}` : ''
}

/** 生成 API 大纲的相对展示路径，仅移除完整匹配的 base path 前缀。 */
export function apiEndpointDisplayPath(endpointPath: string, basePath: string): string {
  const normalizedEndpointPath = endpointPath.trim() || '/'
  const normalizedBasePath = basePath.trim().replace(/\/+$/, '') || '/'
  if (normalizedBasePath === '/' || normalizedEndpointPath === normalizedBasePath) {
    return normalizedEndpointPath === normalizedBasePath ? '/' : normalizedEndpointPath
  }
  if (!normalizedEndpointPath.startsWith(`${normalizedBasePath}/`)) {
    return normalizedEndpointPath
  }
  return normalizedEndpointPath.slice(normalizedBasePath.length) || '/'
}

/** 从持久化会话归属生成详情目标键。 */
export function sessionDetailTargetKey(session: DetailSessionIdentity | undefined): string {
  if (session?.apiContractId && session.endpointId) {
    return endpointDetailTargetKey(session.apiContractId, session.endpointId)
  }
  return pageDetailTargetKey(session?.pageId || '')
}

/** 从 Workflow 快照读取详情目标键，避免历史运行状态串到当前页面。 */
export function workflowDetailTargetKey(workflow: unknown): string {
  if (!workflow || typeof workflow !== 'object') return ''
  const payload = workflow as {
    state?: Record<string, unknown>
    result?: Record<string, unknown>
  }
  const state = payload.state || {}
  const result = payload.result || {}
  const apiContractId = String(
    state.selectedApiContractId ||
      state.selected_api_contract_id ||
      result.selectedApiContractId ||
      result.selected_api_contract_id ||
      ''
  ).trim()
  const endpointId = String(
    state.selectedEndpointId ||
      state.selected_endpoint_id ||
      result.selectedEndpointId ||
      result.selected_endpoint_id ||
      ''
  ).trim()
  if (apiContractId && endpointId) {
    return endpointDetailTargetKey(apiContractId, endpointId)
  }
  const pageId = String(
    state.selectedPageId ||
      state.selected_page_id ||
      result.selectedPageId ||
      result.selected_page_id ||
      ''
  ).trim()
  return pageDetailTargetKey(pageId)
}

/** 将工作区相对路径拆分为目录和文件名，供窄面板优先展示文件名。 */
export function splitWorkspacePath(path: string): WorkspacePathParts {
  const normalizedPath = path.replaceAll('\\', '/')
  const separatorIndex = normalizedPath.lastIndexOf('/')
  if (separatorIndex < 0) return { directory: '', fileName: normalizedPath }
  return {
    directory: normalizedPath.slice(0, separatorIndex),
    fileName: normalizedPath.slice(separatorIndex + 1)
  }
}

/** 拼接工作区根目录名称和文件相对路径，并兼容缺少 workspaceName 的旧记录。 */
export function workspaceCodeChangeDisplayPath(
  filePath: string,
  workspaceRoot: string,
  workspaceName?: string
): string {
  const normalizedPath = filePath.replaceAll('\\', '/').replace(/^\.\//, '')
  const normalizedRoot = workspaceRoot.replaceAll('\\', '/').replace(/\/+$/, '')
  const derivedWorkspaceName = normalizedRoot.split('/').filter(Boolean).at(-1) || ''
  const rootName = workspaceName?.trim() || derivedWorkspaceName
  if (!rootName || normalizedPath === rootName || normalizedPath.startsWith(`${rootName}/`)) {
    return normalizedPath
  }
  return `${rootName}/${normalizedPath}`
}

export function formatSessionTime(value: number): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return '未知时间'
  return date.toLocaleString('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  })
}

export function clampAssistantPanelRatio(nextRatio: number): number {
  const maxRatio = Math.max(
    MIN_ASSISTANT_PANEL_RATIO,
    1 - MIN_RIGHT_PANEL_RATIO,
  )

  return Math.min(Math.max(nextRatio, MIN_ASSISTANT_PANEL_RATIO), maxRatio)
}

/** 判断变更路径是否属于工作目录内的 Agent 内部状态目录。 */
export function isInternalWorkspaceChangePath(path: string): boolean {
  const segments = path
    .replaceAll('\\', '/')
    .split('/')
    .filter((segment) => Boolean(segment) && segment !== '.')
  return segments.includes('.xcodeagent')
}

/** 过滤内部状态文件，并按路径合并同一次运行中的多段文件变更。 */
export function groupWorkspaceCodeChanges(
  files: WorkspaceCodeChangeFile[]
): GroupedWorkspaceCodeChange[] {
  const grouped = new Map<string, GroupedWorkspaceCodeChange>()

  files.forEach((file) => {
    if (isInternalWorkspaceChangePath(file.path)) return

    const current = grouped.get(file.path)
    if (!current) {
      grouped.set(file.path, {
        path: file.path,
        additions: file.additions,
        deletions: file.deletions,
        changeType: file.changeType,
        changes: [file]
      })
      return
    }

    current.additions += file.additions
    current.deletions += file.deletions
    current.changes.push(file)
    if (current.changeType !== 'deleted') current.changeType = file.changeType
  })

  return Array.from(grouped.values())
}

/** 汇总过滤后的可审阅文件数量与增删行数。 */
export function summarizeWorkspaceCodeChanges(
  files: GroupedWorkspaceCodeChange[]
): WorkspaceCodeChangeSummary {
  return files.reduce<WorkspaceCodeChangeSummary>(
    (summary, file) => ({
      files: summary.files + 1,
      additions: summary.additions + file.additions,
      deletions: summary.deletions + file.deletions
    }),
    { files: 0, additions: 0, deletions: 0 }
  )
}

export function stoppedAnswer(content: string): string {
  const trimmedContent = content.trim()
  return trimmedContent ? `${trimmedContent}\n\n_已停止生成。_` : '_已停止生成。_'
}

export function workflowCodeChanges(
  workflow: WorkflowRunPayload | undefined
): WorkspaceCodeChangeSet | undefined {
  if (!workflow) return undefined
  if (workflow.codeChanges?.files?.length) return workflow.codeChanges

  const stateCodeChanges = workflow.state?.codeChanges
  if (
    stateCodeChanges &&
    typeof stateCodeChanges === 'object' &&
    Array.isArray((stateCodeChanges as Partial<WorkspaceCodeChangeSet>).files) &&
    (stateCodeChanges as Partial<WorkspaceCodeChangeSet>).files?.length
  ) {
    return stateCodeChanges as WorkspaceCodeChangeSet
  }

  return undefined
}

/** 根据 Workflow 最终状态生成结果标题，避免失败运行被标记为任务完成。 */
export function workflowFinalResultPresentation(
  workflow: WorkflowRunPayload | undefined
): WorkflowFinalResultPresentation {
  const failed = workflow?.summary.status === 'failed'
  return {
    failed,
    title: failed ? '任务执行失败' : '任务已完成'
  }
}

/** 从实时且成功的启动节点中提取一次性预览导航目标。 */
export function workflowPreviewTarget(
  workflow: WorkflowRunPayload | undefined,
  live: boolean
): WorkflowPreviewTarget | undefined {
  const conversationChangeCompleted =
    workflow?.summary.phase === 'conversation' &&
    workflow?.summary.intent === 'workspace_change' &&
    workflow.summary.status === 'completed'
  const workflowLaunchReady =
    workflow?.summary.phase === 'launch_project' &&
    workflow.summary.status === 'requires_user_input'
  if (!live || !workflow || (!workflowLaunchReady && !conversationChangeCompleted)) {
    return undefined
  }

  const url = normalizePreviewUrl(workflow.summary.previewUrl || '')
  if (!url) return undefined
  return {
    key: `${workflow.threadId}:${workflow.runId}:${url}`,
    url
  }
}

/** 进入开发阶段时先要求用户选择开发目标，已有页面设计也允许重新选择页面继续开发。 */
export function requiresInitialDetailDesignSelection(hasPageDesigns: boolean): boolean {
  void hasPageDesigns
  return true
}

/** 页面视觉由 React UI 稿负责，因此页面永远不再要求 PageDetail 门禁。 */
export function requiresPageDetailDesign(
  page: DevelopmentPlanningPageOption | undefined
): boolean {
  void page
  return false
}

/** 以当前接口的落盘详情状态判断是否需要锁定对话区。 */
export function requiresEndpointDetailDesign(
  endpoint: DevelopmentPlanningApiEndpoint | undefined
): boolean {
  return Boolean(endpoint && !endpoint.designed && !endpoint.hasDetailPlan)
}
