// AiChatPanel 的纯逻辑层：阶段会话选择、生命周期瞬时快照、产物归属解析、
// 门禁扫描与页面/接口身份解析。只做数据变换、不持有 React 状态，供主组件编排调用。
import type { BackgroundTask } from '../../backgroundTasks'
import type {
  ApplicationLifecycle,
  ApplicationMenuItem,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  WorkflowRunPayload
} from '../../typings'
import { composePreviewUrl } from '../../utils'
import type { ChatSessionSummary } from '../../service/chatSessions'
// 前端本地合成 lifecycle 快照的 revision 必须与剧本共享计数器，避免与下一帧撞号被拒合并
import { nextSyntheticLifecycleRevision } from '../../mock/scripts/revision'
import type { WorkbenchPhase } from '../../workbenchPhase'
import {
  endpointArtifactId,
  pageArtifactId,
  type WorkbenchArtifactStatus
} from '../../workbenchDomain'
import { appApiArtifactId } from '../AppApis/model'
import type { FormalArtifactKey } from '../../initializationPlanning'
import type { PageDesign } from '../../workbenchArtifacts'
import type { TestCaseExecutionSnapshot } from '../../testCasePreparation'
import { endpointDetailTargetKey, pageDetailTargetKey, pendingGateWorkflow } from './utils'
import type { AgentChatMessage } from './types'
import type { SessionRunStatus } from './hooks/sessionRuntime'
import type { RelatedEndpointContext } from './hooks/useChatSessions'
import { workflowClarification } from './components/WorkflowRunCard'

/** 从挡板消息生成目标键，用于判断同一产物是否已有待确认的模板选择卡。 */
export function detailBlockerTargetKey(blocker: AgentChatMessage['detailBlocker']): string {
  if (!blocker) return ''
  return blocker.type === 'endpoint'
    ? endpointDetailTargetKey(blocker.apiContractId, blocker.endpointId)
    : pageDetailTargetKey(blocker.pageId)
}

/** 开发阶段的产物发起引导话术：空对话首次进入与无目标发送共用的落库文本；渲染层由产物发起引导卡承载。 */
export const DEVELOPMENT_GUIDE_TEXT = '请选择“应用页面”或“应用API”开始开发，点选产物即可直接发起实施。'

/** 将后台实现任务状态映射为开发产物的扩展状态；未涉及产物返回 undefined。 */
export function backgroundTaskArtifactStatus(task: BackgroundTask): WorkbenchArtifactStatus | undefined {
  if (task.status === 'queued') return 'impl-queued'
  if (task.status === 'running') return 'implementing'
  if (task.status === 'failed' || task.status === 'cancelled') return 'failed'
  if (task.status === 'completed') {
    // 任务完成后若验收后续步骤尚未执行，产物保持「待验收」；执行后即为已完成。
    return task.nextStep && !task.nextStep.done ? 'awaiting-review' : 'completed'
  }
  return undefined
}

export type ProjectDocumentConfig = {
  content: string
  onSaveEdit?: (draft: string) => void
  readOnly: boolean
}

/** 返回当前阶段最近使用的应用级会话；页面和接口会话只归开发阶段管理。 */
export function latestStageSession(
  sessions: ChatSessionSummary[],
  phase: WorkbenchPhase
): ChatSessionSummary | undefined {
  return sessions
    .filter(
      (session) =>
        session.sessionKind === phase &&
        !session.pageId &&
        !session.apiContractId &&
        !session.endpointId
    )
    .sort((left, right) => right.updatedAt - left.updatedAt)[0]
}

