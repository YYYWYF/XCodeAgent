import { MIN_ASSISTANT_PANEL_RATIO, MIN_RIGHT_PANEL_RATIO } from './constants'
import type {
  DevelopmentPlanningApiEndpoint,
  DevelopmentPlanningEntityOption,
  DevelopmentPlanningPageOption,
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'
import { normalizePreviewUrl } from '../../utils/previewUrl'
import type { WorkbenchPhase } from '../../workbenchPhase'

export type WorkflowPreviewTarget = {
  key: string
  url: string
}

export type WorkflowFinalResultPresentation = {
  failed: boolean
  terminal: boolean
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

export type WorkspaceCodeChangeSection<T> = {
  title: string
  files: T[]
}

export type WorkspacePathParts = {
  directory: string
  fileName: string
}

/** 生成页面详情目标键，供临时运行状态按页面隔离。 */
export function pageDetailTargetKey(pageId: string): string {
  return pageId ? `page:${pageId}` : ''
}

/** 生成接口详情目标键，供临时运行状态按接口隔离。 */
export function endpointDetailTargetKey(apiContractId: string, endpointId: string): string {
  return apiContractId && endpointId ? `endpoint:${apiContractId}:${endpointId}` : ''
}

/** 生成实体详情目标键，供临时运行状态按实体隔离。 */
export function entityDetailTargetKey(entityId: string): string {
  return entityId ? `entity:${entityId}` : ''
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
  const entityId = String(
    state.selectedEntityId ||
      state.selected_entity_id ||
      result.selectedEntityId ||
      result.selected_entity_id ||
      ''
  ).trim()
  if (entityId) {
    return entityDetailTargetKey(entityId)
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
  const maxRatio = Math.max(MIN_ASSISTANT_PANEL_RATIO, 1 - MIN_RIGHT_PANEL_RATIO)

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

/** 判断变更路径是否属于前后端单元测试文件。 */
export function isWorkspaceTestFilePath(path: string): boolean {
  const normalized = path.replaceAll('\\', '/').toLowerCase()
  const fileName = normalized.split('/').pop() || ''
  const frontendTest = /\.(test|spec)\.(ts|tsx|js|jsx)$/.test(fileName)
  const backendTest = /(?:^|\/)src\/test\/java\/.+\.java$/.test(normalized)
  return frontendTest || backendTest
}

/** 把可审阅文件拆成业务代码和测试文件两组，业务代码始终在前。 */
export function splitWorkspaceCodeChanges<T>(
  files: T[],
  pathOf: (item: T) => string
): WorkspaceCodeChangeSection<T>[] {
  const sections: [T[], T[]] = [[], []]
  files.forEach((item) => {
    sections[isWorkspaceTestFilePath(pathOf(item)) ? 1 : 0].push(item)
  })
  return [
    { title: '业务代码', files: sections[0] },
    { title: '测试文件', files: sections[1] }
  ].filter((section) => section.files.length > 0)
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

/** 仅在开发交接与自由修改结果中展示代码差异，测试、审查和验收阶段始终隐藏。 */
export function workflowShouldShowCodeChanges(workflow: WorkflowRunPayload | undefined): boolean {
  if (!workflow) return true

  return ['conversation', 'test_phase_confirmation'].includes(String(workflow.summary.phase || ''))
}

/** 仅在代码审查及进入验收的确认门展示审查结果，验收会话不继承审查卡片。 */
export function workflowShouldShowCodeReview(workflow: WorkflowRunPayload | undefined): boolean {
  if (!workflow) return false
  const phase = String(workflow.summary.phase || '')
  if (phase === 'code_review') return workflow.summary.status !== 'failed'
  if (phase !== 'acceptance_phase_confirmation') return false
  const candidates: unknown[] = [
    workflow.summary.codeReviewResult,
    workflow.state?.codeReviewResult,
    workflow.state?.code_review_result,
    workflow.result?.codeReviewResult,
    workflow.result?.code_review_result
  ]
  return candidates.some(
    (value) =>
      Boolean(value) &&
      typeof value === 'object' &&
      !Array.isArray(value) &&
      Object.keys(value as Record<string, unknown>).length > 0
  )
}

/** 验收主节点运行时即展示启动卡片，并用子节点事件或启动结果覆盖流式快照差异。 */
export function workflowShouldShowProjectLaunch(
  workflow: WorkflowRunPayload | undefined,
  activePhase: WorkbenchPhase
): boolean {
  if (!workflow || activePhase !== 'acceptance') return false
  const phase = String(workflow.summary.phase || '')
  if (phase === 'launch_project') return true
  if (phase !== 'acceptance') return false
  if (workflow.summary.status === 'running') return true
  if (Object.keys(workflow.summary.launchResult || {}).length > 0) return true
  return workflow.events.some(
    (event) => String(event.nodeName || event.node?.id || '') === 'launch_project'
  )
}

/** 开发完成确认需要先展示 Build Diff，再展示进入测试阶段的确认卡。 */
export function workflowCodeChangesBeforeConfirmation(
  workflow: WorkflowRunPayload | undefined
): boolean {
  return workflow?.summary.phase === 'test_phase_confirmation'
}

/** 根据 Workflow 最终状态生成结果标题，避免失败运行被标记为任务完成。 */
export function workflowFinalResultPresentation(
  workflow: WorkflowRunPayload | undefined
): WorkflowFinalResultPresentation {
  const status = workflow?.summary.status
  const failed = status === 'failed'
  const completed = status === 'completed' || status === 'passed'
  return {
    failed,
    terminal: failed || completed,
    title: failed ? '任务执行失败' : completed ? '任务已完成' : ''
  }
}

/** 从实时且成功的启动节点中提取一次性预览导航目标。 */
export function workflowPreviewTarget(
  workflow: WorkflowRunPayload | undefined,
  live: boolean
): WorkflowPreviewTarget | undefined {
  const conversationChangeCompleted =
    workflow?.summary.phase === 'conversation' &&
    workflow?.summary.intent === 'implementation_fix' &&
    workflow.summary.status === 'completed'
  const workflowLaunchReady =
    ['launch_project', 'acceptance'].includes(String(workflow?.summary.phase || '')) &&
    workflow?.summary.status === 'requires_user_input'
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

/** 仅在当前工作区还没有任何已持久化设计时显示首次详细设计目标选择器。 */
export function requiresInitialDetailDesignSelection(hasPageDesigns: boolean): boolean {
  return !hasPageDesigns
}

type DevelopmentTargetSelectorState = {
  developmentEntrySelectionPending: boolean
  developmentPlanningReady: boolean
  detailConfirmationWaitingReview: boolean
  detailProgressVisible: boolean
  freeChatSelected: boolean
  hasActiveDetailWorkflow: boolean
  initialDetailDesignSelectionRequired: boolean
  isApplicationPlanningPhase: boolean
}

/** 判断开发对象选择器是否应接管主区域；显式进入开发必须优先于残留会话状态。 */
export function shouldShowDevelopmentTargetSelector({
  developmentEntrySelectionPending,
  developmentPlanningReady,
  detailConfirmationWaitingReview,
  detailProgressVisible,
  freeChatSelected,
  hasActiveDetailWorkflow,
  initialDetailDesignSelectionRequired,
  isApplicationPlanningPhase
}: DevelopmentTargetSelectorState): boolean {
  if (isApplicationPlanningPhase) return false
  // 已有页面/API/Endpoint 设计时，重新进入开发只恢复工作台，不再强制弹出目标选择器。
  if (developmentEntrySelectionPending) return initialDetailDesignSelectionRequired
  return (
    developmentPlanningReady &&
    initialDetailDesignSelectionRequired &&
    !hasActiveDetailWorkflow &&
    !detailProgressVisible &&
    !detailConfirmationWaitingReview &&
    !freeChatSelected
  )
}

/** 以当前实体的数据源绑定状态判断是否需要锁定对话区。 */
export function requiresEntitySourceBinding(
  entity: DevelopmentPlanningEntityOption | undefined
): boolean {
  return Boolean(entity && !entity.designed && !entity.hasDetailPlan)
}

/** 以页面详细设计文档状态判断是否需要显示开始详细设计入口。 */
export function requiresPageDetailDesign(
  page: DevelopmentPlanningPageOption | undefined
): boolean {
  return Boolean(page && !page.designed && !page.hasDetailPlan)
}

/** 判断待设计页面是否应显示锁定蒙层；正式开发产物存在时不再重复引导。 */
export function shouldShowPageDetailDesignEntry(
  page: DevelopmentPlanningPageOption | undefined,
  developmentArtifactExists: boolean
): boolean {
  return requiresPageDetailDesign(page) && !developmentArtifactExists
}

/** 以 endpoint 文档状态判断接口是否需要显示开始详细设计入口。 */
export function requiresEndpointDetailDesign(
  endpoint: DevelopmentPlanningApiEndpoint | undefined
): boolean {
  return Boolean(endpoint && !endpoint.designed && !endpoint.hasDetailPlan)
}

/** 判断待设计接口是否应显示锁定卡片；空白新会话仍属于尚未开始设计。 */
export function shouldShowEndpointDetailDesignEntry(
  endpoint: DevelopmentPlanningApiEndpoint | undefined,
  endpointSessionActive: boolean,
  messageCount: number
): boolean {
  return requiresEndpointDetailDesign(endpoint) && (!endpointSessionActive || messageCount === 0)
}

/** 判断 Workflow 快照是否属于独立 EntitySourceBinding 场景。 */
export function isEntityDesignWorkflow(workflow: WorkflowRunPayload | undefined): boolean {
  if (!workflow) return false
  // 实体数据源绑定是独立工作流，不进入后续 Build。
  const phase = String(workflow.summary.phase || '').trim()
  if (phase && phase !== 'entity_source_binding') return false
  if (workflow.summary.clarification?.review?.summary?.entityDesign) return true
  if (workflow.summary.clarification?.review?.summary?.detailTargetType === 'entity') return true
  const state = workflow.state || {}
  const result = workflow.result || {}
  const entityId = String(
    state.selectedEntityId ||
      state.selected_entity_id ||
      result.selectedEntityId ||
      result.selected_entity_id ||
      ''
  ).trim()
  const detailTargetType = String(
    state.detailTargetType ||
      state.detail_target_type ||
      result.detailTargetType ||
      result.detail_target_type ||
      ''
  ).trim()
  if (entityId && (!detailTargetType || detailTargetType === 'entity')) return true
  // 实时运行快照可能只带节点增量，从事件 stateDelta 中恢复实体目标上下文。
  const events = Array.isArray(workflow.events) ? workflow.events : []
  for (const event of events) {
    const stateDelta = event.data?.stateDelta
    if (stateDelta && typeof stateDelta === 'object') {
      const delta = stateDelta as Record<string, unknown>
      const deltaEntityId = String(delta.selectedEntityId || delta.selected_entity_id || '').trim()
      const deltaTargetType = String(
        delta.detailTargetType || delta.detail_target_type || ''
      ).trim()
      if (deltaEntityId && (!deltaTargetType || deltaTargetType === 'entity')) return true
    }
    // 事件 detail 中可能直接携带 EntitySourceBinding 确认载荷，从中恢复实体归属。
    const detail = event.data?.detail
    if (detail && typeof detail === 'object') {
      const clarification = (detail as Record<string, unknown>).clarification
      if (clarification && typeof clarification === 'object') {
        const reviewSummary = (
          clarification as {
            review?: { summary?: Record<string, unknown> }
          }
        ).review?.summary
        if (reviewSummary?.entityDesign || reviewSummary?.selectedEntityId) return true
      }
    }
  }
  return false
}