/** 从生命周期扩展读取测试用例执行快照，测试阶段只以用例执行状态为准。 */
export function readTestExecutionSnapshot(
  extensions: Record<string, unknown>
): TestCaseExecutionSnapshot | undefined {
  const rawStatus = String(extensions.testExecutionStatus || '')
  if (!['idle', 'running', 'failed', 'passed'].includes(rawStatus)) return undefined
  const total = Number(extensions.testCasesTotal || 0)
  const completed = Number(extensions.testCasesCompleted || 0)
  if (!Number.isFinite(total) || total <= 0) return undefined
  const rawResults = extensions.testCaseResults
  const results =
    rawResults && typeof rawResults === 'object'
      ? (Object.fromEntries(
          Object.entries(rawResults as Record<string, unknown>).filter(([, value]) =>
            ['pending', 'running', 'passed', 'failed'].includes(String(value))
          )
        ) as TestCaseExecutionSnapshot['results'])
      : undefined
  const rawDefects = extensions.testCaseDefects
  const defects = (() => {
    if (!rawDefects || typeof rawDefects !== 'object') return undefined
    const normalized: NonNullable<TestCaseExecutionSnapshot['defects']> = {}
    Object.entries(rawDefects as Record<string, unknown>).forEach(([caseId, value]) => {
      if (!Array.isArray(value)) return
      const caseDefects = value.flatMap((item) => {
        if (!item || typeof item !== 'object') return []
        const defect = item as Record<string, unknown>
        const id = String(defect.id || '')
        if (!id) return []
        return [
          {
            id,
            severity: String(defect.severity) === '严重' ? ('严重' as const) : ('一般' as const),
            target: String(defect.target || '当前用例关联产物'),
            title: String(defect.title || '测试缺陷'),
            summary: String(defect.summary || ''),
            status: ['open', 'repairing', 'resolved'].includes(String(defect.status))
              ? (String(defect.status) as 'open' | 'repairing' | 'resolved')
              : ('open' as const)
          }
        ]
      })
      if (caseDefects.length > 0) normalized[caseId] = caseDefects
    })
    return Object.keys(normalized).length > 0 ? normalized : undefined
  })()
  return {
    activeCaseId: typeof extensions.activeCaseId === 'string' ? extensions.activeCaseId : undefined,
    completed: Math.max(0, Math.min(total, Number.isFinite(completed) ? completed : 0)),
    defects,
    results,
    status: rawStatus as TestCaseExecutionSnapshot['status'],
    total
  }
}

/** 为确认进入测试阶段创建瞬时应用级执行快照，让阶段位置先于测试 Agent 加载切换。 */
export function beginTestingExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string,
  testCaseTotal: number
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-testing-entry-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: nextSyntheticLifecycleRevision(lifecycle.revision),
    extensions: {
      ...lifecycle.extensions,
      testExecutionStatus: 'running',
      testCasesCompleted: 0,
      testCasesTotal: testCaseTotal
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'application_test',
        status: 'running',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 为确认进入审查阶段创建瞬时应用级执行快照，让审查 Agent 在确认后再启动。 */
export function beginReviewExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-review-entry-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: nextSyntheticLifecycleRevision(lifecycle.revision),
    extensions: {
      ...lifecycle.extensions,
      reviewEntryConfirmed: true
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'code_review',
        status: 'running',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 将已确认的审查报告收口为审查通过态，随后由阶段推导自动进入验收。 */
export function completeReviewExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-review-complete-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: nextSyntheticLifecycleRevision(lifecycle.revision),
    extensions: {
      ...lifecycle.extensions,
      reviewEntryConfirmed: true,
      reviewStatus: 'passed'
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'code_review',
        status: 'completed',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 记录用户对当前预览交付的明确验收确认，不改变验收对话归属。 */
export function completeAcceptanceExecution(
  lifecycle: ApplicationLifecycle,
  applicationId: string
): ApplicationLifecycle {
  const now = new Date().toISOString()
  const runId = `mock-acceptance-complete-${Date.now()}`
  return {
    ...lifecycle,
    updatedAt: now,
    revision: nextSyntheticLifecycleRevision(lifecycle.revision),
    extensions: {
      ...lifecycle.extensions,
      acceptanceStatus: 'passed',
      acceptanceBasedOnRevision: lifecycle.revision
    },
    activeExecutions: {
      ...lifecycle.activeExecutions,
      [runId]: {
        scope: 'application',
        targetId: applicationId,
        threadId: runId,
        runId,
        phase: 'acceptance',
        status: 'completed',
        startedAt: now,
        updatedAt: now
      }
    }
  }
}

/** 从开发 Workflow 快照解析本轮真正交付的产物，避免按当前目录选中项误推进。 */
export function developmentWorkflowArtifactId(workflow?: WorkflowRunPayload): string {
  if (!workflow) return ''
  const state = (workflow.state || {}) as Record<string, unknown>
  const result = (workflow.result || {}) as Record<string, unknown>
  const detailTargetType = String(state.detailTargetType || result.detailTargetType || '').trim()
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
  const pageId = String(
    state.selectedPageId ||
      state.selected_page_id ||
      result.selectedPageId ||
      result.selected_page_id ||
      ''
  ).trim()
  const objectId = String(
    state.selectedObjectId || result.selectedObjectId || ''
  ).trim()
  // 应用页面工作流即使带有依赖接口，也只推进应用页面产物；接口工作流才推进 endpoint。
  if (objectId && detailTargetType === 'app-api') return appApiArtifactId(objectId)
  if (pageId && detailTargetType !== 'endpoint') return pageArtifactId(pageId)
  if (apiContractId && endpointId) return endpointArtifactId(apiContractId, endpointId)
  return pageId ? pageArtifactId(pageId) : ''
}

/** 为应用预览地址附加当前版本标识，让独立预览服务渲染对应版本快照。 */
export function composeVersionPreviewUrl(baseUrl: string, path: string, versionKey: string): string {
  const previewUrl = composePreviewUrl(baseUrl, path)
  if (!previewUrl || !versionKey) return previewUrl
  const parsedUrl = new URL(previewUrl)
  parsedUrl.searchParams.set('version', versionKey)
  return parsedUrl.toString()
}

/** 根据生命周期阶段选择右侧结构化审阅产物；生成中的下游产物不打断当前审阅。 */
export function resolvePlanningArtifactKey(
  stage: string | undefined,
  viewingPhase: WorkbenchPhase
): FormalArtifactKey {
  if (viewingPhase === 'planning') return 'technical-plan'
  // UI 设计稿只在生成完成、等待逐页确认（含规划准入门尚可回看设计稿）时接管右侧；
  // 生成期间保持需求规格说明书，避免用户还没读完就被切走。
  if (stage === 'awaiting_ui_design_confirmation' || stage === 'awaiting_planning_stage_entry') {
    return 'ui-designs'
  }
  return 'requirement-spec'
}

/** 从原型的单文件新增 Diff 还原待保存文本；非新增行保留现有文件内容作为安全兜底。 */
export function contentFromFileDiff(diff: string, currentContent = ''): string {
  const addedLines = diff
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
  return addedLines.length > 0 ? addedLines.join('\n') : currentContent
}

/** 设计与规划文档改由结构化表单确认，禁止历史 Diff 载荷重新打开文件改动卡。 */
export function isPendingWorkspaceCodeChange(workflow: WorkflowRunPayload | undefined): boolean {
  const phase = String(workflow?.summary?.phase || '')
  return !['requirements', 'requirement_document', 'technical_planning'].includes(phase)
}

/** 从当前消息历史里读取最后一个 Workflow，弥补 activeWorkflow 在运行结束瞬间的状态空窗。 */
export function latestMessageWorkflow(
  messages: Array<{ workflow?: WorkflowRunPayload }>
): WorkflowRunPayload | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    if (messages[index].workflow) return messages[index].workflow
  }
  return undefined
}

/**
 * 跨会话扫描指定阶段会话的待处理门禁。工作流走完后允许新建任务，查看对象随时可能
 * 切到别的会话；门禁是阶段层状态，不能因为当前会话没有轨迹就让顶部入口失联。
 */
export function findPendingGateWorkflow(
  sessions: Array<{ id: string; sessionKind?: string }>,
  sessionKind: string,
  options: {
    mode: string
    readMessages: (key: string) => Array<{ workflow?: WorkflowRunPayload }>
    runtimeKey: (sessionId: string) => string
  }
): WorkflowRunPayload | undefined {
  for (const session of sessions) {
    if (session.sessionKind !== sessionKind) continue
    const gate = pendingGateWorkflow(
      latestMessageWorkflow(options.readMessages(options.runtimeKey(session.id))),
      options.mode
    )
    if (gate) return gate
  }
  return undefined
}

/** 判断当前推进任务是否真有未结束事项；已提交的历史确认卡不再占用新建门禁。 */
export function sessionRunBlocksConversationCreation(
  status: SessionRunStatus | undefined,
  workflow: WorkflowRunPayload | undefined
): boolean {
  if (status === 'running' || status === 'stopping') return true
  if (status !== 'awaiting_user') return false
  const clarification = workflowClarification(workflow)
  if (workflow?.summary?.status !== 'requires_user_input') return false
  // 阶段准入门（规划/开发）只等待用户切换阶段，任务自身的工作项已全部完成，
  // 不能让它把本阶段的“新建任务”入口永久锁住。
  if (
    clarification?.mode === 'planning_stage_entry' ||
    clarification?.mode === 'development_entry_confirmation'
  ) {
    return false
  }
  // 有确认载荷时，只认仍明确要求输入的卡片；submitted/completed 等历史状态全部释放门禁。
  return clarification ? clarification.status === 'requires_user_input' : true
}

/** 按页面名称递归查找对应的菜单配置。 */
export function findPageMenuItem(
  items: ApplicationMenuItem[],
  label: string
): ApplicationMenuItem | undefined {
  for (const item of items) {
    if (item.label === label) return item
    const matchedChild = findPageMenuItem(item.children || [], label)
    if (matchedChild) return matchedChild
  }
  return undefined
}

/** 在最新 ProjectPlan 页面目录中解析会话保存的页面标识，避免旧 pageId 覆盖当前选择。 */
export function resolvePlanningPageId(pages: DevelopmentPlanningPageOption[], pageId: string): string {
  const normalizedPageId = pageId.trim()
  if (!normalizedPageId) return ''
  const matched = pages.find((page) => page.pageId === normalizedPageId)
  if (matched) return matched.pageId
  const alias = pageIdAlias(normalizedPageId)
  return pages.find((page) => pageIdAlias(page.pageId) === alias)?.pageId || ''
}

/** 生成页面标识的宽松别名，兼容历史会话里的 page- 前缀差异。 */
export function pageIdAlias(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/^page-/, '')
}

/** 从页面设计的接口依赖与响应绑定中解析同一实现任务负责的 endpoint。 */
export function resolvePageRelatedEndpoint(
  pageId: string | undefined,
  pageDesigns: Record<string, PageDesign | undefined>,
  apiContracts: DevelopmentPlanningApiContract[]
): RelatedEndpointContext | undefined {
  if (!pageId) return undefined
  const design = pageDesigns[pageId]
  const dependency = design?.api_dependencies?.[0]
  if (!dependency) return undefined
  const contract = apiContracts.find((item) => item.id === dependency.apiContractId)
  if (!contract) return undefined
  const boundEndpointId = design?.response_bindings?.find(
    (binding) => binding.endpointId
  )?.endpointId
  const endpointIndex = contract.endpoints.findIndex(
    (item) =>
      (boundEndpointId && item.id === boundEndpointId) ||
      (item.method === dependency.method && item.path === dependency.path)
  )
  if (endpointIndex < 0) return undefined
  const endpoint = contract.endpoints[endpointIndex]
  return {
    apiContractId: endpoint.apiContractId || contract.id,
    endpointId: endpoint.id || String(endpointIndex + 1),
    endpointLabel: `${endpoint.method} ${endpoint.path}`
  }
}
