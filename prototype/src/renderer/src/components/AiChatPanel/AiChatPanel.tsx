import { Alert, Modal } from 'antd'
import { ProjectOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useWorkbench, useWorkbenchPhase } from '../../context'
import PhaseGateModal from '../PhaseGateModal'
import {
  getBackgroundTasks,
  type BackgroundTask,
  type BackgroundTaskSystem
} from '../../backgroundTasks'
import { useBackgroundTasks } from '../../hooks/useBackgroundTasks'
import type {
  ApplicationConfig,
  ApplicationLifecycle,
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  DevelopmentPlanningPageTreeNode,
  ApplicationMenuItem,
  DevelopmentPlanningEntity,
  EditorMode,
  WorkflowDebugOptions,
  WorkflowRunPayload
} from '../../typings'
import { composePreviewUrl, cx, previewOrigin } from '../../utils'
import type { ChatSessionSummary } from '../../service/chatSessions'
import BrowserPreviewPanel from '../BrowserPreviewPanel/BrowserPreviewPanel'
import ChatComposer from './components/ChatComposer'
import CodeChangeCard from './components/CodeChangeCard'
import MessageList from './components/MessageList'
import type { AgentChatMessage, WorkspaceDocKey } from './types'
import SourcePanel from './components/SourcePanel'
import DevelopmentArtifactsPanel, {
  type DevelopmentArtifactItem
} from './components/DevelopmentArtifactsPanel'
import TestCasesPanel from './components/TestCasesPanel'
import TestCaseTaskTypeModal from './components/TestCaseTaskTypeModal'
import {
  buildEndpointSource,
  buildPageSource,
  buildProjectPlanDoc,
  buildRequirementSpecDoc,
  type PageDesign
} from '../../workbenchArtifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import {
  APPLICATION_ROOT,
  WORKSPACE_DOC_PATHS,
  appPath,
  workspaceScaffoldDirectories,
  workspaceScaffoldFiles
} from '../../mock/workspaceFiles'
// 前端本地合成 lifecycle 快照的 revision 必须与剧本共享计数器，避免与下一帧撞号被拒合并
import { nextSyntheticLifecycleRevision } from '../../mock/scripts/revision'
import {
  compareWorkbenchPhases,
  isInitialPlanningPhase,
  WORKBENCH_PHASE_AGENTS,
  type WorkbenchPhase
} from '../../workbenchPhase'
import {
  documentArtifactId,
  entityArtifactId,
  endpointArtifactId,
  pageArtifactId,
  resolveArtifactAccess,
  type WorkbenchArtifact,
  type WorkbenchArtifactAccess,
  type WorkbenchArtifactProgress,
  type WorkbenchArtifactStatus
} from '../../workbenchDomain'
import { type WorkspaceTab, type WorkspaceTabKey } from './components/RightPanelTabs'
import WorkbenchRightPanel from './components/WorkbenchRightPanel'
import type { RightPanelLayout } from './types'
import PhaseNavigation from './components/PhaseNavigation'
import {
  DevelopmentStageCompleteModal,
  TestingStageCompleteModal
} from './components/StageCompleteModal'
import PageContextHeader from './components/PageContextHeader'
import {
  workflowClarification,
  type ClarificationAnswers
} from './components/WorkflowRunCard'
import type { ProcessStepRecord } from '../../service/agUiAgent'
import type { ComposerArtifactTarget } from './artifactMention'
import type { ConversationManagementContent } from './components/AuxiliaryDrawer'
import AgentFilesPage from '../AgentFilesPage/AgentFilesPage'
import SettingsPage from '../SettingsPage/SettingsPage'
import SkillsPage from '../SkillsPage/SkillsPage'
import { useAssistantPreviewLayout } from './hooks/useAssistantPreviewLayout'
import { useChatSessions } from './hooks/useChatSessions'
import type { RelatedEndpointContext } from './hooks/useChatSessions'
import { useCodeChangeRevert } from './hooks/useCodeChangeRevert'
import { useWorkflowConversation } from './hooks/useWorkflowConversation'
import { useCountedRequestTrigger } from './hooks/useCountedRequestTrigger'
import {
  sessionRuntimeKey,
  type SessionIdentity,
  type SessionRunStatus
} from './hooks/sessionRuntime'
import { chatCopy } from './constants'
import {
  endpointDetailTargetKey,
  pageDetailTargetKey,
  pendingGateWorkflow,
  workflowCodeChanges,
  workflowDetailTargetKey,
  workflowReviewTarget,
  type WorkflowPreviewTarget
} from './utils'
import './AiChatPanel.less'
import type {
  TestCaseExecutionSnapshot,
  TestCaseGenerationTaskType,
  TestCasePreparationSnapshot
} from '../../testCasePreparation'
import { TEST_CASE_ESTIMATE_GROUPS } from '../../testCasePreparation'

type Props = {
  application: ApplicationConfig
  applicationLifecycle?: ApplicationLifecycle
  developmentPlanningReady: boolean
  developmentPlanningPages: DevelopmentPlanningPageOption[]
  developmentPlanningPageTree: DevelopmentPlanningPageTreeNode[]
  developmentPlanningApiContracts: DevelopmentPlanningApiContract[]
  developmentPlanningEntities: DevelopmentPlanningEntity[]
  editorMode: EditorMode
  onApplicationUpdate: (application: ApplicationConfig) => void
  onApplicationLifecycleChange: (lifecycle: ApplicationLifecycle) => void
  onPlanningArtifactsRefresh: () => void
  previewBaseUrl: string
  previewLaunchError: string
  versionReadOnly: boolean
  versionPreviewOnly: boolean
  versionViewKey: string
  /** 顶部阶段条请求打开“进入测试”确认弹框的自增信号。 */
  testingEntryRequest?: number
  /** 向顶部阶段条上报测试阶段是否具备进入条件（“允许进入”不等于“已进入”）。 */
  onTestingEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求重新唤起“进入开发”准入门弹框的自增信号。 */
  developmentEntryRequest?: number
  /** 向顶部阶段条上报开发准入门是否待处理（计划确认后的弹框未完成选择）。 */
  onDevelopmentEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求重新唤起“进入项目规划”准入门弹框的自增信号。 */
  planningEntryRequest?: number
  /** 向顶部阶段条上报项目规划准入门是否待处理（需求文档确认后的弹框未完成确认）。 */
  onPlanningEntryAvailableChange?: (available: boolean) => void
  /** 顶部阶段条请求打开“进入审查”确认弹框的自增信号。 */
  reviewEntryRequest?: number
  /** 向顶部阶段条上报测试通过后是否具备进入审查条件。 */
  onReviewEntryAvailableChange?: (available: boolean) => void
  /** 向顶部阶段条同步当前开发产物完成进度。 */
  onDevelopmentArtifactProgressChange?: (progress: WorkbenchArtifactProgress) => void
  testCasePreparation: TestCasePreparationSnapshot
  testPreparationOpenRequest: number
  onRetryTestCases: () => void
  /** 后台任务队列抽屉是否展开（状态由工作台页持有，抽屉挂载在主体容器）。 */
  backgroundTasksDrawer?: BackgroundTaskSystem | null
  /** 左侧菜单切换后台任务队列抽屉。 */
  onOpenBackgroundTasks?: (system: BackgroundTaskSystem) => void
  /** 点击待验收任务的「验收」入口后请求启动该产物的验收工作流（自增请求，由工作台页转发）。 */
  backgroundTaskAcceptRequest?: { nonce: number; taskId: string }
  /** 输入区请求继续后台工作流；与后台任务抽屉共用同一任务锁与启动路径。 */
  onRequestBackgroundTaskContinuation?: (taskId: string) => void
  /** 验收工作流结束（无论成败）后回调；工作台页据此解除其它入口的禁用态。 */
  onBackgroundTaskAcceptanceSettled?: (taskId: string) => void
  /** 左侧菜单打开任务管理抽屉（工作台页统一处理互斥）。 */
  onOpenConversationManagement?: () => void
  /** 任务管理抽屉是否展开（工作台页持有，用于菜单激活态）。 */
  conversationDrawerOpen?: boolean
  /** 聊天面板向工作台页注册任务管理内容查询函数。 */
  onConversationManagementReady?: (query: () => ConversationManagementContent) => void
  /** 关闭辅助抽屉（工作台页统一处理互斥）。 */
  onCloseAuxiliaryDrawer?: () => void
  /** 项目计划确认时同步所选的测试用例生成任务类型。 */
  onTestCaseGenerationTaskTypeChange?: (taskType: TestCaseGenerationTaskType) => void
}
type ActiveView = 'chat' | 'skills' | 'files' | 'settings'

type ActiveApiEndpointTarget = {
  apiContractId: string
  endpointId: string
  endpointKey: string
  label: string
}
type ActiveDetailTarget =
  | { type: 'none' }
  | { type: 'page'; pageId: string }
  | ({ type: 'endpoint' } & ActiveApiEndpointTarget)

type DevelopmentAutoTarget = {
  artifactId: string
  kind: 'page'
  page: DevelopmentPlanningPageOption
}

/** @ 产物发起的模板选择卡目标：页面或独立接口，作为本轮开发 Workflow 的起点。 */
type DevelopmentTemplateTarget =
  | { kind: 'page'; artifactId: string; page: DevelopmentPlanningPageOption }
  | {
      kind: 'endpoint'
      artifactId: string
      apiContractId: string
      endpointId: string
      label: string
      path: string
      summary: string
    }

/** 从挡板消息生成目标键，用于判断同一产物是否已有待确认的模板选择卡。 */
function detailBlockerTargetKey(blocker: AgentChatMessage['detailBlocker']): string {
  if (!blocker) return ''
  return blocker.type === 'endpoint'
    ? endpointDetailTargetKey(blocker.apiContractId, blocker.endpointId)
    : pageDetailTargetKey(blocker.pageId)
}

/** 开发阶段的产物发起引导话术：空对话首次进入与无目标发送共用；快速入口内嵌在正文里。 */
const DEVELOPMENT_GUIDE_TEXT = '请选择从哪个产物开始开发。'

/** 将后台实现任务状态映射为开发产物的扩展状态；未涉及产物返回 undefined。 */
function backgroundTaskArtifactStatus(task: BackgroundTask): WorkbenchArtifactStatus | undefined {
  if (task.status === 'queued') return 'impl-queued'
  if (task.status === 'running') return 'implementing'
  if (task.status === 'failed' || task.status === 'cancelled') return 'failed'
  if (task.status === 'completed') {
    // 任务完成后若验收后续步骤尚未执行，产物保持「待验收」；执行后即为已完成。
    return task.nextStep && !task.nextStep.done ? 'awaiting-review' : 'completed'
  }
  return undefined
}

type ProjectDocumentConfig = {
  content: string
  onSaveEdit?: (draft: string) => void
  readOnly: boolean
}

/** 返回当前阶段最近使用的应用级会话；页面和接口会话只归开发阶段管理。 */
function latestStageSession(
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
function readTestExecutionSnapshot(
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
function beginTestingExecution(
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
function beginReviewExecution(
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
function completeReviewExecution(
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
function completeAcceptanceExecution(
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
function developmentWorkflowArtifactId(workflow?: WorkflowRunPayload): string {
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
  // 页面工作流即使带有依赖接口，也只推进页面产物；接口工作流才推进 endpoint。
  if (pageId && detailTargetType !== 'endpoint') return pageArtifactId(pageId)
  if (apiContractId && endpointId) return endpointArtifactId(apiContractId, endpointId)
  return pageId ? pageArtifactId(pageId) : ''
}

/** 为应用预览地址附加当前版本标识，让独立预览服务渲染对应版本快照。 */
function composeVersionPreviewUrl(baseUrl: string, path: string, versionKey: string): string {
  const previewUrl = composePreviewUrl(baseUrl, path)
  if (!previewUrl || !versionKey) return previewUrl
  const parsedUrl = new URL(previewUrl)
  parsedUrl.searchParams.set('version', versionKey)
  return parsedUrl.toString()
}

// 需求分析/项目规划阶段产物文档的可用进度（按生命周期 initialization.stage 判定）。
// 顺序与 planning.ts / workbenchPhase 的规划期 stage 推进一致；到达门槛 stage 后对应文档才生成。
const DESIGN_DOC_STAGE_ORDER = [
  'collecting_requirement',
  'analyzing_requirement',
  'awaiting_requirement_clarification',
  'generating_requirement_spec',
  'awaiting_requirement_confirmation',
  'generating_project_plan',
  'awaiting_project_plan_confirmation',
  'generating_application_template_files',
  'ready_for_workbench'
]
function designStageReached(stage: string | undefined, threshold: string): boolean {
  if (!stage) return false
  const current = DESIGN_DOC_STAGE_ORDER.indexOf(stage)
  const target = DESIGN_DOC_STAGE_ORDER.indexOf(threshold)
  return current >= 0 && target >= 0 && current >= target
}

/** 项目计划只有在用户确认且保存完成后，才算正式解锁开发产物目录。 */
function isDevelopmentCatalogConfirmed(stage: string | undefined): boolean {
  return stage === 'ready_for_workbench'
}

/** 需求分析/项目规划阶段两份正式产物文档的 key → 可用的门槛 stage。 */
const DESIGN_DOC_THRESHOLDS: Record<WorkspaceDocKey, string> = {
  'requirement-spec': 'generating_requirement_spec',
  'project-plan': 'generating_project_plan'
}

/** 当前 stage 对应"最新就绪"的文档 key：生成完成(stage 推进)自动跟随到对应文档。 */
function designActiveDocKey(stage: string | undefined): WorkspaceDocKey | undefined {
  if (designStageReached(stage, 'generating_project_plan')) return 'project-plan'
  if (designStageReached(stage, 'generating_requirement_spec')) return 'requirement-spec'
  return undefined
}

/** 从原型的单文件新增 Diff 还原待保存文本；非新增行保留现有文件内容作为安全兜底。 */
function contentFromFileDiff(diff: string, currentContent = ''): string {
  const addedLines = diff
    .split('\n')
    .filter((line) => line.startsWith('+') && !line.startsWith('+++'))
    .map((line) => line.slice(1))
  return addedLines.length > 0 ? addedLines.join('\n') : currentContent
}

/**
 * 需求分析/项目规划阶段右栏文档 tab 跟随规则：回看时优先按当前查看阶段定位，
 * 正常推进时生成中按 workflow.phase、就绪后按 lifecycle.stage 接管切到内容。
 */
function resolveDesignDocKey(
  stage: string | undefined,
  phase: string | undefined,
  phaseRunning: boolean,
  viewingPhase?: WorkbenchPhase
): WorkspaceDocKey | undefined {
  // 回看需求分析/项目规划阶段时，左侧产物必须跟随当前查看阶段，不能被生命周期已到达的最新文档覆盖。
  if (viewingPhase === 'analysis') return 'requirement-spec'
  if (viewingPhase === 'planning') return 'project-plan'
  const generatingKey =
    phase === 'requirements' && phaseRunning
      ? 'requirement-spec'
      : phase === 'project_planning' && phaseRunning
        ? 'project-plan'
        : undefined
  return generatingKey ?? designActiveDocKey(stage)
}

/** 判断需求分析/项目规划阶段的文件改动是否仍属于当前阶段的待授权生成，过滤已确认后残留的历史快照。 */
function isPendingDesignCodeChange(
  workflow: WorkflowRunPayload | undefined,
  stage: string | undefined
): boolean {
  const phase = String(workflow?.summary?.phase || '')
  const running = workflow?.summary?.status === 'running'
  if (phase === 'requirements') {
    // 需求生成期间即使生命周期仍停留在需求分析阶段，也要持续展示每一帧 Diff。
    return (
      running ||
      stage === 'generating_requirement_spec' ||
      stage === 'awaiting_requirement_confirmation'
    )
  }
  if (phase === 'project_planning') {
    // 项目 Agent 的首个 running 快照到来时，生命周期通常还没切到项目规划阶段；
    // 不能因此过滤掉渐进写入的中间快照，否则用户只能看到最后一帧。
    return (
      running ||
      stage === 'generating_project_plan' ||
      stage === 'awaiting_project_plan_confirmation'
    )
  }
  // 页面/接口构建与测试/审查报告沿用各自工作流的当前 Diff，不按查看阶段过滤。
  return true
}

/** 从当前消息历史里读取最后一个 Workflow，弥补 activeWorkflow 在运行结束瞬间的状态空窗。 */
function latestMessageWorkflow(
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
function findPendingGateWorkflow(
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
function sessionRunBlocksConversationCreation(
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
function findPageMenuItem(
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
function resolvePlanningPageId(pages: DevelopmentPlanningPageOption[], pageId: string): string {
  const normalizedPageId = pageId.trim()
  if (!normalizedPageId) return ''
  const matched = pages.find((page) => page.pageId === normalizedPageId)
  if (matched) return matched.pageId
  const alias = pageIdAlias(normalizedPageId)
  return pages.find((page) => pageIdAlias(page.pageId) === alias)?.pageId || ''
}

/** 生成页面标识的宽松别名，兼容历史会话里的 page- 前缀差异。 */
function pageIdAlias(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/_/g, '-')
    .replace(/^page-/, '')
}

/** 从页面设计的接口依赖与响应绑定中解析同一实现任务负责的 endpoint。 */
function resolvePageRelatedEndpoint(
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

/** 组织应用侧栏、对话区、页面信息与预览面板的主工作台。 */
export default function AiChatPanel({
  application,
  applicationLifecycle,
  developmentPlanningReady,
  developmentPlanningPages,
  developmentPlanningPageTree,
  developmentPlanningApiContracts,
  developmentPlanningEntities,
  editorMode,
  onApplicationUpdate,
  onApplicationLifecycleChange,
  onPlanningArtifactsRefresh,
  previewBaseUrl,
  previewLaunchError,
  versionReadOnly,
  versionPreviewOnly,
  versionViewKey,
  testingEntryRequest,
  onTestingEntryAvailableChange,
  developmentEntryRequest,
  onDevelopmentEntryAvailableChange,
  planningEntryRequest,
  onPlanningEntryAvailableChange,
  onReviewEntryAvailableChange,
  onDevelopmentArtifactProgressChange,
  reviewEntryRequest,
  testCasePreparation,
  testPreparationOpenRequest,
  onRetryTestCases,
  backgroundTasksDrawer,
  onOpenBackgroundTasks,
  backgroundTaskAcceptRequest,
  onRequestBackgroundTaskContinuation,
  onBackgroundTaskAcceptanceSettled,
  onOpenConversationManagement,
  conversationDrawerOpen,
  onConversationManagementReady,
  onCloseAuxiliaryDrawer,
  onTestCaseGenerationTaskTypeChange
}: Props): ReactElement {
  const [activeView, setActiveView] = useState<ActiveView>('chat')
  // 需求分析/项目规划阶段文档编辑态:editedDesignDocs 存快捷键保存后的编辑版(覆盖静态产物显示);
  // 编辑草稿由 DocPanel 内部管理(默认即编辑,IDE 式),Ctrl/Cmd+S 后经 onSaveEdit(draft) 回传。
  const [editedDesignDocs, setEditedDesignDocs] = useState<
    Partial<Record<WorkspaceDocKey, string>>
  >({})
  const [activeDetailTarget, setActiveDetailTarget] = useState<ActiveDetailTarget>({ type: 'none' })
  const [developmentCompleteModalOpen, setDevelopmentCompleteModalOpen] = useState(false)
  const [testingTransitionRequested, setTestingTransitionRequested] = useState(false)
  const [reviewCompleteModalOpen, setReviewCompleteModalOpen] = useState(false)
  const [reviewTransitionRequested, setReviewTransitionRequested] = useState(false)
  const [testCaseTaskTypeModalOpen, setTestCaseTaskTypeModalOpen] = useState(false)
  // 项目规划准入门（planning_stage_entry）：弹框开关与“暂不进入”的 dismiss 标记。
  // 门禁本体是工作流的待输入节点，是否可进入由 planningEntryWorkflow 推导。
  const [planningEntryModalOpen, setPlanningEntryModalOpen] = useState(false)
  const [dismissedPlanningEntryRunId, setDismissedPlanningEntryRunId] = useState('')
  // 产物选择弹层的远程打开信号：引导消息的快速按钮与输入区工作流按钮共用同一弹层。
  const [artifactPickerRequest, setArtifactPickerRequest] = useState(0)
  const [dismissedDevelopmentEntryRunId, setDismissedDevelopmentEntryRunId] = useState('')
  const [projectPlanDiffAccepting, setProjectPlanDiffAccepting] = useState(false)

  // 开发门禁状态按版本保留：阶段准入必须消费已确认的产物状态，而不是瞬时设计标记。
  const [developmentStatusState, setDevelopmentStatusState] = useState<{
    statuses: Record<string, WorkbenchArtifactStatus>
    versionKey: string
  }>(() => ({ statuses: {}, versionKey: versionViewKey }))
  // 同一版本内已确认交付的产物只增不减，避免下一条 Workflow 的摘要刷新把完成数回退。
  const completedDevelopmentArtifactIdsRef = useRef(new Map<string, Set<string>>())
  const developmentArtifactProgressRef = useRef<{
    progress: WorkbenchArtifactProgress
    versionKey: string
  }>({ progress: { completed: 0, total: 0 }, versionKey: versionViewKey })
  const activeDetailTargetRef = useRef<ActiveDetailTarget>(activeDetailTarget)
  const [, setInteractingDetailTargetKey] = useState('')
  const [, setGeneratingDetailTargetKey] = useState('')
  const [previewError, setPreviewError] = useState('')
  const [elementInspectionActive, setElementInspectionActive] = useState(false)
  const [runtimePreviewBaseUrl, setRuntimePreviewBaseUrl] = useState(() =>
    previewOrigin(previewBaseUrl)
  )
  const [runtimePreviewLaunchError, setRuntimePreviewLaunchError] = useState(previewLaunchError)
  const handledPreviewTargetRef = useRef('')
  const { publishAiMessage } = useWorkbench()
  // 阶段门禁：查看阶段决定当前 Agent 与编辑权限，执行阶段和已到达阶段独立保留。
  const { reachedPhase, switchPhase, viewingPhase: activeWorkbenchPhase } = useWorkbenchPhase()
  const acceptanceAccepted =
    String(applicationLifecycle?.extensions?.acceptanceStatus || '') === 'passed'
  // 统一后台任务流水：开发产物状态、待验收卡与「后台任务」入口提示都从这一份状态推导。
  const allBackgroundTasks = useBackgroundTasks()
  const versionBackgroundTasks = useMemo(
    () =>
      allBackgroundTasks.filter(
        (task) =>
          task.applicationId === application.id &&
          task.versionId === (application.currentVersionId || 'current')
      ),
    [allBackgroundTasks, application.currentVersionId, application.id]
  )
  const [viewingTaskPhase, setViewingTaskPhase] = useState<WorkbenchPhase>(activeWorkbenchPhase)
  useEffect(() => {
    // 当前执行进入设计、测试、审查或验收阶段时立即同步查看位置，避免沿用上一阶段内容。
    if (
      activeWorkbenchPhase === 'analysis' ||
      activeWorkbenchPhase === 'planning' ||
      activeWorkbenchPhase === 'testing' ||
      activeWorkbenchPhase === 'review' ||
      activeWorkbenchPhase === 'acceptance'
    ) {
      setViewingTaskPhase(activeWorkbenchPhase)
    }
  }, [activeWorkbenchPhase])
  // 异步页面会话完成时读取最新目标，避免旧页面挡板覆盖刚打开的应用预览。
  useEffect(() => {
    activeDetailTargetRef.current = activeDetailTarget
  }, [activeDetailTarget])
  // 按当前应用工作区取演示数据（三应用各自独立）。
  const scenario = appDataByWorkspace(application.workspaceRoot)
  const pageDesignCatalog = useMemo(
    () => ({
      ...(scenario.designedPageDesigns as Record<string, PageDesign | undefined>),
      ...(scenario.pageDesigns as Record<string, PageDesign | undefined>)
    }),
    [scenario.designedPageDesigns, scenario.pageDesigns]
  )
  // 需求分析和项目规划阶段共用现有规划工作区；后续任务再拆分两套对话流程。
  const isDesignPhase = activeWorkbenchPhase === 'analysis' || activeWorkbenchPhase === 'planning'
  // 需求分析/项目规划阶段由当前工作台阶段直接决定 Agent 身份；会话切换完成前不沿用上一阶段的消息和加载态。
  const renderedTaskPhase = isDesignPhase ? activeWorkbenchPhase : viewingTaskPhase
  const displayIsDesignPhase = renderedTaskPhase === 'analysis' || renderedTaskPhase === 'planning'
  const displayIsDevelopmentPhase = renderedTaskPhase === 'development'
  const displayIsTestingPhase = renderedTaskPhase === 'testing'
  const displayIsReviewPhase = renderedTaskPhase === 'review'
  const displayIsAcceptancePhase = renderedTaskPhase === 'acceptance'
  const viewingHistoricalStage = renderedTaskPhase !== activeWorkbenchPhase
  // 所有模块(页面+接口)开发完成后仅提示用户确认，由用户决定是否进入测试阶段。
  // 准入必须读取当前工作台动态产物清单，不能读取静态演示剧本，否则未开始页面会被误判为已完成。
  const planningPages = developmentPlanningPages
  const planningContracts = developmentPlanningApiContracts
  const testExecutionExtensions = (applicationLifecycle?.extensions || {}) as Record<
    string,
    unknown
  >
  const testExecutionSnapshot = readTestExecutionSnapshot(testExecutionExtensions)
  const testExecutionPassedForEntry = testExecutionSnapshot?.status === 'passed'
  const reviewCompletionPromptRef = useRef('')
  const allDevelopmentModulesComplete =
    planningPages.length > 0 &&
    planningPages.every((page) => {
      const pageId = String(page.pageId || '')
      return (
        developmentStatusState.versionKey === versionViewKey &&
        developmentStatusState.statuses[pageArtifactId(pageId)] === 'completed'
      )
    }) &&
    planningContracts.every((contract) =>
      contract.endpoints.every(
        (endpoint) =>
          developmentStatusState.versionKey === versionViewKey &&
          developmentStatusState.statuses[
            endpointArtifactId(contract.id, String(endpoint.id || ''))
          ] === 'completed'
      )
    )
  const {
    assistantPanelWidth,
    handlePanelSplitKeyDown,
    handlePanelSplitDragStart,
    panelRef,
    panelStyle,
    rightPanel,
    rightPanelLayout,
    setRightPanelLayout,
    setRightPanel,
    splitDragging
  } = useAssistantPreviewLayout()
  useEffect(() => {
    if (testPreparationOpenRequest > 0) {
      setActiveView('chat')
      // 测试阶段的用例明细属于右侧工作台，不再打开与主内容重叠的辅助抽屉。
      onCloseAuxiliaryDrawer?.()
      setRightPanel({ type: 'test-cases' })
      setRightPanelLayout('split')
    }
  }, [onCloseAuxiliaryDrawer, setRightPanel, setRightPanelLayout, testPreparationOpenRequest])
  const activePageId = activeDetailTarget.type === 'page' ? activeDetailTarget.pageId : ''
  const activeApiEndpoint = activeDetailTarget.type === 'endpoint' ? activeDetailTarget : undefined
  const activePageOption = useMemo(
    () => developmentPlanningPages.find((page) => page.pageId === activePageId),
    [activePageId, developmentPlanningPages]
  )
  /** 接收实时 launch 结果并复用手动预览入口打开右侧面板。 */
  const handlePreviewReady = useCallback(
    (target: WorkflowPreviewTarget) => {
      if (handledPreviewTargetRef.current === target.key) return
      handledPreviewTargetRef.current = target.key
      const nextBaseUrl = previewOrigin(target.url)
      const nextPreviewUrl = composeVersionPreviewUrl(nextBaseUrl, '/', versionViewKey)
      if (!nextPreviewUrl) return
      setPreviewError('')
      setRuntimePreviewBaseUrl(nextBaseUrl)
      setRuntimePreviewLaunchError('')
      // 预览目标是当前实际展示的权威来源；切换页面预览时同步目录选中项，
      // 避免主会话仍保留旧页面身份而让目录高亮停留在上一项。
      if (activeWorkbenchPhase === 'development') {
        let previewPath = ''
        try {
          previewPath = new URL(target.url, window.location.origin).pathname
        } catch {
          previewPath = ''
        }
        const previewPage = developmentPlanningPages.find((page) => page.path === previewPath)
        if (previewPage) {
          setActiveDetailTarget({ type: 'page', pageId: previewPage.pageId })
        }
      }
      // 开发阶段的页面预览收敛在“开发产物”内容区，不再跳转独立浏览器 Tab。
      if (activeWorkbenchPhase === 'development') {
        setRightPanel({ type: 'development-artifacts' })
      }
      setRightPanelLayout('split')
    },
    [
      activeWorkbenchPhase,
      developmentPlanningPages,
      setRightPanel,
      setRightPanelLayout,
      versionViewKey
    ]
  )

  // 同步工作台自动启动返回的最新前端端口和错误，不进行任何浏览器持久化。
  useEffect(() => {
    setRuntimePreviewBaseUrl(previewOrigin(previewBaseUrl))
    setRuntimePreviewLaunchError(previewLaunchError)
  }, [previewBaseUrl, previewLaunchError])

  const {
    activeSession,
    activeSessionId,
    agUiSessionsRef,
    createReviewSession,
    createTestingSession,
    createAcceptanceSession,
    ensureDevelopmentSession,
    ensureAnalysisSession,
    draft,
    draftKey,
    ensureActiveSession,
    ensureEndpointSession,
    ensurePageSession,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    handleRenameSession,
    loadingSessions,
    ensurePlanningSession,
    messages,
    persistSession,
    recordAcceptedFile,
    runningSessionsRef,
    selectedSkills,
    sessions,
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  } = useChatSessions({ application, editorMode })
  // 默认主任务持有推进权；空闲时新建任务直接接棒，查看已有非推进任务时才需显式确认转移。
  const [authorizedEditingSessionId, setAuthorizedEditingSessionId] = useState<string>()
  const [pendingRegularSessionId, setPendingRegularSessionId] = useState<string>()
  // 编辑权限属于当前阶段；进入下一阶段时必须由该阶段默认对话重新取得权限。
  useEffect(() => {
    setAuthorizedEditingSessionId(undefined)
    setPendingRegularSessionId(undefined)
  }, [activeWorkbenchPhase])
  useEffect(() => {
    if (
      authorizedEditingSessionId ||
      !activeSessionId ||
      activeSession?.sessionKind !== activeWorkbenchPhase
    )
      return
    setAuthorizedEditingSessionId(activeSessionId)
  }, [activeSession?.sessionKind, activeSessionId, activeWorkbenchPhase, authorizedEditingSessionId])
  /** 以已授权保存的正式源码判断开发产物是否交付，不使用详设计划状态抢先完成。 */
  const hasSavedCodeFile = (suffix: string): boolean =>
    sessions.some((session) =>
      (session.savedFiles || []).some((file) => {
        const path = file.path.replace(/\\/g, '/').replace(/^.*?wh-branch-pms-new\//, '')
        return path === suffix || path.endsWith(`/${suffix}`)
      })
    )
  const isPageCodeDelivered = (pageId: string): boolean =>
    hasSavedCodeFile(`frontend/pages/${pageId}.tsx`)
  const isEndpointCodeDelivered = (endpointId: string): boolean =>
    endpointId === 'ep-my-rechecks' && hasSavedCodeFile('backend/rechecks-controller.java')
  const activePageCodeDelivered = Boolean(
    activePageOption && isPageCodeDelivered(activePageOption.pageId)
  )
  const directModificationEnabled = activeApiEndpoint
    ? isEndpointCodeDelivered(activeApiEndpoint.endpointId)
    : Boolean(activePageOption && isPageCodeDelivered(activePageOption.pageId))
  const conversationPhase = useMemo<WorkbenchPhase>(() => {
    const sessionKind = activeSession?.sessionKind
    if (
      sessionKind === 'analysis' ||
      sessionKind === 'planning' ||
      sessionKind === 'development' ||
      sessionKind === 'testing' ||
      sessionKind === 'review' ||
      sessionKind === 'acceptance'
    ) {
      return sessionKind
    }
    if (activeSession?.pageId || activeSession?.endpointId) return 'development'
    const sessionTitle = sessions.find((session) => session.id === activeSessionId)?.title || ''
    if (sessionTitle.includes('应用测试')) return 'testing'
    if (sessionTitle.includes('代码审查')) return 'review'
    if (sessionTitle.includes('应用验收')) return 'acceptance'
    if (sessionTitle === '项目计划') return 'planning'
    if (sessionTitle.includes('需求分析')) return 'analysis'
    return renderedTaskPhase
  }, [
    activeSession,
    activeSession?.endpointId,
    activeSession?.pageId,
    activeSession?.sessionKind,
    activeSessionId,
    renderedTaskPhase,
    sessions
  ])
  const expectedDesignSessionKind = activeWorkbenchPhase === 'planning' ? 'planning' : 'analysis'
  const designSessionSwitching =
    isDesignPhase && activeSession?.sessionKind !== expectedDesignSessionKind

  // 旅程切换期间先锁住当前输入区，避免用户把内容写入尚未切换完成的阶段会话；
  // Workflow 运行中的 loading 不属于这里，运行态仍由输入框保留编辑能力、发送键切换为中止。
  const activeSessionMatchesStage =
    Boolean(activeSession) &&
    (activeSession?.sessionKind === activeWorkbenchPhase ||
      (!activeSession?.sessionKind && conversationPhase === activeWorkbenchPhase))
  const stageSessionSwitching = loadingSessions || !activeSessionMatchesStage

  // 需求分析阶段默认会话只归属需求文档，项目规划阶段默认会话只归属项目计划。
  const formalAnalysisSession = useMemo(() => {
    const candidates = sessions.filter(
      (session) =>
        !session.pageId &&
        !session.endpointId &&
        (session.sessionKind === 'analysis' || (session.title || '').includes('需求分析'))
    )
    return candidates.sort((a, b) => a.createdAt - b.createdAt)[0]
  }, [sessions])
  const formalPlanningSession = useMemo(
    () =>
      sessions
        .filter(
          (session) =>
            !session.pageId &&
            !session.endpointId &&
            (session.sessionKind === 'planning' || session.title === '项目计划')
        )
        .sort((a, b) => a.createdAt - b.createdAt)[0] || formalAnalysisSession,
    [formalAnalysisSession, sessions]
  )

  const {
    activeWorkflow,
    error,
    handleSend,
    handleStartEndpointDetailConfirmation,
    handleStartDetailConfirmation,
    handleStopGenerating,
    handleSubmitClarification,
    editingSessionId,
    loading,
    sessionRunStates,
    stopping,
    workspaceBusy
  } = useWorkflowConversation({
    activeSession,
    authorizedEditingSessionId,
    agUiSessionsRef,
    application,
    applicationLifecycle,
    draft,
    draftKey,
    editorMode,
    ensureActiveSession,
    ensureDevelopmentSession,
    ensureAnalysisSession,
    ensurePlanningSession,
    ensureReviewSession: createReviewSession,
    ensureTestingSession: createTestingSession,
    ensureAcceptanceSession: createAcceptanceSession,
    ensureEndpointSession,
    ensurePageSession,
    getSessionMessages,
    persistSession,
    onApplicationLifecycleChange,
    onPreviewReady: handlePreviewReady,
    publishAiMessage,
    runningSessionsRef,
    selectedApiContractId: activeApiEndpoint?.apiContractId,
    selectedEndpointId: activeApiEndpoint?.endpointId,
    selectedSkills,
    selectedPageId: activePageOption?.pageId || activePageOption?.key,
    directModificationEnabled,
    developmentPhase: activeWorkbenchPhase === 'development',
    designPhase: isDesignPhase,
    planningPhase: activeWorkbenchPhase === 'planning',
    testingPhase: activeWorkbenchPhase === 'testing',
    autoStartDesign: isInitialPlanningPhase(applicationLifecycle),
    autoStartTesting: allDevelopmentModulesComplete && testingTransitionRequested,
    acceptancePhase: displayIsAcceptancePhase,
    // 全部业务用例执行通过后才开放审查；进入审查后由审查 Agent 自动开启默认对话。
    autoStartReview:
      !versionReadOnly &&
      activeWorkbenchPhase === 'review' &&
      // 进入审查的准入由顶部/测试门禁负责；一旦当前阶段已是审查，
      // 即使冷启动快照暂时缺少 testExecutionStatus，也必须启动审查 Workflow。
      (testExecutionPassedForEntry || compareWorkbenchPhases(reachedPhase, 'review') >= 0),
    setDraftByKey,
    setSelectedSkillsByKey,
    setSessionMessages
  })

  useEffect(() => {
    // mock Workflow 的 launch_project completed 直接返回页面地址，不一定经过实时预览回调；
    // 将该地址同步为开发产物内容区的预览基址，保证 Diff 接受后立即能看到页面效果。
    if (activeWorkbenchPhase !== 'development') return
    const workflowPreviewUrl = activeWorkflow?.result?.preview_url
    if (typeof workflowPreviewUrl !== 'string') return
    let previewPath = ''
    try {
      previewPath = new URL(workflowPreviewUrl, window.location.origin).pathname
    } catch {
      previewPath = ''
    }
    const previewPage = developmentPlanningPages.find((page) => page.path === previewPath)
    if (previewPage) {
      setActiveDetailTarget((current) =>
        current.type === 'page' && current.pageId === previewPage.pageId
          ? current
          : { type: 'page', pageId: previewPage.pageId }
      )
    }
    const nextBaseUrl = previewOrigin(workflowPreviewUrl)
    if (nextBaseUrl && nextBaseUrl !== runtimePreviewBaseUrl) {
      setRuntimePreviewBaseUrl(nextBaseUrl)
      setRuntimePreviewLaunchError('')
    }
  }, [
    activeWorkbenchPhase,
    activeWorkflow?.result?.preview_url,
    developmentPlanningPages,
    runtimePreviewBaseUrl
  ])

  const applicationPreviewUrl = composeVersionPreviewUrl(runtimePreviewBaseUrl, '/', versionViewKey)
  const acceptancePreviewEntryRef = useRef('')
  useEffect(() => {
    // 验收进入时默认全宽展示独立“应用预览”Tab；后续 Tab 切换不再改变用户选择的宽度。
    if (!displayIsAcceptancePhase || !applicationPreviewUrl) {
      if (!displayIsAcceptancePhase) {
        acceptancePreviewEntryRef.current = ''
      }
      return
    }
    const entryKey = `${versionViewKey}:${String(applicationLifecycle?.extensions?.reviewStatus || '')}`
    if (acceptancePreviewEntryRef.current === entryKey) return
    acceptancePreviewEntryRef.current = entryKey
    setRightPanel({
      type: 'preview',
      requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:acceptance`,
      url: applicationPreviewUrl
    })
    setRightPanelLayout('full')
  }, [
    applicationLifecycle?.extensions?.reviewStatus,
    applicationPreviewUrl,
    displayIsAcceptancePhase,
    runtimePreviewBaseUrl,
    setRightPanel,
    setRightPanelLayout,
    versionViewKey
  ])

  /** 切换右侧工作区三档布局：隐藏、分栏和全宽覆盖。 */
  const handleRightPanelLayoutChange = (layout: RightPanelLayout): void => {
    if (layout === 'hidden') {
      // 隐藏只收起工作区，不清除当前 Tab，恢复后仍回到用户上一次查看的内容。
      autoOpenStateRef.current.dismissed = true
      setRightPanelLayout('hidden')
      return
    }
    if (layout === 'split') {
      // 极早期尚未生成右侧内容时，先提供稳定的文档工作区，避免“分栏”后为空。
      if (!rightPanel) setRightPanel({ type: 'doc' })
      setRightPanelLayout('split')
      return
    }
    // 全宽仅改变公共面板尺寸；当前文件、浏览器或应用预览 Tab 保持不变。
    if (!rightPanel) setRightPanel({ type: 'doc' })
    setRightPanelLayout('full')
  }

  // 开发产物写入完成不等于开发工作流完成；单元测试、集成测试和预览启动仍在运行时，
  // 必须禁止“进入测试阶段”提示，避免用户在上一条工作流尚未收尾时被提前切阶段。
  const developmentWorkflowRunning =
    loading ||
    Object.entries(sessionRunStates).some(([sessionId, status]) => {
      if (status !== 'running' && status !== 'stopping' && status !== 'awaiting_user') return false
      const session = sessions.find((item) => item.id === sessionId)
      return Boolean(
        session?.sessionKind === 'development' || session?.pageId || session?.endpointId
      )
    })
  // 测试阶段“允许进入”判定：开发产物全部完成且当前开发工作流已收尾。
  const testingEntryAvailable =
    activeWorkbenchPhase === 'development' &&
    allDevelopmentModulesComplete &&
    !developmentWorkflowRunning &&
    !versionReadOnly &&
    !testingTransitionRequested
  // 全部业务用例执行通过后才开放审查入口，测试阶段不依赖报告确认。
  const reviewEntryAvailable =
    testExecutionPassedForEntry &&
    compareWorkbenchPhases(reachedPhase, 'review') < 0 &&
    !loading &&
    !workspaceBusy &&
    !versionReadOnly &&
    !reviewTransitionRequested

  useEffect(() => {
    // 全部业务用例执行通过后只提示进入审查，测试阶段仍停留在当前工作台供用户查看结果。
    if (
      versionReadOnly ||
      activeWorkbenchPhase !== 'testing' ||
      !testExecutionPassedForEntry ||
      reviewTransitionRequested ||
      loading ||
      workspaceBusy
    ) {
      if (activeWorkbenchPhase !== 'testing') reviewCompletionPromptRef.current = ''
      return
    }
    const promptKey = `${versionViewKey}:testing-complete`
    if (reviewCompletionPromptRef.current === promptKey) return
    reviewCompletionPromptRef.current = promptKey
    setReviewCompleteModalOpen(true)
  }, [
    activeWorkbenchPhase,
    application.id,
    applicationLifecycle,
    loading,
    onApplicationLifecycleChange,
    reviewTransitionRequested,
    testExecutionPassedForEntry,
    versionReadOnly,
    versionViewKey,
    workspaceBusy
  ])

  // 顶部应用配置入口复用现有配置页，不在工作台中增加第二套配置界面。
  const { requestCodeChangeRevert, revertingCodeChangeIds } = useCodeChangeRevert({
    activeSession,
    disabled: loading || workspaceBusy,
    getSessionMessages,
    persistSession,
    rightPanel,
    setRightPanel,
    setSessionMessages
  })

  // 输入框引导语按阶段/任务/迭代单独写（剧本范畴）：设计(新应用 vs 迭代)/审查/开发(页面/接口/应用)。
  const baseChatCopy = chatCopy[editorMode]
  const iterationVersion = (application.versions || []).find(
    (v) => v.id === application.currentVersionId
  )
  const composerPlaceholder = displayIsDesignPhase
    ? viewingTaskPhase === 'planning'
      ? '描述项目计划需要调整的页面、接口或实体范围…'
      : iterationVersion?.parentVersionId
        ? '描述本次迭代要补充或调整的需求，例如新增功能、调整流程…'
        : '描述应用的核心场景与业务需求，或确认当前需求文档…'
    : displayIsReviewPhase
      ? '确认审查结果，或输入审查意见…'
      : activeApiEndpoint
        ? '描述想调整的接口，例如参数、返回值、业务逻辑…'
        : activePageOption
          ? '描述想微调的页面，例如修改文案、样式、字段…'
          : '描述应用整体调整，或确认开发计划…'
  const copy = { ...baseChatCopy, placeholder: composerPlaceholder }
  const workspaceRoot = application.workspaceRoot || '未选择工作目录'
  const activePageTitle =
    activePageOption?.label || application.defaultPage || application.pages[0] || '页面'
  const activePage = useMemo(
    () => findPageMenuItem(application.menus?.items || [], activePageTitle),
    [activePageTitle, application.menus?.items]
  )
  const activeApiEndpointOption = useMemo(() => {
    if (!activeApiEndpoint) return undefined
    for (const contract of developmentPlanningApiContracts) {
      for (const [endpointIndex, endpoint] of contract.endpoints.entries()) {
        const endpointId = endpoint.id || String(endpointIndex + 1)
        const apiContractId = endpoint.apiContractId || contract.id
        const endpointKey = `${apiContractId}:${endpointId}`
        if (
          endpointKey === activeApiEndpoint.endpointKey ||
          (apiContractId === activeApiEndpoint.apiContractId &&
            endpointId === activeApiEndpoint.endpointId)
        ) {
          return { contract, endpoint, endpointId, endpointKey, apiContractId }
        }
      }
    }
    return undefined
  }, [activeApiEndpoint, developmentPlanningApiContracts])
  const activeHeaderTarget = activeApiEndpoint
    ? {
        type: 'api' as const,
        title: activeApiEndpoint.label,
        path:
          activeApiEndpointOption?.endpoint.path ||
          activeApiEndpoint.label.replace(/^[A-Z]+\s+/, ''),
        description:
          activeApiEndpointOption?.endpoint.summary ||
          `接口来自 ${activeApiEndpointOption?.contract.label || activeApiEndpoint.apiContractId}`,
        keyFeatures: [
          `Method：${activeApiEndpointOption?.endpoint.method || activeApiEndpoint.label.split(' ')[0] || 'API'}`,
          `Contract：${activeApiEndpointOption?.contract.label || activeApiEndpoint.apiContractId}`,
          activeApiEndpointOption?.endpoint.designed ||
          activeApiEndpointOption?.endpoint.hasDetailPlan
            ? '状态：已设计'
            : '状态：待设计'
        ]
      }
    : activeDetailTarget.type === 'page'
      ? {
          type: 'page' as const,
          title: activePageTitle,
          path: activePageOption?.path || activePage?.path || '/',
          description:
            activePageOption?.purpose ||
            activePage?.purpose ||
            application.senario ||
            '当前应用页面',
          keyFeatures: activePage?.keyFeatures || []
        }
      : {
          type: 'application' as const,
          title: application.name,
          path: '/',
          description: application.senario || '完整应用预览与版本成果',
          keyFeatures: ['应用级预览', '页面与接口完成情况', '版本成果']
        }
  const latestWorkflowForDisplay = activeWorkflow || latestMessageWorkflow(messages)
  const developmentArtifactRefreshRef = useRef('')
  useEffect(() => {
    // 构建终态在 mock/真实 AG-UI 中都可能晚于生命周期快照落盘；终态到达后主动刷新一次，
    // 让左侧产物状态、测试准入和右侧预览共同消费同一份最新规划事实。
    if (activeWorkbenchPhase !== 'development') return
    const workflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    const terminalBuild =
      workflow?.summary?.status === 'completed' &&
      ['build', 'launch_project', 'integration_test'].includes(String(workflow.summary.phase || ''))
    if (!terminalBuild) return
    const refreshKey = `${versionViewKey}:${workflow.runId}:${workflow.summary.phase}`
    if (developmentArtifactRefreshRef.current === refreshKey) return
    developmentArtifactRefreshRef.current = refreshKey
    onPlanningArtifactsRefresh()
  }, [activeWorkbenchPhase, latestWorkflowForDisplay, onPlanningArtifactsRefresh, versionViewKey])
  // 当前写入中的单文件变更：固定放在对话输入框上方，作为独立的授权条展示。
  const pendingCodeChangeContext = (() => {
    const workflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    const changes = workflowCodeChanges(workflow)
    const pending =
      workflow?.summary?.status === 'running' || workflow?.summary?.status === 'requires_user_input'
    if (
      !pending ||
      !changes ||
      changes.files.length === 0 ||
      !isPendingDesignCodeChange(workflow, applicationLifecycle?.initialization?.stage)
    ) {
      return undefined
    }
    const sourceMessage = [...messages].reverse().find((message) => {
      const messageChanges = workflowCodeChanges(message.workflow)
      return messageChanges?.id === changes.id
    })
    return { changes, messageId: sourceMessage?.id, workflow }
  })()
  const pendingFileDiff = pendingCodeChangeContext
    ? {
        path: pendingCodeChangeContext.changes.files[0].path,
        diff: pendingCodeChangeContext.changes.files[0].diff,
        additions: pendingCodeChangeContext.changes.files[0].additions
      }
    : undefined

  // 开发准入门：先看当前查看会话的最新工作流，再回退扫描规划阶段全部会话——
  // 推进权/查看对象切到新建任务后，规划默认任务里挂起的门禁仍要可被唤起。
  const developmentEntryWorkflow =
    pendingGateWorkflow(
      latestWorkflowForDisplay as WorkflowRunPayload | undefined,
      'development_entry_confirmation'
    ) ||
    findPendingGateWorkflow(sessions, 'planning', {
      mode: 'development_entry_confirmation',
      readMessages: getSessionMessages,
      runtimeKey: (sessionId) =>
        sessionRuntimeKey(application.workspaceRoot || '', editorMode, sessionId)
    })
  // 开发准入门是否待处理：与测试/审查的“可进入”语义对齐，供上报与请求消费共用。
  const developmentEntryAvailable = Boolean(developmentEntryWorkflow)

  // 只有独立开发准入 gate 已到达且计划消息完成后，才打开任务类型选择弹框。
  useEffect(() => {
    if (
      !developmentEntryWorkflow ||
      dismissedDevelopmentEntryRunId === developmentEntryWorkflow.runId ||
      testCaseTaskTypeModalOpen ||
      loading ||
      stageSessionSwitching
    ) {
      return
    }
    // 用 setTimeout 延迟一拍而非双 requestAnimationFrame：
    // 后台标签页或嵌入式容器可能长期不泵帧，帧回调不执行时门禁弹框将永远无法自动弹出。
    const timer = window.setTimeout(() => setTestCaseTaskTypeModalOpen(true), 0)
    return () => window.clearTimeout(timer)
  }, [
    developmentEntryWorkflow,
    dismissedDevelopmentEntryRunId,
    loading,
    stageSessionSwitching,
    testCaseTaskTypeModalOpen
  ])

  // 开发准入门待处理即上报“可进入开发”：顶部阶段条据此点亮开发阶段，供用户暂离后重新唤起。
  useEffect(() => {
    onDevelopmentEntryAvailableChange?.(developmentEntryAvailable)
  }, [developmentEntryAvailable, onDevelopmentEntryAvailableChange])

  // 测试阶段只读执行用例；需要修复时回到开发主对话创建新的 Workflow。
  // 固定应用预览始终使用应用根路由，并按当前查看版本渲染快照。
  const previewTabUrl = applicationPreviewUrl
  // 自动开预览：切换目标页 → 重新自动开；用户手动关闭后不再抢开（dismissed），直到切换页面。
  const autoOpenStateRef = useRef<{
    page: string
    dismissed: boolean
    type: WorkspaceTabKey | null
  }>({ page: '', dismissed: false, type: null })
  // 记录当前右侧面板归属的页面，页面切换时强制刷新面板内容（未设计页空占位）。
  const lastPanelForPageRef = useRef('')
  useEffect(() => {
    const state = autoOpenStateRef.current
    if (state.page !== activePageId) {
      state.page = activePageId
      state.dismissed = false
      state.type = null
    }
    // 历史版本始终以应用级预览为主视图（切版本时强制回应用预览，不受 dismissed 影响），
    // 不让所属阶段的默认文档覆盖版本预览。
    // 测试阶段必须让用户看到用例工作台，不沿用开发阶段手动关闭面板的状态。
    if (displayIsTestingPhase) state.dismissed = false
    // 从其他阶段回到开发时不沿用旧的应用预览；先回到应用文件，等待本轮编码 Diff。
    if (activeWorkbenchPhase === 'development' && rightPanel?.type === 'preview') {
      state.dismissed = false
      state.type = 'source'
      setRightPanel({ type: 'source' })
      return
    }
    if (state.dismissed) return
    // 需求分析/项目规划阶段：右侧固定「文档」区，自动落到第一份已生成产物（需求文档/项目计划）。
    // 注意：本 effect 依赖 rightPanel，必须仅在非 doc 或未选中有效文档时才 set，否则每次新建对象 →
    // rightPanel 引用变 → effect 重跑 → 再 set，形成 Maximum update depth 死循环。
    if (displayIsDesignPhase) {
      // 文档页签固定：Diff 写入过程由 DocPanel 内嵌的 FileDiffView 呈现（单文件、绿色新增行），
      // 生成/待接受期间不需要打开独立的变更审阅面板。
      state.type = 'doc'
      // active docKey 跟随旅程：生成中按 workflow.phase，就绪后按 stage（见 resolveDesignDocKey）。
      const targetKey = resolveDesignDocKey(
        applicationLifecycle?.initialization?.stage,
        activeWorkflow?.summary?.phase,
        activeWorkflow?.summary?.status === 'running',
        activeWorkbenchPhase
      )
      const currentKey = rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
      const shouldOpen = !rightPanel || rightPanel.type !== 'doc'
      const keyChanged = !shouldOpen && currentKey !== targetKey
      if (shouldOpen || keyChanged) {
        setRightPanel({ type: 'doc', docKey: targetKey })
      }
      return
    }
    // 开发阶段入口先保留“应用文件”源码区，避免在首个编码 Diff 出现前抢先打开开发产物。
    // 未交付页面也不能沿用此前的预览，后续 Diff 到达时再由下方分支切回源码区。
    if (
      activeWorkbenchPhase === 'development' &&
      activePageOption &&
      !activePageCodeDelivered &&
      rightPanel?.type === 'preview'
    ) {
      state.type = 'source'
      setRightPanel({ type: 'source' })
      return
    }
    // 页面/接口详细设计是工作流内部过程，不生成用户文件；开发阶段从代码 Diff 开始呈现。
    if (
      activeWorkbenchPhase === 'development' &&
      activeWorkflow?.summary?.phase === 'detail_confirmation' &&
      !pendingFileDiff
    ) {
      state.type = null
      // 详细设计仍是工作流内部过程，没有新文件时保持用户当前查看的文件页。
      return
    }
    // 代码工作流尚未产生 Diff 时不展示旧的设计文档；代码交付完成后切到源码区。
    if (
      activeWorkbenchPhase === 'development' &&
      activeWorkflow?.summary?.phase === 'build' &&
      !pendingFileDiff
    ) {
      state.type = null
      if (activeWorkflow.summary.status === 'completed') {
        // 同步任务落终态且携带产物审查目标时，右侧保持「启动产物审查」的开发产物视图，
        // 由产物审查 effect 定位当前产物（页面预览/接口调试）；无审查目标时才收口到源码区。
        if (!workflowReviewTarget(activeWorkflow) && rightPanel?.type !== 'source') {
          setRightPanel({ type: 'source' })
        }
      }
      // 构建节点准备下一个文件时不切走当前源码页；新 Diff 到达后由下方分支接管。
      return
    }
    // 开发阶段所有目标统一走单文件 Diff：页面、页面的直接接口依赖，以及独立接口对话都不能跳过。
    // 该判断必须早于接口文档分支和页面存在性判断，否则直接打开接口产物时右侧不会切到 Diff。
    const buildWorkflow = latestWorkflowForDisplay as WorkflowRunPayload | undefined
    if (
      pendingFileDiff &&
      ['build', 'detail_confirmation'].includes(String(buildWorkflow?.summary?.phase || ''))
    ) {
      state.type = null
      const targetTab: WorkspaceTabKey = pendingFileDiff.path.startsWith('backend/')
        ? 'endpoint-source'
        : 'page-source'
      if (activeArtifactTab !== targetTab) setActiveArtifactTab(targetTab)
      if (rightPanel?.type !== 'source') setRightPanel({ type: 'source' })
      return
    }
    // 测试阶段固定展示“测试用例”工作台，不再切到报告文档。
    if (displayIsTestingPhase) {
      state.type = 'test-cases'
      if (!rightPanel || rightPanel.type !== 'test-cases') setRightPanel({ type: 'test-cases' })
      return
    }
    // 开发阶段默认打开「开发产物」：让用户直观看到当前还没有任何已实施产物。
    // 用户手动切到其它 Tab（dismissed）时不抢回；首个编码 Diff 出现后由上方分支接管。
    if (activeWorkbenchPhase === 'development' && !pendingFileDiff) {
      if (state.dismissed) return
      state.type = 'development-artifacts'
      if (rightPanel?.type !== 'development-artifacts') {
        setRightPanel({ type: 'development-artifacts' })
      }
      return
    }
    // 开发阶段的接口调试由“开发产物”内容区承载；其他阶段保持原有文档视图。
    if (activeApiEndpoint && activeWorkbenchPhase === 'development') {
      state.type = 'development-artifacts'
      if (rightPanel?.type !== 'development-artifacts') {
        setRightPanel({ type: 'development-artifacts' })
      }
      return
    }
    // 非开发阶段的接口目标仍固定展示对应文档。
    if (activeApiEndpoint) {
      state.type = 'doc'
      if (!rightPanel || rightPanel.type !== 'doc') {
        setRightPanel({ type: 'doc' })
      }
      return
    }
    // 审查阶段：右侧固定「审查报告」tab（报告内容随工作流进度填充，见 docContent）。
    // 已发布/锁定历史版本默认应用预览（versionPreviewOnly 分支已处理，与此互斥）。
    if (displayIsReviewPhase) {
      if (rightPanel?.type === 'preview' && state.dismissed) {
        state.type = 'preview'
        return
      }
      state.type = 'doc'
      if (!rightPanel || rightPanel.type !== 'doc') {
        setRightPanel({ type: 'doc' })
      }
      return
    }
    if (!activePageOption) return
    // 预览在页面开发完成（integration_test 通过）即可展示；launch_project/acceptance 向前兼容。
    const previewLaunched =
      (activeWorkflow?.summary?.phase === 'launch_project' &&
        activeWorkflow.summary.status === 'completed') ||
      activeWorkflow?.summary?.phase === 'acceptance' ||
      (activeWorkflow?.summary?.phase === 'integration_test' &&
        activeWorkflow?.summary?.status === 'completed')
    // 页面任务默认开「页面预览」（当前页面 path），与应用预览（应用根）区分。
    const workflowPreviewUrl = activeWorkflow?.result?.preview_url
    const launchedPreviewUrl =
      composeVersionPreviewUrl(
        runtimePreviewBaseUrl,
        activeHeaderTarget.path || '/',
        versionViewKey
      ) || (typeof workflowPreviewUrl === 'string' ? workflowPreviewUrl : '')
    if (previewLaunched && launchedPreviewUrl) {
      lastPanelForPageRef.current = activePageId || ''
      if (activeWorkbenchPhase === 'development') {
        state.type = 'development-artifacts'
        if (rightPanel?.type !== 'development-artifacts') {
          setRightPanel({ type: 'development-artifacts' })
        }
      } else if (
        !state.dismissed &&
        (rightPanel?.type !== 'preview' || !rightPanel.requestKey?.endsWith(':page'))
      ) {
        state.type = 'preview'
        setRightPanel({
          type: 'preview',
          requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:page`,
          url: launchedPreviewUrl
        })
      }
      return
    }
    // 当前已有 Workflow 但没有新文件写入时，不再用通用兜底覆盖右侧已有文件。
    if (activeWorkbenchPhase === 'development' && activeWorkflow) return
    // 非开发阶段的空状态不主动打开右侧内容（开发阶段的默认「开发产物」已在上方处理）。
    if (!rightPanel) {
      state.type = 'doc'
      setRightPanel({ type: 'doc' })
    }
  }, [
    rightPanel,
    setRightPanel,
    activePageOption,
    previewTabUrl,
    runtimePreviewBaseUrl,
    activeHeaderTarget.path,
    activePageId,
    activeApiEndpoint,
    applicationPreviewUrl,
    displayIsDesignPhase,
    displayIsTestingPhase,
    activeWorkbenchPhase,
    displayIsReviewPhase,
    versionPreviewOnly,
    versionViewKey,
    applicationLifecycle?.initialization?.stage,
    activeWorkflow?.summary?.phase,
    activeWorkflow?.summary?.status,
    // 工作流对象本身也要入依赖：构建节点的渐进变更快照只改 state.codeChanges / pendingFileDiff，
    // summary.phase/status 不变时也必须驱动右侧 Diff 面板更新（否则只显示终态）。
    latestWorkflowForDisplay,
    pendingFileDiff,
    activePageCodeDelivered
  ])

  // 页面详细设计只作为工作流内部上下文，右侧不再展示或持久化单独的设计文档。
  const designStage = applicationLifecycle?.initialization?.stage
  // 开发产物在单个版本内只解锁一次，避免生命周期事件切换时整组目录闪退再出现。
  const [developmentCatalogUnlocked, setDevelopmentCatalogUnlocked] = useState(() =>
    isDevelopmentCatalogConfirmed(designStage)
  )
  useEffect(() => {
    if (isDevelopmentCatalogConfirmed(designStage)) setDevelopmentCatalogUnlocked(true)
  }, [designStage])
  const developmentCompletionPromptRef = useRef('')
  useEffect(() => {
    // 开发完成弹框只属于开发阶段；进入测试后必须关闭，测试完成由独立审查门禁承接。
    if (activeWorkbenchPhase !== 'development') {
      setDevelopmentCompleteModalOpen(false)
      if (activeWorkbenchPhase !== 'testing') {
        developmentCompletionPromptRef.current = ''
        setTestingTransitionRequested(false)
      }
      return
    }
    if (
      versionReadOnly ||
      developmentWorkflowRunning ||
      !allDevelopmentModulesComplete ||
      testingTransitionRequested
    )
      return
    const promptKey = `${versionViewKey}:development-complete`
    if (developmentCompletionPromptRef.current === promptKey) return
    developmentCompletionPromptRef.current = promptKey
    setDevelopmentCompleteModalOpen(true)
  }, [
    activeWorkbenchPhase,
    allDevelopmentModulesComplete,
    developmentWorkflowRunning,
    testingTransitionRequested,
    versionReadOnly,
    versionViewKey
  ])
  // 向顶部阶段条上报测试阶段是否具备进入条件，供其放开测试节点点击。
  useEffect(() => {
    onTestingEntryAvailableChange?.(testingEntryAvailable)
  }, [onTestingEntryAvailableChange, testingEntryAvailable])
  useEffect(() => {
    onReviewEntryAvailableChange?.(reviewEntryAvailable)
  }, [onReviewEntryAvailableChange, reviewEntryAvailable])
  // 顶部阶段条发起进入请求时复用同一个确认弹框：只有确认动作才创建测试对话并推进阶段。
  useCountedRequestTrigger({
    available: testingEntryAvailable,
    onOpen: () => setDevelopmentCompleteModalOpen(true),
    request: testingEntryRequest
  })
  useCountedRequestTrigger({
    available: reviewEntryAvailable,
    onOpen: () => setReviewCompleteModalOpen(true),
    request: reviewEntryRequest
  })
  // 开发准入门与测试/审查同一模式：弹框可暂不进入，稍后点击顶部开发阶段按钮重新唤起。
  useCountedRequestTrigger({
    available: developmentEntryAvailable,
    onOpen: () => {
      // 清除取消时的 dismiss 标记，自动打开 effect 才允许再次弹出同一 gate。
      setDismissedDevelopmentEntryRunId('')
      setTestCaseTaskTypeModalOpen(true)
    },
    request: developmentEntryRequest
  })
  const activeDesignSessionSummary = activeSession
    ? sessions.find((session) => session.id === activeSession.sessionId)
    : undefined
  // 首个设计会话由首页动作创建；用户消息、Agent 消息或运行态任一出现都代表产物已开始。
  const designConversationStarted = Boolean(
    displayIsDesignPhase &&
      activeSession &&
      (messages.some((message) => message.role === 'user' || message.role === 'assistant') ||
        (activeDesignSessionSummary?.messageCount || 0) > 0 ||
        sessionRunStates[activeSession.sessionId])
  )
  const [stableDesignStatuses, setStableDesignStatuses] = useState(() => ({
    'requirement-spec': 'not-started' as WorkbenchArtifactStatus,
    'project-plan': 'not-started' as WorkbenchArtifactStatus
  }))
  const [stableDesignAvailability, setStableDesignAvailability] = useState(() => ({
    'requirement-spec': designStageReached(designStage, DESIGN_DOC_THRESHOLDS['requirement-spec']),
    'project-plan': designStageReached(designStage, DESIGN_DOC_THRESHOLDS['project-plan'])
  }))
  useEffect(() => {
    const requirementPath = appPath(WORKSPACE_DOC_PATHS.requirementSpec)
    const projectPlanPath = appPath(WORKSPACE_DOC_PATHS.projectPlan)
    const latestSavedAt = (session: typeof formalAnalysisSession, path: string): number =>
      (session?.savedFiles || [])
        .filter(
          (file) =>
            (file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)) === path
        )
        .reduce((latest, file) => Math.max(latest, file.savedAt), 0)
    const requirementSavedAt = latestSavedAt(formalAnalysisSession, requirementPath)
    const projectPlanSavedAt = latestSavedAt(formalPlanningSession, projectPlanPath)
    const sessionStarted = (session: typeof formalAnalysisSession): boolean =>
      Boolean(session && session.messageCount > 0)
    const analysisSessionStarted =
      sessionStarted(formalAnalysisSession) ||
      Boolean(activeSession?.sessionKind === 'analysis' && messages.length > 0)
    const planningSessionStarted =
      sessionStarted(formalPlanningSession) ||
      Boolean(conversationPhase === 'planning' && messages.length > 0)
    // 文档写入后需求本身保持完成；只有新需求确实保存成功，才使下游项目计划重新进入进行中。
    setStableDesignStatuses({
      'requirement-spec': requirementSavedAt
        ? 'completed'
        : analysisSessionStarted
          ? 'in-progress'
          : 'not-started',
      'project-plan':
        projectPlanSavedAt && projectPlanSavedAt >= requirementSavedAt
          ? 'completed'
          : planningSessionStarted || requirementSavedAt > projectPlanSavedAt
            ? 'in-progress'
            : 'not-started'
    })
    // 已经生成过的文档继续保留在产物视图中，失效只体现在状态变化，不变成未来阶段灰色节点。
    setStableDesignAvailability((current) => ({
      'requirement-spec':
        current['requirement-spec'] ||
        designConversationStarted ||
        designStageReached(designStage, DESIGN_DOC_THRESHOLDS['requirement-spec']),
      'project-plan':
        current['project-plan'] ||
        designStageReached(designStage, DESIGN_DOC_THRESHOLDS['project-plan'])
    }))
  }, [
    designConversationStarted,
    designStage,
    formalAnalysisSession,
    formalPlanningSession,
    activeSession?.sessionKind,
    conversationPhase,
    messages.length
  ])
  // 产物状态只关心实现任务的状态位，不关心进度百分比；用签名做依赖，
  // 避免引擎插值进度每次变化都触发全量状态推导。
  const artifactTaskStatusSignature = useMemo(
    () =>
      versionBackgroundTasks
        .filter((task) => task.kind === 'artifact_implementation')
        .map(
          (task) =>
            `${task.id}:${task.status}:${task.nextStep?.done ? 'accepted' : 'pending'}:${task.artifactIds.join(',')}`
        )
        .join('|'),
    [versionBackgroundTasks]
  )
  useEffect(() => {
    /** 共享路由等公共文件可能由另一个页面会话交付；状态按整个版本的正式文件快照判定。 */
    const savedFileExists = (suffix: string): boolean =>
      sessions.some((session) =>
        (session.savedFiles || []).some((file) => {
          const path = file.path.replace(/\\/g, '/').replace(/^.*?wh-branch-pms-new\//, '')
          return path === suffix || path.endsWith(`/${suffix}`)
        })
      )
    const artifactFilesComplete = (artifactId: string): boolean => {
      if (artifactId.startsWith('page:')) {
        const pageId = artifactId.replace(/^page:/, '')
        // 路由属于应用初始化脚手架，不是页面产物；页面源码授权完成即可完成页面产物。
        return savedFileExists(`frontend/pages/${pageId}.tsx`)
      }
      if (artifactId.startsWith('endpoint:'))
        return savedFileExists('backend/rechecks-controller.java')
      return false
    }
    const workflowSnapshots = messages
      .map((message) => message.workflow)
      .filter((workflow): workflow is WorkflowRunPayload => Boolean(workflow))
    const statusForArtifact = (artifactId: string): WorkbenchArtifactStatus => {
      const completedArtifactIds =
        completedDevelopmentArtifactIdsRef.current.get(versionViewKey) || new Set<string>()
      completedDevelopmentArtifactIdsRef.current.set(versionViewKey, completedArtifactIds)
      // 文件确认或只读版本已经形成事实快照；后续会话摘要刷新不能覆盖这个完成事实。
      if (versionReadOnly || artifactFilesComplete(artifactId)) {
        completedArtifactIds.add(artifactId)
      }
      if (completedArtifactIds.has(artifactId)) return 'completed'
      // 产物状态只由目标 Workflow 推导；会话仅保存消息，不再承担产物归属或写锁。
      return workflowSnapshots.some(
        (workflow) => developmentWorkflowArtifactId(workflow) === artifactId
      )
        ? 'in-progress'
        : 'not-started'
    }
    setDevelopmentStatusState(() => {
      const nextStatuses: Record<string, WorkbenchArtifactStatus> = {}
      developmentPlanningPages.forEach((page) => {
        const artifactId = pageArtifactId(page.pageId)
        nextStatuses[artifactId] = statusForArtifact(artifactId)
      })
      developmentPlanningApiContracts.forEach((contract) => {
        contract.endpoints.forEach((endpoint, endpointIndex) => {
          const apiContractId = endpoint.apiContractId || contract.id
          const endpointId = endpoint.id || String(endpointIndex + 1)
          const artifactId = endpointArtifactId(apiContractId, endpointId)
          nextStatuses[artifactId] = statusForArtifact(artifactId)
        })
      })
      // 后台实现任务覆盖产物状态：任务流水是「排队/实现中/待验收/失败/完成」的权威来源；
      // 按更新次序应用，同一产物以最新任务为准，且已确认完成的产物不回退。
      versionBackgroundTasks
        .filter((task) => task.kind === 'artifact_implementation')
        .slice()
        .sort((left, right) => left.updatedAt - right.updatedAt)
        .forEach((task) => {
          const nextStatus = backgroundTaskArtifactStatus(task)
          if (!nextStatus) return
          task.artifactIds.forEach((artifactId) => {
            if (nextStatuses[artifactId] === 'completed') return
            nextStatuses[artifactId] = nextStatus
          })
        })
      return { statuses: nextStatuses, versionKey: versionViewKey }
    })
    // 依赖使用任务状态签名：进度百分比变化不影响产物状态，不必重新推导。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    artifactTaskStatusSignature,
    developmentPlanningApiContracts,
    developmentPlanningPages,
    messages,
    sessions,
    versionReadOnly,
    versionViewKey
  ])
  const developmentArtifactStatusById =
    developmentStatusState.versionKey === versionViewKey ? developmentStatusState.statuses : {}
  const developmentArtifactItems = useMemo<DevelopmentArtifactItem[]>(
    () => [
      ...developmentPlanningPages.map((page) => ({
        id: pageArtifactId(page.pageId),
        kind: 'page' as const,
        label: page.label,
        path: page.path,
        status: developmentArtifactStatusById[pageArtifactId(page.pageId)] || 'not-started'
      })),
      ...developmentPlanningApiContracts.flatMap((contract) =>
        contract.endpoints.map((endpoint, endpointIndex) => {
          const apiContractId = endpoint.apiContractId || contract.id
          const endpointId = endpoint.id || String(endpointIndex + 1)
          return {
            groupId: contract.id,
            groupLabel: contract.label,
            id: endpointArtifactId(apiContractId, endpointId),
            kind: 'endpoint' as const,
            label: `${endpoint.method} ${endpoint.path}`,
            path: endpoint.path,
            status:
              developmentArtifactStatusById[endpointArtifactId(apiContractId, endpointId)] ||
              'not-started'
          }
        })
      ),
      ...developmentPlanningEntities.map((entity) => ({
        id: entityArtifactId(entity.entityId),
        kind: 'entity' as const,
        label: entity.label,
        path: entity.schemaRef || `entities/${entity.entityId}`,
        status: developmentArtifactStatusById[entityArtifactId(entity.entityId)] || 'not-started'
      }))
    ],
    [
      developmentArtifactStatusById,
      developmentPlanningApiContracts,
      developmentPlanningEntities,
      developmentPlanningPages
    ]
  )
  const developmentArtifactProgress = useMemo<WorkbenchArtifactProgress>(() => {
    const previous = developmentArtifactProgressRef.current
    // 规划目录异步刷新期间可能短暂返回空数组；沿用当前版本上一份快照，避免顶部出现 0/3 回退。
    if (
      previous.versionKey === versionViewKey &&
      developmentArtifactItems.length === 0 &&
      previous.progress.total > 0
    ) {
      return previous.progress
    }
    const nextProgress = {
      completed: developmentArtifactItems.filter((item) => item.status === 'completed').length,
      total: developmentArtifactItems.length
    }
    developmentArtifactProgressRef.current = { progress: nextProgress, versionKey: versionViewKey }
    return nextProgress
  }, [developmentArtifactItems, versionViewKey])
  useEffect(() => {
    // 顶部阶段条只展示当前版本的开发产物总进度，目录不再重复显示总计。
    onDevelopmentArtifactProgressChange?.(developmentArtifactProgress)
  }, [developmentArtifactProgress, onDevelopmentArtifactProgressChange])
  const developmentAutoTargets = useMemo<DevelopmentAutoTarget[]>(
    () => [
      ...developmentPlanningPages.map((page) => ({
        artifactId: pageArtifactId(page.pageId),
        kind: 'page' as const,
        page
      }))
    ],
    [developmentPlanningPages]
  )
  const developmentHistoryRepairRef = useRef(new Set<string>())

  useEffect(() => {
    // 旧的原型运行态可能已经把完成页面写入产物快照，却在会话切换竞态中丢了消息。
    // 进入当前契约后把缺失的完成 Workflow 补回唯一主对话，后续正常流程不会命中此分支。
    // 多对话后必须限定「应用开发」主对话：用户新建的空白开发对话一旦处于激活态，
    // 会因本地没有产物消息而被误判为“历史丢失”，把其它对话的工作流总结卡串写进来。
    const activeSessionIsDevelopmentMain =
      activeSession?.sessionKind === 'development' &&
      sessions.find((session) => session.id === activeSessionId)?.title === '应用开发'
    if (
      activeWorkbenchPhase !== 'development' ||
      !activeSessionIsDevelopmentMain ||
      versionReadOnly
    ) {
      return
    }
    const completedPages = developmentAutoTargets.filter(
      (target) => developmentArtifactStatusById[target.artifactId] === 'completed'
    )
    const missingPages = completedPages.filter((target) => {
      const repairKey = `${versionViewKey}:${target.artifactId}`
      if (developmentHistoryRepairRef.current.has(repairKey)) return false
      return !messages.some((message) => {
        const state = (message.workflow?.state || {}) as Record<string, unknown>
        const result = (message.workflow?.result || {}) as Record<string, unknown>
        const pageId = String(state.selectedPageId || result.selectedPageId || '').trim()
        return pageId === target.page.pageId
      })
    })
    if (missingPages.length === 0) return

    const now = Date.now()
    const repairedMessages: AgentChatMessage[] = missingPages.map((target, index) => {
      const workflow: WorkflowRunPayload = {
        runId: `development-history-${versionViewKey}-${target.page.pageId}`,
        threadId: activeSession.threadId,
        summary: {
          phase: 'launch_project',
          status: 'completed',
          message: `「${target.page.label}」页面已完成开发与预览。`
        },
        events: [
          { type: 'workflow.node.completed', nodeName: 'detail_confirmation' },
          { type: 'workflow.node.completed', nodeName: 'build' },
          { type: 'workflow.node.completed', nodeName: 'launch_project' }
        ],
        state: {
          selectedPageId: target.page.pageId,
          detailTargetType: 'page'
        },
        result: {}
      } as WorkflowRunPayload
      return {
        id: now + index,
        role: 'assistant',
        agentPhase: 'development',
        content: '',
        createdAt: now + index,
        detailBlocker: {
          type: 'page',
          pageId: target.page.pageId,
          label: target.page.label,
          path: target.page.path,
          purpose: target.page.purpose
        },
        workflow,
        processSteps: [
          {
            id: `history-detail-${target.page.pageId}`,
            kind: 'workflow',
            status: 'completed',
            title: '完成页面详细设计',
            detail: '已确认页面范围与实现方案。',
            sequence: 1,
            nodeName: 'detail_confirmation'
          },
          {
            id: `history-build-${target.page.pageId}`,
            kind: 'workflow',
            status: 'completed',
            title: '完成页面代码实现',
            detail: '页面源码已通过 Diff 接受。',
            sequence: 2,
            nodeName: 'build'
          },
          {
            id: `history-preview-${target.page.pageId}`,
            kind: 'workflow',
            status: 'completed',
            title: '完成产物审查',
            detail: '页面产物已交付，可在开发产物中查看。',
            sequence: 3,
            nodeName: 'launch_project'
          }
        ]
      }
    })
    missingPages.forEach((target) => {
      developmentHistoryRepairRef.current.add(`${versionViewKey}:${target.artifactId}`)
    })
    const firstPendingCardIndex = messages.findIndex(
      (message) =>
        message.detailBlocker?.type === 'page' &&
        developmentArtifactStatusById[pageArtifactId(message.detailBlocker.pageId)] !== 'completed'
    )
    const insertAt = firstPendingCardIndex >= 0 ? firstPendingCardIndex : messages.length
    const nextMessages = [
      ...messages.slice(0, insertAt),
      ...repairedMessages,
      ...messages.slice(insertAt)
    ]
    setSessionMessages(activeSession.key, nextMessages)
    void persistSession({
      editorMode: activeSession.editorMode,
      messages: nextMessages,
      sessionId: activeSession.sessionId,
      threadId: activeSession.threadId,
      apiContractId: activeSession.apiContractId,
      endpointId: activeSession.endpointId,
      endpointLabel: activeSession.endpointLabel,
      pageId: activeSession.pageId,
      sessionKind: activeSession.sessionKind,
      titleFrom: '应用开发'
    })
  }, [
    activeSession,
    activeSessionId,
    activeWorkbenchPhase,
    developmentArtifactStatusById,
    developmentAutoTargets,
    messages,
    persistSession,
    sessions,
    setSessionMessages,
    versionReadOnly,
    versionViewKey
  ])

  const designDocs = (
    [
      {
        key: 'requirement-spec' as WorkspaceDocKey,
        title: '需求文档',
        path: WORKSPACE_DOC_PATHS.requirementSpec,
        content: buildRequirementSpecDoc(scenario.requirementSpec, application.name)
      },
      {
        key: 'project-plan' as WorkspaceDocKey,
        title: '项目计划',
        path: WORKSPACE_DOC_PATHS.projectPlan,
        content: buildProjectPlanDoc(scenario.projectPlan, application.name)
      }
    ] as Array<{ key: WorkspaceDocKey; title: string; path: string; content: string }>
  ).map((doc) => ({
    ...doc,
    available: stableDesignAvailability[doc.key]
  }))
  const activeDesignDocKey: WorkspaceDocKey | undefined =
    rightPanel?.type === 'doc' ? rightPanel.docKey : undefined
  const conversationPageOption =
    activePageOption ||
    developmentPlanningPages.find((page) => page.pageId === activeSession?.pageId)
  const conversationPageDesign = conversationPageOption
    ? (scenario.pageDesigns as Record<string, PageDesign | undefined>)[
        conversationPageOption.pageId
      ] ||
      (scenario.designedPageDesigns as Record<string, PageDesign | undefined>)[
        conversationPageOption.pageId
      ]
    : undefined
  const conversationRelatedEndpoint = useMemo(
    () =>
      resolvePageRelatedEndpoint(
        conversationPageOption?.pageId,
        pageDesignCatalog,
        developmentPlanningApiContracts
      ),
    [conversationPageOption?.pageId, developmentPlanningApiContracts, pageDesignCatalog]
  )
  /** 从当前会话恢复它关联的接口，即使用户正从页面视角查看同一跨产物任务。 */
  const conversationEndpointOption = useMemo(() => {
    const apiContractId =
      activeSession?.apiContractId ||
      activeApiEndpoint?.apiContractId ||
      conversationRelatedEndpoint?.apiContractId
    const endpointId =
      activeSession?.endpointId ||
      activeApiEndpoint?.endpointId ||
      conversationRelatedEndpoint?.endpointId
    if (!apiContractId || !endpointId) return undefined
    const contract = developmentPlanningApiContracts.find((item) => item.id === apiContractId)
    const endpoint = contract?.endpoints.find(
      (item, index) => (item.id || String(index + 1)) === endpointId
    )
    return contract && endpoint ? { apiContractId, endpointId, contract, endpoint } : undefined
  }, [
    activeApiEndpoint?.apiContractId,
    activeApiEndpoint?.endpointId,
    activeSession?.apiContractId,
    activeSession?.endpointId,
    conversationRelatedEndpoint?.apiContractId,
    conversationRelatedEndpoint?.endpointId,
    developmentPlanningApiContracts
  ])
  const conversationEndpointDesign = conversationEndpointOption
    ? (scenario.endpointDesigns as Record<string, Record<string, unknown> | undefined>)[
        conversationEndpointOption.endpointId
      ]
    : undefined
  // 开发阶段：详细设计生成中（detail_confirmation running，尚未到确认卡），右侧文档区富加载占位。
  // 审查报告就绪：子图（lint/安全/健康度）跑完、发 code_review 结果确认卡，或已 finalize。
  // 未就绪时审查报告 tab 显示占位（启动审查前 / 跑动中），就绪后才填充报告内容。
  const reviewReportClarification = (activeWorkflow?.state?.clarification ??
    activeWorkflow?.result?.clarification) as { mode?: string } | undefined
  const reviewReportReady =
    activeWorkflow?.summary?.phase === 'finalize_project' ||
    (activeWorkflow?.summary?.phase === 'code_review' &&
      reviewReportClarification?.mode !== 'review_start')
  // 已完成审查后查看历史设计或开发对话时，报告状态仍应保持完成，不能被当前会话的工作流覆盖。
  const reviewArtifactReady =
    reviewReportReady ||
    (compareWorkbenchPhases(reachedPhase, 'review') >= 0 &&
      sessions.some(
        (session) => (session.title || '').includes('代码审查') && session.messageCount > 0
      ))
  const hasSavedWorkspaceFile = (path: string): boolean =>
    sessions.some((session) =>
      (session.savedFiles || []).some(
        (file) =>
          (file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)) === path
      )
    )
  const reviewSession = sessions.find(
    (session) => session.sessionKind === 'review' || (session.title || '').includes('代码审查')
  )
  const reviewReportFileSaved = hasSavedWorkspaceFile(appPath(WORKSPACE_DOC_PATHS.codeReview))
  const reviewNeedsCompletionRepair = Boolean(
    activeWorkflow?.summary?.phase === 'code_review' &&
      reviewReportClarification?.mode === 'code_review' &&
      reviewReportFileSaved &&
      !pendingCodeChangeContext
  )
  const reviewCompletionRepairRef = useRef('')
  useEffect(() => {
    if (!reviewNeedsCompletionRepair || !applicationLifecycle) {
      if (!reviewNeedsCompletionRepair) reviewCompletionRepairRef.current = ''
      return
    }
    const repairKey = `${versionViewKey}:${activeWorkflow?.runId || ''}`
    if (!repairKey || reviewCompletionRepairRef.current === repairKey) return
    reviewCompletionRepairRef.current = repairKey
    onApplicationLifecycleChange(completeReviewExecution(applicationLifecycle, application.id))
  }, [
    activeWorkflow?.runId,
    application.id,
    applicationLifecycle,
    onApplicationLifecycleChange,
    reviewNeedsCompletionRepair,
    versionViewKey
  ])
  const reviewArtifactStatus: WorkbenchArtifactStatus = !reviewSession?.messageCount
    ? 'not-started'
    : reviewReportFileSaved && reviewArtifactReady
      ? 'completed'
      : 'in-progress'
  // 一个开发对话可以同时关联页面和接口，右侧分别保留各自的源码与设计文件。
  const pageSource =
    conversationPageOption &&
    isPageCodeDelivered(conversationPageOption.pageId) &&
    conversationPageDesign
      ? buildPageSource(conversationPageDesign, conversationPageOption.pageId || 'page')
      : undefined
  const endpointSource = conversationEndpointDesign
    ? buildEndpointSource(conversationEndpointDesign)
    : undefined
  const [activeArtifactTab, setActiveArtifactTab] = useState<WorkspaceTabKey>('page-source')
  const activeSource =
    activeArtifactTab === 'endpoint-source' ? endpointSource : pageSource || endpointSource
  // 右侧工程文件树只消费已经授权保存的文件；工作流中的 Diff 由 pendingFileDiff 单独覆盖展示。
  // 这避免需求/计划/代码在尚未生成时就以静态骨架的形式“提前存在”。
  const workspaceSourceFiles = useMemo(() => {
    const filesByPath = new Map(workspaceScaffoldFiles.map((file) => [file.path, file]))
    sessions
      .flatMap((session) => session.savedFiles || [])
      .sort((left, right) => left.savedAt - right.savedAt)
      .forEach((file) => {
        const path = file.path.startsWith(`${APPLICATION_ROOT}/`) ? file.path : appPath(file.path)
        filesByPath.set(path, { path, content: file.content })
      })
    return [...filesByPath.values()]
  }, [sessions])
  const savedFileContentByPath = useMemo(
    () => new Map(workspaceSourceFiles.map((file) => [file.path, file.content])),
    [workspaceSourceFiles]
  )
  // 页面任务独立「页面预览」：路由到当前页面 path，与应用预览（应用根）区分。
  const pagePreviewPath = conversationPageOption?.path || activeHeaderTarget.path || '/'
  const pagePreviewUrl = composeVersionPreviewUrl(
    runtimePreviewBaseUrl,
    pagePreviewPath,
    versionViewKey
  )
  // 验收阶段右侧只保留「应用预览」Tab；浏览器与应用文件不提供。
  const acceptancePreviewTab: WorkspaceTab | undefined = displayIsAcceptancePhase
    ? {
        key: 'application-preview',
        label: '应用预览',
        available: Boolean(applicationPreviewUrl),
        icon: 'application'
      }
    : undefined
  // 右侧按阶段收敛：开发使用开发产物，测试使用测试用例，验收只使用应用预览。
  const workspaceTabs: WorkspaceTab[] = [
    ...(displayIsDevelopmentPhase
      ? [
          {
            key: 'development-artifacts' as const,
            label: '开发产物',
            available: true,
            icon: 'artifacts' as const
          }
        ]
      : displayIsTestingPhase
        ? [
            {
              key: 'test-cases' as const,
              label: '测试用例',
              available: true,
              icon: 'test-cases' as const
            }
          ]
        : []),
    ...(!displayIsAcceptancePhase
      ? [{ key: 'project' as const, label: '应用文件', available: true, icon: 'project' as const }]
      : []),
    ...(acceptancePreviewTab ? [acceptancePreviewTab] : [])
  ]
  // 当前激活的工作区 tab：预览面板 → 浏览器；文档/源码面板统一归入“应用文件”。
  const activeWorkspaceTab: WorkspaceTabKey =
    rightPanel?.type === 'development-artifacts'
      ? 'development-artifacts'
      : rightPanel?.type === 'test-cases'
        ? 'test-cases'
        : rightPanel?.type === 'preview'
          ? rightPanel.requestKey?.endsWith(':page')
            ? ('page-preview' as WorkspaceTabKey)
            : displayIsAcceptancePhase
              ? rightPanel.requestKey?.endsWith(':acceptance')
                ? 'application-preview'
                : 'preview'
              : 'preview'
          : 'project'
  const isAcceptancePreviewTab =
    displayIsAcceptancePhase && activeWorkspaceTab === 'application-preview'
  const openWorkspaceTab = (key: WorkspaceTabKey): void => {
    // 手动切换 Tab 后不再自动覆盖用户当前查看内容，面板尺寸始终由三档控制器决定。
    autoOpenStateRef.current.type = null
    autoOpenStateRef.current.dismissed = true
    if (key === 'page-preview' && pagePreviewUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:page`,
        url: pagePreviewUrl
      })
    } else if (key === 'application-preview' && applicationPreviewUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:acceptance`,
        url: applicationPreviewUrl
      })
    } else if (key === 'preview' && previewTabUrl) {
      setRightPanel({
        type: 'preview',
        requestKey: `${versionViewKey}:${runtimePreviewBaseUrl}:application`,
        url: previewTabUrl
      })
    } else if (key === 'project') {
      // 应用文件页签：右侧展示应用目录（文档+代码），并定位到当前产物对应的文件。
      setRightPanel({ type: activeSource ? 'source' : 'doc' })
    } else if (key === 'development-artifacts') {
      setRightPanel({ type: 'development-artifacts' })
    } else if (key === 'test-cases') {
      setRightPanel({ type: 'test-cases' })
    }
  }
  /** 在开发清单中切换当前目标；主对话不切换，后续发送内容才会推动对应 Workflow。 */
  const handleSelectDevelopmentArtifact = (item: DevelopmentArtifactItem): void => {
    if (item.kind === 'page') {
      const page = developmentPlanningPages.find(
        (candidate) => pageArtifactId(candidate.pageId) === item.id
      )
      if (page) setActiveDetailTarget({ type: 'page', pageId: page.pageId })
      return
    }
    for (const contract of developmentPlanningApiContracts) {
      for (const [endpointIndex, endpoint] of contract.endpoints.entries()) {
        const apiContractId = endpoint.apiContractId || contract.id
        const endpointId = endpoint.id || String(endpointIndex + 1)
        if (endpointArtifactId(apiContractId, endpointId) !== item.id) continue
        setActiveDetailTarget({
          type: 'endpoint',
          apiContractId,
          endpointId,
          endpointKey: `${apiContractId}:${endpointId}`,
          label: item.label
        })
        return
      }
    }
  }
  const activeDevelopmentArtifactId =
    activeDetailTarget.type === 'page'
      ? pageArtifactId(activeDetailTarget.pageId)
      : activeDetailTarget.type === 'endpoint'
        ? endpointArtifactId(activeDetailTarget.apiContractId, activeDetailTarget.endpointId)
        : undefined

  // 同步任务的「启动产物审查」节点落定后，右侧工作区切换到「开发产物」并定位当前产物：
  // 页面打开页面预览、接口打开接口调试。每次审查目标只自动切换一次（按 run+产物 记账），
  // 避免流式期间的重复 emit 反复抢面板，也不覆盖用户其后的手动切换。
  const reviewPanelSwitchedRef = useRef('')
  useEffect(() => {
    if (activeWorkbenchPhase !== 'development') return
    const reviewTarget = workflowReviewTarget(activeWorkflow)
    if (!reviewTarget) return
    const targetKey =
      reviewTarget.type === 'page'
        ? pageArtifactId(reviewTarget.pageId)
        : endpointArtifactId(reviewTarget.apiContractId, reviewTarget.endpointId)
    const switchKey = `${activeWorkflow?.threadId}:${activeWorkflow?.runId}:${targetKey}`
    if (reviewPanelSwitchedRef.current === switchKey) return
    reviewPanelSwitchedRef.current = switchKey
    const item = developmentArtifactItems.find((candidate) => candidate.id === targetKey)
    if (item) handleSelectDevelopmentArtifact(item)
    openWorkspaceTab('development-artifacts')
    // 两个 handler 每次渲染都是新引用，触发时机完全由审查目标（activeWorkflow）驱动。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeWorkbenchPhase, activeWorkflow, developmentArtifactItems])

  // 待继续任务的「继续处理」入口：在开发主对话恢复产物审查与确认工作流。
  // 右侧先切到开发产物打开预览/调试视图，验收工作流承载确认动作，确认后任务转已完成。
  const handledBackgroundTaskAcceptRef = useRef(0)
  // 验收进行中的任务集合：任一入口触发后，其它入口在该任务上禁用，避免重复发起验收。
  const acceptanceInFlightRef = useRef<Record<string, true>>({})
  /** 解除某条任务的验收入口锁定，并同步解除工作台页入口条目的禁用态。 */
  const releaseAcceptanceInFlight = (taskId: string): void => {
    if (!acceptanceInFlightRef.current[taskId]) return
    delete acceptanceInFlightRef.current[taskId]
    onBackgroundTaskAcceptanceSettled?.(taskId)
  }
  /** 启动某条待验收任务的验收工作流；同一任务进行中时其它入口重复触发会被忽略。 */
  const startArtifactAcceptance = async (taskId: string): Promise<void> => {
    const task = getBackgroundTasks().find((candidate) => candidate.id === taskId)
    // 后续步骤入口只对「已完成且验收未执行」的实现任务开放。
    if (!task || !task.nextStep || task.nextStep.done) return
    if (acceptanceInFlightRef.current[taskId]) return
    // 会话正在执行时发送会被静默拒绝，此时不能加锁，否则入口会被永久禁用。
    if (loading) {
      setPreviewError('当前有工作流正在执行，请等待完成后再继续处理。')
      return
    }
    acceptanceInFlightRef.current[taskId] = true
    try {
      // 先定位产物并切到「开发产物」工作区，让预览先就绪。
      const item = developmentArtifactItems.find(
        (candidate) => candidate.id === task.primaryArtifactId
      )
      if (item) handleSelectDevelopmentArtifact(item)
      openWorkspaceTab('development-artifacts')
      setActiveView('chat')
      setViewingTaskPhase('development')
      const target = task.execTarget
      await handleSend(undefined, `验收：${task.title}`, {
        sessionIdentity: await ensureDevelopmentSession(),
        suppressUserMessage: true,
        // 显式产物目标必须三维齐全：页面验收清空接口维度、接口验收清空页面维度，
        // 防止右侧面板残留的其它产物选中把验收路由到错误目标。
        ...(target?.type === 'endpoint'
          ? {
              selectedPageId: '',
              selectedApiContractId: target.apiContractId,
              selectedEndpointId: target.endpointId
            }
          : target?.type === 'page'
            ? { selectedPageId: target.pageId, selectedApiContractId: '', selectedEndpointId: '' }
            : {}),
        workflowScope: 'artifact_acceptance'
      })
    } catch (caughtError) {
      // 发送失败必须立即解锁，否则该任务的验收入口会被永久禁用。
      releaseAcceptanceInFlight(taskId)
      setPreviewError(
        caughtError instanceof Error ? caughtError.message : '继续工作流启动失败，请重试。'
      )
      return
    }
    // 发送成功后工作流停在「等待确认验收」挂起态：保持入口禁用直到用户确认完成，
    // 确认落定（nextStep.done 置位）由下方监听 effect 解锁。
  }
  // 验收确认完成后及时解除对应任务的入口锁定；发送途中任务已落定的边角场景同样覆盖。
  useEffect(() => {
    for (const taskId of Object.keys(acceptanceInFlightRef.current)) {
      const done = allBackgroundTasks.find((task) => task.id === taskId)?.nextStep?.done
      if (done) releaseAcceptanceInFlight(taskId)
    }
    // releaseAcceptanceInFlight 每次渲染都是新引用；解锁时机完全由任务流水变化驱动。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [allBackgroundTasks])
  useEffect(() => {
    if (
      !backgroundTaskAcceptRequest ||
      !backgroundTaskAcceptRequest.taskId ||
      backgroundTaskAcceptRequest.nonce <= handledBackgroundTaskAcceptRef.current
    ) {
      return
    }
    handledBackgroundTaskAcceptRef.current = backgroundTaskAcceptRequest.nonce
    void startArtifactAcceptance(backgroundTaskAcceptRequest.taskId)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [backgroundTaskAcceptRequest])
  const artifactCatalog: WorkbenchArtifact[] = [
    ...designDocs.map((doc) => ({
      id: documentArtifactId(doc.key),
      name: doc.title,
      path: doc.path,
      phase: doc.key === 'project-plan' ? ('planning' as const) : ('analysis' as const),
      status: stableDesignStatuses[doc.key],
      type: 'document' as const,
      available: doc.available
    })),
    ...(developmentPlanningReady && developmentCatalogUnlocked
      ? developmentPlanningPages.map((page) => ({
          id: pageArtifactId(page.pageId),
          name: page.label,
          path: page.path,
          phase: 'development' as const,
          status: developmentArtifactStatusById[pageArtifactId(page.pageId)] || 'not-started',
          type: 'page' as const,
          available: true
        }))
      : []),
    ...(developmentPlanningReady && developmentCatalogUnlocked
      ? developmentPlanningApiContracts.flatMap((contract) =>
          contract.endpoints.map((endpoint, endpointIndex) => {
            const apiContractId = endpoint.apiContractId || contract.id
            const endpointId = endpoint.id || String(endpointIndex + 1)
            return {
              id: endpointArtifactId(apiContractId, endpointId),
              name: `${endpoint.method} ${endpoint.path}`,
              path: endpoint.path,
              phase: 'development' as const,
              status:
                developmentArtifactStatusById[endpointArtifactId(apiContractId, endpointId)] ||
                'not-started',
              type: 'endpoint' as const,
              available: true
            }
          })
        )
      : []),
    ...(developmentPlanningReady && developmentCatalogUnlocked
      ? developmentPlanningEntities.map((entity) => ({
          id: entityArtifactId(entity.entityId),
          name: entity.label,
          path: entity.schemaRef || `entities/${entity.entityId}`,
          phase: 'development' as const,
          status: 'not-started' as const,
          type: 'entity' as const,
          available: true
        }))
      : []),
    {
      id: documentArtifactId('code-review'),
      name: '审查报告',
      path: WORKSPACE_DOC_PATHS.codeReview,
      phase: 'review',
      status: reviewArtifactStatus,
      type: 'document',
      available: compareWorkbenchPhases(reachedPhase, 'review') >= 0
    }
  ]
  const artifactAccessById = Object.fromEntries(
    artifactCatalog.map((artifact) => [
      artifact.id,
      resolveArtifactAccess({
        artifact,
        currentPhase: activeWorkbenchPhase,
        reachedPhase,
        versionLocked: versionReadOnly
      })
    ])
  ) as Record<string, WorkbenchArtifactAccess>
  // 页面目录刷新时保留当前页面上下文；仅在清单稳定且当前页面失效时回退。
  useEffect(() => {
    if (activeApiEndpoint) return
    setActiveDetailTarget((currentTarget) => {
      if (currentTarget.type === 'endpoint') return currentTarget
      if (currentTarget.type === 'none') return currentTarget
      const currentPageId = currentTarget.pageId
      if (developmentPlanningPages.length === 0) return currentTarget
      if (developmentPlanningPages.some((page) => page.pageId === currentPageId)) {
        return currentTarget
      }
      const fallbackPageId =
        developmentPlanningPages.find((page) => page.designed)?.pageId ||
        developmentPlanningPages[0]?.pageId ||
        ''
      return fallbackPageId ? { type: 'page', pageId: fallbackPageId } : { type: 'none' }
    })
  }, [activeApiEndpoint, developmentPlanningPages])

  // 打开历史页面或接口会话时同步目标上下文，避免标题与消息归属不一致。
  useEffect(() => {
    const session = sessions.find((item) => item.id === activeSessionId)
    if (!session) return
    if (session.sessionKind === 'development' && session.title === '应用开发') {
      // 开发主会话不绑定单一页面；保留当前目录/预览明确选择的目标。
      return
    }
    const requestedTarget = activeDetailTargetRef.current
    if (requestedTarget.type === 'page' && requestedTarget.pageId === session.pageId) return
    if (
      requestedTarget.type === 'endpoint' &&
      requestedTarget.apiContractId === session.apiContractId &&
      requestedTarget.endpointId === session.endpointId
    ) {
      return
    }
    if (session?.apiContractId && session.endpointId) {
      setActiveDetailTarget({
        type: 'endpoint',
        apiContractId: session.apiContractId,
        endpointId: session.endpointId,
        endpointKey: `${session.apiContractId}:${session.endpointId}`,
        label: session.endpointLabel || session.title
      })
      return
    }
    const sessionPageId = session?.pageId
    if (!sessionPageId) {
      // 开发主对话不绑定某个页面；保留用户从开发产物目录显式选择的预览目标。
      if (session.sessionKind !== 'development') {
        setActiveDetailTarget({ type: 'none' })
      }
      return
    }
    const resolvedPageId = resolvePlanningPageId(developmentPlanningPages, sessionPageId)
    if (resolvedPageId) {
      setActiveDetailTarget({ type: 'page', pageId: resolvedPageId })
    }
  }, [activeSessionId, developmentPlanningPages, sessions])

  const handleShowSkills = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('skills')
  }

  const handleShowFiles = (): void => {
    setPreviewError('')
    setRightPanel(undefined)
    setActiveView('files')
  }

  const handleShowSettings = (): void => {
    setPreviewError('')
    // 应用配置只是覆盖中间工作区的临时抽屉，不改变用户对右侧产物面板的开关选择。
    setActiveView('settings')
  }

  /**
   * 判断产物当前是否允许再次发起新的实施 Workflow：完成、进行中、已被后台任务接管均不可发起，
   * 仅未开始与失败（可重试）产物开放实施；返回占用状态与对应的禁用原因。
   */
  const resolveArtifactOccupancy = (
    artifactId: string
  ): { blocked: boolean; reason: string } => {
    const status = developmentArtifactStatusById[artifactId]
    if (status === 'completed') {
      return { blocked: true, reason: '该产物已完成交付，不可重复发起' }
    }
    if (status === 'in-progress') {
      return { blocked: true, reason: '该产物正在实施中，请先完成当前工作流' }
    }
    if (['impl-queued', 'implementing', 'awaiting-review'].includes(status)) {
      return { blocked: true, reason: '该产物已由后台任务接管，请在任务中心跟进' }
    }
    return { blocked: false, reason: '' }
  }

  /** 推导产物的展示状态：已交付 / 实施中 / 后台任务接管 / 待实施，供产物面板渲染状态徽标。 */
  const resolveArtifactState = (artifactId: string, delivered: boolean): ComposerArtifactTarget['state'] => {
    const status = developmentArtifactStatusById[artifactId]
    if (delivered || status === 'completed') return 'delivered'
    if (status === 'in-progress') return 'in-progress'
    if (['impl-queued', 'implementing', 'awaiting-review'].includes(status)) return 'queued'
    return 'ready'
  }

  // 待继续工作流按产物归位：与产物状态推导同规则（同一产物以最新任务为准），
  // 最新任务已完成但留有未执行后续步骤时，产物行自身直接呈现为「继续处理」入口，
  // 不再在产物弹层上方单独摆放一张待继续任务列表。
  const pendingContinuationByArtifact = new Map<string, { taskId: string; title: string }>()
  versionBackgroundTasks
    .filter((task) => task.kind === 'artifact_implementation')
    .slice()
    .sort((left, right) => left.updatedAt - right.updatedAt)
    .forEach((task) => {
      const pending = Boolean(task.nextStep && !task.nextStep.done)
      task.artifactIds.forEach((artifactId) => {
        if (pending) {
          pendingContinuationByArtifact.set(artifactId, { taskId: task.id, title: task.title })
        } else {
          pendingContinuationByArtifact.delete(artifactId)
        }
      })
    })

  /** 给产物候选合并待继续信息：命中任务的产物行变成「继续处理」入口，点击恢复该工作流而非发起新实施。 */
  const withArtifactContinuation = (item: ComposerArtifactTarget): ComposerArtifactTarget => {
    const continuation = pendingContinuationByArtifact.get(item.artifactId)
    if (!continuation) return item
    return {
      ...item,
      state: 'continue',
      disabled: false,
      disabledReason: '',
      continuationTaskId: continuation.taskId
    }
  }

  /**
   * 产物面板候选：页面与接口；已完成、进行中或已被后台任务接管的产物不可再次发起，
   * 但工作流已完成而留有后续步骤的产物会改写为「继续处理」入口（见 withArtifactContinuation）。
   */
  const composerMentionItems: ComposerArtifactTarget[] = [
    ...developmentPlanningPages.map((page) => {
      const artifactId = pageArtifactId(page.pageId)
      const occupancy = resolveArtifactOccupancy(artifactId)
      const delivered = isPageCodeDelivered(page.pageId)
      const blocked = occupancy.blocked || delivered
      return {
        artifactId,
        kind: 'page' as const,
        label: page.label,
        hint: page.path,
        pageId: page.pageId,
        state: resolveArtifactState(artifactId, delivered),
        disabled: blocked,
        disabledReason: delivered
          ? '该页面已交付'
          : occupancy.reason || '该页面不可重复发起'
      }
    }),
    ...developmentPlanningApiContracts.flatMap((contract) =>
      contract.endpoints.map((endpoint, endpointIndex) => {
        const apiContractId = endpoint.apiContractId || contract.id
        const endpointId = endpoint.id || String(endpointIndex + 1)
        const artifactId = endpointArtifactId(apiContractId, endpointId)
        const occupancy = resolveArtifactOccupancy(artifactId)
        const delivered = isEndpointCodeDelivered(endpointId)
        const blocked = occupancy.blocked || delivered
        return {
          artifactId,
          kind: 'endpoint' as const,
          label: `${endpoint.method} ${endpoint.path}`,
          hint: contract.label,
          apiContractId,
          endpointId,
          state: resolveArtifactState(artifactId, delivered),
          disabled: blocked,
          disabledReason: delivered
            ? '该接口已交付'
            : occupancy.reason || '该接口不可重复发起'
        }
      })
    )
  ].map(withArtifactContinuation)

  /** 把产物面板候选还原为规划清单中的模板卡目标；产物已被上游计划移除时返回 undefined。 */
  const buildTemplateTargetFromMention = (
    mention: ComposerArtifactTarget
  ): DevelopmentTemplateTarget | undefined => {
    if (mention.kind === 'page') {
      const page = developmentPlanningPages.find((candidate) => candidate.pageId === mention.pageId)
      return page ? { kind: 'page', artifactId: mention.artifactId, page } : undefined
    }
    for (const contract of developmentPlanningApiContracts) {
      for (const [endpointIndex, endpoint] of contract.endpoints.entries()) {
        const apiContractId = endpoint.apiContractId || contract.id
        const endpointId = endpoint.id || String(endpointIndex + 1)
        if (apiContractId !== mention.apiContractId || endpointId !== mention.endpointId) continue
        return {
          kind: 'endpoint',
          artifactId: mention.artifactId,
          apiContractId,
          endpointId,
          label: `${endpoint.method} ${endpoint.path}`,
          path: endpoint.path,
          summary: endpoint.summary
        }
      }
    }
    return undefined
  }

  /**
   * 在指定开发对话投放产物模板选择卡，作为该产物开发 Workflow 的正式起点。
   * 开发阶段不再自动投放：仅由用户从「产物」面板选择后触发；同产物已有待确认卡片时跳过重复投放，
   * 上一次尝试已启动 Workflow 时允许重新发起，并可选携带触发本次发起的用户消息。
   */
  const presentDevelopmentTemplateSelector = async (
    target: DevelopmentTemplateTarget,
    options?: { identity?: SessionIdentity; userMessageText?: string }
  ): Promise<void> => {
    const identity = options?.identity || (await ensureDevelopmentSession())
    const targetKey =
      target.kind === 'page'
        ? pageDetailTargetKey(target.page.pageId)
        : endpointDetailTargetKey(target.apiContractId, target.endpointId)
    const now = Date.now()
    let nextMessages: AgentChatMessage[] = []
    let appended = false
    // 用函数式更新读取 store 的最新数组，避免并发送起时用旧快照覆盖上一段 Workflow 历史。
    setSessionMessages(identity.key, (currentMessages) => {
      const workflowStarted = currentMessages.some(
        (message) => message.workflow && workflowDetailTargetKey(message.workflow) === targetKey
      )
      const hasPendingCard = currentMessages.some(
        (message) => detailBlockerTargetKey(message.detailBlocker) === targetKey
      )
      const appendedMessages: AgentChatMessage[] = []
      if (options?.userMessageText) {
        appendedMessages.push({
          id: now,
          role: 'user',
          content: options.userMessageText,
          createdAt: now
        })
      }
      if (!hasPendingCard || workflowStarted) {
        appendedMessages.push(
          target.kind === 'page'
            ? {
                id: now + 1,
                role: 'assistant',
                agentPhase: 'development',
                // 模板选择卡自身已经包含完整引导，不重复追加一段普通正文。
                content: '',
                createdAt: now + 1,
                detailBlocker: {
                  type: 'page',
                  pageId: target.page.pageId,
                  label: target.page.label,
                  path: target.page.path,
                  purpose: target.page.purpose
                },
                // 模板选择是开发 Workflow 的第一个可交互节点，后续设计与实现继续复用该消息。
                processSteps: [
                  {
                    id: `detail-template-${pageDetailTargetKey(target.page.pageId)}`,
                    kind: 'workflow',
                    status: 'requires_user_input',
                    title: '选择页面模板',
                    detail: '请选择页面模板后开始详细设计。',
                    sequence: 1,
                    nodeName: 'detail_confirmation'
                  } satisfies ProcessStepRecord
                ]
              }
            : {
                id: now + 1,
                role: 'assistant',
                agentPhase: 'development',
                content: '',
                createdAt: now + 1,
                detailBlocker: {
                  type: 'endpoint',
                  apiContractId: target.apiContractId,
                  endpointId: target.endpointId,
                  label: target.label,
                  path: target.path,
                  purpose: target.summary
                },
                processSteps: [
                  {
                    id: `detail-template-${endpointDetailTargetKey(target.apiContractId, target.endpointId)}`,
                    kind: 'workflow',
                    status: 'requires_user_input',
                    title: '确认接口详细设计',
                    detail: '请确认接口详细设计后开始实现。',
                    sequence: 1,
                    nodeName: 'detail_confirmation'
                  } satisfies ProcessStepRecord
                ]
              }
        )
      }
      if (appendedMessages.length === 0) {
        nextMessages = currentMessages
        return currentMessages
      }
      nextMessages = [...currentMessages, ...appendedMessages]
      appended = true
      return nextMessages
    })
    if (!appended) return
    await persistSession({
      editorMode: identity.editorMode,
      messages: nextMessages,
      sessionId: identity.sessionId,
      threadId: identity.threadId,
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId,
      endpointLabel: identity.endpointLabel,
      pageId: identity.pageId,
      sessionKind: identity.sessionKind
    })
  }

  /**
   * 向开发对话追加引导消息；携带 userText 时先记录用户输入再回复 Agent 引导，
   * 不调用 Workflow 剧本——无 @ 目标的普通消息不应推动工作流。
   */
  const appendDevelopmentGuide = async (
    identity: SessionIdentity,
    content: string,
    userText?: string
  ): Promise<void> => {
    const now = Date.now()
    let nextMessages: AgentChatMessage[] = []
    setSessionMessages(identity.key, (currentMessages) => {
      const appendedMessages: AgentChatMessage[] = []
      if (userText) {
        appendedMessages.push({ id: now, role: 'user', content: userText, createdAt: now })
      }
      appendedMessages.push({
        id: now + 1,
        role: 'assistant',
        agentPhase: 'development',
        content,
        guideAction: 'artifact-launch',
        createdAt: now + 1
      })
      nextMessages = [...currentMessages, ...appendedMessages]
      return nextMessages
    })
    await persistSession({
      editorMode: identity.editorMode,
      messages: nextMessages,
      sessionId: identity.sessionId,
      threadId: identity.threadId,
      apiContractId: identity.apiContractId,
      endpointId: identity.endpointId,
      endpointLabel: identity.endpointLabel,
      pageId: identity.pageId,
      sessionKind: identity.sessionKind
    })
  }

  // 空开发对话首次进入时由研发 Agent 投放一次「产物」发起引导；已投放的会话不再重复。
  // 只在当前可编辑的对话里投放：只读对话的「产物」按钮不可用，引导应等到取得权限后出现。
  const developmentGuideDroppedRef = useRef(new Set<string>())
  /**
   * 首轮操作后的任务自动命名：仅当任务还是默认名「新任务」时生效，
   * 用户重命名过或阶段默认命名的任务不覆盖。开发任务按所选产物命名，
   * 其它阶段按该阶段的产物职责命名。
   */
  const autoRenameTaskAfterFirstRound = async (
    identity: SessionIdentity | undefined,
    title: string
  ): Promise<void> => {
    if (!identity) return
    const summary = sessions.find((session) => session.id === identity.sessionId)
    if (!summary || summary.title !== '新任务') return
    await handleRenameSession(identity.sessionId, title)
  }
  /** 按工作流阶段返回首轮自动命名标题；无对应职责的阶段返回 undefined 跳过。 */
  const firstRoundTitleForPhase = (phase: string): string | undefined => {
    const titles: Record<string, string> = {
      requirements: '需求文档梳理',
      project_planning: '项目计划编制',
      development: '产物实施推进',
      application_test: '应用测试执行',
      business_test: '业务用例执行',
      code_review: '代码审查报告',
      acceptance: '应用验收确认'
    }
    return titles[phase]
  }

  useEffect(() => {
    if (activeWorkbenchPhase !== 'development') return
    if (!activeSession || activeSession.sessionKind !== 'development') return
    if (messages.length > 0 || loadingSessions || stageSessionSwitching) return
    if (workspaceBusy || loading || versionReadOnly) return
    if (developmentGuideDroppedRef.current.has(activeSession.key)) return
    developmentGuideDroppedRef.current.add(activeSession.key)
    void appendDevelopmentGuide(activeSession, DEVELOPMENT_GUIDE_TEXT)
  }, [
    activeSession,
    activeWorkbenchPhase,
    loading,
    loadingSessions,
    messages.length,
    stageSessionSwitching,
    versionReadOnly,
    workspaceBusy
  ])

  /** 用户从产物面板选定某个产物后发起实施：右侧定位产物，并在当前开发对话投放模板选择卡（不产生用户消息）。 */
  const startArtifactWorkflow = async (
    target: DevelopmentTemplateTarget
  ): Promise<void> => {
    const identity = activeSession
    if (!identity) return
    setActiveView('chat')
    setViewingTaskPhase('development')
    setGeneratingDetailTargetKey('')
    const endpointKey =
      target.kind === 'endpoint' ? `${target.apiContractId}:${target.endpointId}` : ''
    // 右侧目录与预览同步定位到发起的产物，保持“选择即聚焦”。
    setActiveArtifactTab('page-source')
    setInteractingDetailTargetKey(
      target.kind === 'page' ? pageDetailTargetKey(target.page.pageId) : endpointKey
    )
    if (target.kind === 'page') {
      setActiveDetailTarget({ type: 'page', pageId: target.page.pageId })
    } else {
      setActiveDetailTarget({
        type: 'endpoint',
        apiContractId: target.apiContractId,
        endpointId: target.endpointId,
        endpointKey,
        label: target.label
      })
    }
    // 首轮产物发起即完成自动命名：开发任务按所选产物命名（仅对默认名「新任务」生效）。
    const artifactLabel = target.kind === 'page' ? target.page.label : target.label
    void autoRenameTaskAfterFirstRound(identity, `实施「${artifactLabel}」`)
    await presentDevelopmentTemplateSelector(target, { identity })
  }

  /**
   * 产物面板的直接发起入口：校验占用与对话状态后投放实施启动卡。
   * 全程不产生用户消息输入——启动卡即该产物实施 Workflow 的正式起点。
   */
  const handleLaunchArtifact = async (mention: ComposerArtifactTarget): Promise<void> => {
    // 已有等待确认的节点时不并发起新产物，避免同对话内状态机互相踩踏。
    if (activeWorkflow?.summary?.status === 'requires_user_input') {
      setPreviewError('当前有等待确认的工作流节点，请先完成确认，再发起新产物。')
      return
    }
    if (mention.disabled) {
      setPreviewError(`「${mention.label}」${mention.disabledReason}，请在任务中心或验收入口继续。`)
      return
    }
    const templateTarget = buildTemplateTargetFromMention(mention)
    if (!templateTarget) {
      setPreviewError('未在当前规划中找到该产物，请重新选择。')
      return
    }
    // 会话未就绪（阶段切换窗口）时发起会被静默拒绝，此时提示用户重试。
    if (!activeSession) {
      setPreviewError('开发对话尚未就绪，请稍后重试。')
      return
    }
    try {
      await startArtifactWorkflow(templateTarget)
    } catch (caughtError) {
      setPreviewError(
        caughtError instanceof Error ? caughtError.message : '产物发起失败，请重试。'
      )
    }
  }

  /** 开发阶段输入框发送入口：普通消息不推动工作流，引导用户通过「产物」按钮发起实施。 */
  const handleComposerSend = async (
    workflowDebug?: WorkflowDebugOptions,
    selectedFilePaths?: string[]
  ): Promise<void> => {
    if (!displayIsDevelopmentPhase || workflowDebug) {
      await handleSend(workflowDebug, undefined, { selectedFilePaths })
      return
    }
    const userMessageText = draft.trim()
    if (activeWorkflow?.summary?.status !== 'requires_user_input') {
      // 没有等待确认的 Workflow 时普通文本不进入剧本，记录输入并回复产物发起引导。
      setDraftByKey(draftKey, '')
      if (activeSession) {
        await appendDevelopmentGuide(activeSession, DEVELOPMENT_GUIDE_TEXT, userMessageText)
      }
      return
    }
    await handleSend(workflowDebug, undefined, { selectedFilePaths })
  }

        // 工作台页打开任务管理抽屉时只列出当前阶段常规任务，跨阶段历史绝不混入。
  const getConversationManagementContent = useCallback((): ConversationManagementContent => {
    const phaseConversations = sessions
      .filter((session) => session.sessionKind === renderedTaskPhase)
      .sort((left, right) => left.createdAt - right.createdAt)
      .map((session) => ({
        id: session.id,
        title: session.title,
        messageCount: session.messageCount,
        updatedAt: session.updatedAt,
        // 阶段默认任务由系统创建（createdByUser=false），抽屉中不提供删除入口。
        deletable: Boolean(session.createdByUser)
      }))
    // 阶段会话刚创建、目录尚未异步回填时，先以当前运行时会话补位，避免对话门禁短暂显示为空。
    const currentPhaseConversations =
      phaseConversations.length > 0 || activeSession?.sessionKind !== renderedTaskPhase
        ? phaseConversations
        : [
            {
              id: activeSession.sessionId,
              title:
                renderedTaskPhase === 'analysis'
                  ? '需求分析'
                  : renderedTaskPhase === 'planning'
                    ? '项目计划'
                    : renderedTaskPhase === 'development'
                      ? '应用开发'
                      : renderedTaskPhase === 'testing'
                        ? '应用测试'
                        : renderedTaskPhase === 'review'
                          ? '代码审查'
                          : '应用验收',
              messageCount: messages.length,
              updatedAt: Date.now()
            }
          ]
    const authorizedSessionWorkflow = authorizedEditingSessionId
      ? latestMessageWorkflow(
          getSessionMessages(
            sessionRuntimeKey(application.workspaceRoot || '', editorMode, authorizedEditingSessionId)
          )
        )
      : undefined
    const authorizedSessionRunStatus = authorizedEditingSessionId
      ? sessionRunStates[authorizedEditingSessionId]
      : undefined
    // 收尾窗口修正：暂停卡片（requires_user_input）已随消息落地、而运行态仍短暂停留在
    // running 的几秒内，以最新工作流载荷为准按待确认处理，避免门禁误锁“新建任务”。
    const effectiveRunStatus =
      authorizedSessionRunStatus === 'running' &&
      authorizedSessionWorkflow?.summary?.status === 'requires_user_input'
        ? 'awaiting_user'
        : authorizedSessionRunStatus
    const creationBlocked = sessionRunBlocksConversationCreation(
      effectiveRunStatus,
      authorizedSessionWorkflow
    )
    return {
      activeSessionId,
      editingSessionId,
      conversations: currentPhaseConversations.map((conversation) => ({
        ...conversation,
        active: conversation.id === activeSessionId,
        runStatus: sessionRunStates[conversation.id]
      })),
      onSelectSession: (sessionId) => {
        // 不按捕获帧的 activeSessionId 做同会话短路：抽屉内容可能来自旧渲染帧，
        // 守卫会误判“点的是当前会话”而吞掉切换。重复打开当前会话本身无害，
        // 始终幂等地交给打开函数；打开只改变查看对象，不隐式转移推进权。
        void handleOpenChatSession(sessionId)
      },
      onDeleteSession: (sessionId) => {
        // 删除仅对用户自建任务开放（抽屉层已二次确认）；默认任务不渲染删除入口。
        void handleDeleteSession(sessionId).catch(() => undefined)
      },
      createConversationDisabledReason:
        creationBlocked ? '请先完成当前推进任务中的事项' : undefined,
      onCreateConversation:
        renderedTaskPhase === activeWorkbenchPhase && !versionReadOnly
          ? () => {
              // “新建”本身就是明确的工作意图：创建成功后直接把当前阶段推进权交给新对话。
              void handleCreateSessionFromList(activeWorkbenchPhase)
                .then((identity) => setAuthorizedEditingSessionId(identity.sessionId))
                .catch(() => undefined)
            }
          : undefined
    }
    // 切换/新建动作每次渲染都是新引用；快照在抽屉打开时按需查询，无需随渲染重注册。
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeSessionId,
    activeSession?.sessionId,
    activeSession?.sessionKind,
    activeWorkbenchPhase,
    application.workspaceRoot,
    authorizedEditingSessionId,
    editorMode,
    editingSessionId,
    getSessionMessages,
    handleCreateSessionFromList,
    handleDeleteSession,
    handleOpenSession,
    loadingSessions,
    messages.length,
    renderedTaskPhase,
    sessionRunStates,
    sessions,
    versionReadOnly
  ])

  useEffect(() => {
    // 每次渲染后刷新注册：工作台页持有最新闭包，打开任务管理抽屉时查询到的是当帧数据。
    onConversationManagementReady?.(getConversationManagementContent)
  })
  /** 用户确认后先推进测试阶段，再由测试 Agent 创建应用级测试对话。 */
  const handleConfirmDevelopmentComplete = (): void => {
    setDevelopmentCompleteModalOpen(false)
    // 确认进入测试后清除此前手动查看开发阶段的覆盖，顶部和左侧统一跟随测试执行阶段。
    switchPhase(null)
    if (applicationLifecycle) {
      onApplicationLifecycleChange(
        beginTestingExecution(applicationLifecycle, application.id, testCasePreparation.total)
      )
    }
    setTestingTransitionRequested(true)
  }

  /** 用户确认测试结果后才推进审查阶段，并由审查 Agent 创建应用级会话。 */
  const handleConfirmTestingComplete = (): void => {
    setReviewCompleteModalOpen(false)
    // 确认进入审查后清除测试阶段历史查看覆盖，避免顶部阶段条停留在上游阶段。
    switchPhase(null)
    // 同步消费测试自动开启标志：审查开启的同一渲染帧内 autoStartTesting 必须已复位，
    // 否则“开始代码审查”会在标志互斥生效前被路由进测试会话并重放测试剧本。
    setTestingTransitionRequested(false)
    setReviewTransitionRequested(true)
    if (applicationLifecycle) {
      onApplicationLifecycleChange(beginReviewExecution(applicationLifecycle, application.id))
    }
  }

  /** 用户在应用预览内确认验收通过后写入当前版本生命周期，生成版本门禁随之开放。 */
  const handleAcceptApplication = (): void => {
    if (!applicationLifecycle || versionReadOnly) return
    onApplicationLifecycleChange(completeAcceptanceExecution(applicationLifecycle, application.id))
  }

  /** 点击不通过后切回验收对话，由产品 Agent 提示用户输入验收意见。 */
  const handleSubmitAcceptanceFeedback = (): void => {
    if (versionReadOnly) return
    setActiveView('chat')
    setRightPanelLayout('split')
    // 先确保验收默认会话已经成为当前会话，再发起一次不带用户正文的 Agent 提示。
    void createAcceptanceSession()
      .then((identity) =>
        handleSend(undefined, '不通过验收', {
          sessionIdentity: identity,
          suppressUserMessage: true
        })
      )
      .catch(() => undefined)
  }

  // 阶段只负责初始化默认会话或恢复该阶段最近会话；消息发送后不再由阶段逻辑抢占当前会话。
  const phaseSwitchHandledRef = useRef<WorkbenchPhase | ''>('')
  useEffect(() => {
    // 必须先完成会话目录加载，随后由阶段规则明确决定要打开哪一条会话。
    if (loadingSessions) return
    if (phaseSwitchHandledRef.current === activeWorkbenchPhase) return
    phaseSwitchHandledRef.current = activeWorkbenchPhase

    if (activeWorkbenchPhase === 'development') {
      // 开发阶段始终进入唯一主对话；页面/API 只作为 Workflow 目标，不再决定会话身份。
      setViewingTaskPhase('development')
      // 阶段入口先展示应用文件源码区；编码 Diff 在这里呈现，页面预览完成后再切到开发产物。
      setActiveDetailTarget({ type: 'none' })
      setRightPanel({ type: 'source' })
      setRightPanelLayout('split')
      ensureDevelopmentSession()
        .then((identity) => handleOpenChatSession(identity.sessionId))
        .catch(() => {
          // 会话创建/打开失败必须允许重试：一次性门闩不回退会把工作台永久卡在
          // 「正在切换阶段」，下一次渲染即可重新走完切换链路。
          phaseSwitchHandledRef.current = ''
        })
      return
    }

    setViewingTaskPhase(activeWorkbenchPhase)
    if (activeWorkbenchPhase === 'analysis' || activeWorkbenchPhase === 'planning') {
      setRightPanel({
        type: 'doc',
        docKey: activeWorkbenchPhase === 'planning' ? 'project-plan' : 'requirement-spec'
      })
      setRightPanelLayout('split')
    } else if (activeWorkbenchPhase === 'testing') {
      // 测试阶段默认进入用例工作台，启动/非功能节点和业务用例均在同一目录中选择。
      setRightPanel({ type: 'test-cases' })
      setRightPanelLayout('split')
    } else if (activeWorkbenchPhase === 'review') {
      // 审查阶段默认展示审查报告；应用预览由独立页签承载。
      setRightPanel({ type: 'doc' })
    }

    const stageSession = latestStageSession(sessions, activeWorkbenchPhase)
    if (stageSession) {
      handleOpenChatSession(stageSession.id).catch(() => undefined)
      return
    }

    const createDefaultSession =
      activeWorkbenchPhase === 'analysis'
        ? ensureAnalysisSession
        : activeWorkbenchPhase === 'planning'
          ? ensurePlanningSession
          : activeWorkbenchPhase === 'testing'
            ? createTestingSession
            : activeWorkbenchPhase === 'review'
              ? createReviewSession
              : createAcceptanceSession
    createDefaultSession()
      .then((identity) => handleOpenChatSession(identity.sessionId))
      .catch(() => undefined)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [
    activeWorkbenchPhase,
    activeSessionId,
    ensureAnalysisSession,
    ensurePlanningSession,
    ensureDevelopmentSession,
    createAcceptanceSession,
    createReviewSession,
    createTestingSession,
    loadingSessions,
    sessions
  ])
  /** 授权条「放弃」:丢弃该文档编辑版,回到 Agent 生成版。 */
  const handleDiscardArtifact = (docKey: WorkspaceDocKey): void => {
    setEditedDesignDocs((prev) => {
      if (!(docKey in prev)) return prev
      const next = { ...prev }
      delete next[docKey]
      return next
    })
  }

  const handleStartPageDesign = async (
    pageId: string,
    pageLabel: string,
    hasDetailPlan: boolean,
    templateParams?: {
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ): Promise<void> => {
    const targetKey = pageDetailTargetKey(pageId)
    setInteractingDetailTargetKey(targetKey)
    setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
    setActiveDetailTarget({ type: 'page', pageId })
    const relatedEndpoint = resolvePageRelatedEndpoint(
      pageId,
      pageDesignCatalog,
      developmentPlanningApiContracts
    )
    const started = await handleStartDetailConfirmation(
      pageId,
      pageLabel,
      hasDetailPlan,
      templateParams,
      relatedEndpoint
    )
    if (started) {
      onPlanningArtifactsRefresh()
    } else {
      setGeneratingDetailTargetKey((current) => (current === targetKey ? '' : current))
    }
  }

  /** 对话节点「开始详细设计」：按当前待设计目标（含可选模板）启动页面/接口设计。 */
  const handleStartDetailDesign = async (
    targetType: 'page' | 'endpoint',
    targetId: string,
    targetLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string
      endpointId?: string
      templateId?: string
      templateName?: string
      templateSourcePath?: string
    }
  ): Promise<void> => {
    if (targetType === 'endpoint') {
      await handleStartEndpointDesign(targetId, targetLabel, hasDetailPlan, targetContext)
      return
    }
    await handleStartPageDesign(
      targetId,
      targetLabel,
      hasDetailPlan,
      targetContext && (targetContext.templateId || targetContext.templateSourcePath)
        ? {
            templateId: targetContext.templateId,
            templateName: targetContext.templateName,
            templateSourcePath: targetContext.templateSourcePath
          }
        : undefined
    )
  }

  /** 启动当前接口的详细设计；解锁状态仍以后续持久化目录检查为准。 */
  const handleStartEndpointDesign = async (
    endpointTargetId: string,
    endpointLabel: string,
    hasDetailPlan: boolean,
    targetContext?: {
      apiContractId?: string
      endpointId?: string
    }
  ): Promise<void> => {
    const targetKey = targetContext?.apiContractId
      ? endpointDetailTargetKey(
          targetContext.apiContractId,
          targetContext.endpointId || endpointTargetId
        )
      : ''
    setInteractingDetailTargetKey(targetKey)
    setGeneratingDetailTargetKey(hasDetailPlan ? '' : targetKey)
    if (targetContext?.apiContractId) {
      setActiveDetailTarget({
        type: 'endpoint',
        apiContractId: targetContext.apiContractId,
        endpointId: targetContext.endpointId || endpointTargetId,
        endpointKey: `${targetContext.apiContractId}:${targetContext.endpointId || endpointTargetId}`,
        label: endpointLabel
      })
    } else {
      setActiveDetailTarget({ type: 'none' })
    }
    const started = await handleStartEndpointDetailConfirmation({
      apiContractId: targetContext?.apiContractId,
      endpointId: targetContext?.endpointId || endpointTargetId,
      endpointLabel,
      hasDetailPlan
    })
    if (started) {
      onPlanningArtifactsRefresh()
    } else {
      setGeneratingDetailTargetKey((current) => (current === targetKey ? '' : current))
    }
  }

  const handleOpenChatSession = async (sessionId: string): Promise<void> => {
    setActiveView('chat')
    // 对话切换只改变消息上下文；右侧产物、文件、预览和未提交弹框都保持用户当前选择。
    await handleOpenSession(sessionId)
  }

  /** 提交详细设计确认后进入 DAG/构建链路，停止使用详细设计生成进度遮罩。 */
  const handleSubmitWorkflowClarification = async (
    workflow: WorkflowRunPayload,
    answers: ClarificationAnswers
  ): Promise<boolean> => {
    setGeneratingDetailTargetKey('')
    const pendingChanges = workflowCodeChanges(workflow)
    const clarificationMode = String(
      (workflow.state?.clarification as { mode?: unknown } | undefined)?.mode ||
        (workflow.result?.clarification as { mode?: unknown } | undefined)?.mode ||
        ''
    )
    // 正式文件必须先经过 Diff 接受；不能用需求/计划确认卡直接跳过文件写入门槛。
    if (
      pendingChanges?.files.length &&
      ['requirements', 'project_planning', 'code_review'].includes(
        String(workflow.summary?.phase || '')
      ) &&
      clarificationMode !== 'file_acceptance' &&
      typeof answers.file_acceptance !== 'string'
    ) {
      setPreviewError('请先在右侧确认文件 Diff，文件保存后才能确认并进入下一阶段。')
      return false
    }
    const submitted = await handleSubmitClarification(workflow, answers)
    // 确认后刷新规划产物：开发阶段确认详细设计(detail_review)后 markPageDesigned，
    // 大纲与右侧按新状态展示（待设计 → 已设计）。
    // markPageDesigned 在 mock 确认处理中异步执行，立即刷新读不到新状态，延迟二次刷新兜底。
    if (submitted) {
      // 审查报告确认后 Workflow 已完成；清除可能残留的审查阶段手动查看覆盖，继续进入验收。
      if (
        workflow.summary?.phase === 'code_review' &&
        (typeof answers.file_acceptance === 'string' || answers.code_review !== undefined)
      ) {
        switchPhase(null)
      }
      onPlanningArtifactsRefresh()
      // mock 确认处理（markPageDesigned）在提交后异步执行，延迟二次刷新确保大纲/产物读到已设计。
      window.setTimeout(onPlanningArtifactsRefresh, 3000)
      // 首轮操作完成后的自动命名：任务仍是默认名「新任务」时按阶段职责命名。
      const roundTitle = firstRoundTitleForPhase(String(workflow.summary?.phase || ''))
      if (roundTitle) {
        void autoRenameTaskAfterFirstRound(activeSession, roundTitle)
      }
    }
    return submitted
  }

  /** 判断当前待接受变更是否正处于项目计划确认，决定是否先要求选择测试生成任务。 */
  const isProjectPlanConfirmation = (workflow: WorkflowRunPayload | undefined): boolean => {
    const clarification =
      workflow?.summary?.clarification ||
      workflow?.state?.clarification ||
      workflow?.result?.clarification
    return (
      workflow?.summary?.phase === 'project_planning' &&
      Boolean(
        clarification &&
          typeof clarification === 'object' &&
          (clarification as { mode?: unknown }).mode === 'project_plan_confirmation'
      )
    )
  }

  /**
   * 保存待接受的全部文件变更并继续当前工作流。
   * 同步执行一次交付页面与依赖接口多个源码文件，接受时全部落库后再提交确认。
   */
  const approvePendingCodeChange = (): void => {
    const context = pendingCodeChangeContext
    if (!context || context.changes.files.length === 0 || !context.workflow || !activeSession) {
      return
    }
    const changes = context.changes
    const firstFile = changes.files[0]
    void Promise.all(
      changes.files.map((file) => {
        const projectPath = file.path.startsWith(`${APPLICATION_ROOT}/`)
          ? file.path
          : appPath(file.path)
        return recordAcceptedFile(activeSession.sessionId, {
          path: file.path,
          content: contentFromFileDiff(file.diff, savedFileContentByPath.get(projectPath) || '')
        })
      })
    )
      .then(() =>
        handleSubmitWorkflowClarification(context.workflow, {
          file_acceptance: firstFile.path
        })
      )
      .catch((caughtError) => {
        document.title = 'ACCEPT-ERR ' + String(caughtError)
        setPreviewError(
          caughtError instanceof Error ? caughtError.message : '保存文件确认结果失败。'
        )
      })
  }

  /** 保存项目计划 Diff，并恢复到独立开发准入 gate，让第二条计划消息先完成。 */
  const acceptProjectPlanDiff = (): void => {
    const context = pendingCodeChangeContext
    const file = context?.changes.files[0]
    if (!context?.workflow || !file || !activeSession) return
    const projectPath = file.path.startsWith(`${APPLICATION_ROOT}/`)
      ? file.path
      : appPath(file.path)
    setProjectPlanDiffAccepting(true)
    void recordAcceptedFile(activeSession.sessionId, {
      path: file.path,
      content: contentFromFileDiff(file.diff, savedFileContentByPath.get(projectPath) || '')
    })
      .then(() =>
        handleSubmitWorkflowClarification(context.workflow, { file_acceptance: file.path })
      )
      .catch((caughtError) => {
        setPreviewError(
          caughtError instanceof Error ? caughtError.message : '保存项目计划确认结果失败。'
        )
      })
      .finally(() => setProjectPlanDiffAccepting(false))
  }

  // 项目规划准入门（planning_stage_entry）：与开发准入门同一模式——
  // 门禁是工作流自身的待输入节点，弹框只是它的显示面；Diff 确认（文件改动条）
  // 属于「确认文档变更」节点，与阶段门禁彻底解耦。当前会话没有门禁轨迹时，
  // 回退扫描分析阶段全部会话，避免新建任务切走查看对象后门禁失联。
  const planningEntryWorkflow =
    pendingGateWorkflow(
      latestWorkflowForDisplay as WorkflowRunPayload | undefined,
      'planning_stage_entry'
    ) ||
    findPendingGateWorkflow(sessions, 'analysis', {
      mode: 'planning_stage_entry',
      readMessages: getSessionMessages,
      runtimeKey: (sessionId) =>
        sessionRuntimeKey(application.workspaceRoot || '', editorMode, sessionId)
    })
  // 项目规划门禁是否待处理：上报顶部阶段条，供暂离后点击「项目规划」重新唤起。
  const planningEntryAvailable = Boolean(planningEntryWorkflow)

  // 准入门到达且未被本轮暂离时自动弹出（与开发准入门同一自动开启 effect）。
  useEffect(() => {
    if (
      !planningEntryWorkflow ||
      dismissedPlanningEntryRunId === planningEntryWorkflow.runId ||
      planningEntryModalOpen ||
      loading ||
      stageSessionSwitching
    ) {
      return
    }
    const timer = window.setTimeout(() => setPlanningEntryModalOpen(true), 0)
    return () => window.clearTimeout(timer)
  }, [
    dismissedPlanningEntryRunId,
    loading,
    planningEntryModalOpen,
    planningEntryWorkflow,
    stageSessionSwitching
  ])

  // 顶部阶段条发起进入请求时复用同一个门禁弹框：只有确认动作才提交续跑并切换阶段。
  useCountedRequestTrigger({
    available: planningEntryAvailable,
    onOpen: () => {
      // 清除取消时的 dismiss 标记，自动开启 effect 才允许再次弹出同一 gate。
      setDismissedPlanningEntryRunId('')
      setPlanningEntryModalOpen(true)
    },
    request: planningEntryRequest
  })

  // 项目规划门禁待处理即上报“可进入项目规划”：顶部阶段条据此点亮项目规划。
  useEffect(() => {
    onPlanningEntryAvailableChange?.(planningEntryAvailable)
  }, [onPlanningEntryAvailableChange, planningEntryAvailable])

  /** 确认进入项目规划阶段：提交准入门续跑，由规划剧本接管阶段切换。 */
  const handleConfirmPlanningEntry = (): void => {
    const workflow = planningEntryWorkflow
    // 与开发准入门同一竞态处理：先关弹框并标记已消费，防止自动开启 effect 重弹。
    setPlanningEntryModalOpen(false)
    if (!workflow) return
    setDismissedPlanningEntryRunId(workflow.runId)
    void handleSubmitWorkflowClarification(workflow, { planning_stage_entry: 'enter' })
  }

  /** 暂不进入：停留在需求分析阶段；顶部「项目规划」可随时重新唤起同一门禁。 */
  const handleCancelPlanningEntry = (): void => {
    setPlanningEntryModalOpen(false)
    setDismissedPlanningEntryRunId(planningEntryWorkflow?.runId || '')
  }

  /** 接受项目计划 Diff 后再展示任务类型选择，其它文件变更保持原有的一键接受行为。 */
  const handleApprovePendingCodeChange = (): void => {
    if (isProjectPlanConfirmation(pendingCodeChangeContext?.workflow)) {
      acceptProjectPlanDiff()
      return
    }
    approvePendingCodeChange()
  }

  /** 用户在独立开发准入 gate 选择任务类型后，继续进入开发阶段。 */
  const handleConfirmTestCaseTaskType = (taskType: TestCaseGenerationTaskType): void => {
    const workflow = developmentEntryWorkflow
    // 无论门禁工作流是否仍处于待确认态都先关闭弹框：门禁被上游流转消费掉时，
    // 不关会留下一个确认无效且关不掉的弹框（确认按钮提前返回）。
    setTestCaseTaskTypeModalOpen(false)
    onTestCaseGenerationTaskTypeChange?.(taskType)
    if (!workflow) return
    // 提交后到完成帧落地前，最新快照仍可能是本门禁的待确认态（loading 短暂为 false），
    // 自动开启 effect 会据此把刚关掉的弹框再次弹开；标记为已消费可阻止这次竞态重开。
    setDismissedDevelopmentEntryRunId(workflow.runId)
    void handleSubmitWorkflowClarification(workflow, { test_case_task_type: taskType })
  }

  /** 取消选择时停留在项目规划阶段；重新进入页面后仍可从同一 gate 继续。 */
  const handleCancelTestCaseTaskType = (): void => {
    setTestCaseTaskTypeModalOpen(false)
    setDismissedDevelopmentEntryRunId(developmentEntryWorkflow?.runId || '')
  }

  // PlanExecutionDock 移除后，查看计划 / 结构化确认交互暂未挂载（保留节点卡入口）。

  /** 用户关闭技能后立即清理当前会话草稿中的同名标签。 */
  const handleSkillDisabled = (skillName: string): void => {
    const nextSkills = selectedSkills.filter((skill) => skill.name !== skillName)
    if (nextSkills.length !== selectedSkills.length) {
      setSelectedSkillsByKey(draftKey, nextSkills)
    }
  }

  const showRightPanel =
    activeView === 'chat' && rightPanelLayout !== 'hidden' && Boolean(rightPanel)
  // 左侧设计文档只允许选中已生成的产物；右侧旧面板若指向未来文档，自动回到当前阶段文档。
  const selectableDesignDocKeys = new Set(
    designDocs
      .filter((doc) => doc.available && stableDesignStatuses[doc.key] !== 'not-started')
      .map((doc) => doc.key)
  )
  const phaseDesignDocKey: WorkspaceDocKey =
    activeWorkbenchPhase === 'planning' ? 'project-plan' : 'requirement-spec'
  const explicitDesignDocSelection =
    displayIsDesignPhase && viewingTaskPhase !== activeWorkbenchPhase
  const selectedDesignDocKey = displayIsDesignPhase
    ? explicitDesignDocSelection &&
      activeDesignDocKey &&
      selectableDesignDocKeys.has(activeDesignDocKey)
      ? activeDesignDocKey
      : selectableDesignDocKeys.has(phaseDesignDocKey)
        ? phaseDesignDocKey
        : undefined
    : undefined
  const activeEditableArtifactId = displayIsDesignPhase
    ? selectedDesignDocKey
      ? documentArtifactId(selectedDesignDocKey)
      : ''
    : displayIsReviewPhase
      ? documentArtifactId('code-review')
      : activeArtifactTab.startsWith('endpoint') && conversationEndpointOption
        ? endpointArtifactId(
            conversationEndpointOption.apiContractId,
            conversationEndpointOption.endpointId
          )
        : conversationPageOption
          ? pageArtifactId(conversationPageOption.pageId)
          : ''
  const activeArtifactAccess = activeEditableArtifactId
    ? artifactAccessById[activeEditableArtifactId]
    : undefined
  const artifactEditorReadOnly = !activeArtifactAccess || activeArtifactAccess.mode !== 'write'
  // 项目页签定位的文件：设计/审查按当前文档，开发按当前会话产物源码；随左侧产物选择自动切换。
  const projectInitialFilePath = displayIsDesignPhase
    ? selectedDesignDocKey === 'project-plan'
      ? appPath(WORKSPACE_DOC_PATHS.projectPlan)
      : appPath(WORKSPACE_DOC_PATHS.requirementSpec)
    : displayIsTestingPhase
      ? ''
      : displayIsReviewPhase
        ? appPath(WORKSPACE_DOC_PATHS.codeReview)
        : displayIsAcceptancePhase
          ? appPath(WORKSPACE_DOC_PATHS.requirementSpec)
          : activeSource
            ? appPath(activeSource.filePath)
            : ''
  const selectedProjectFilePath = projectInitialFilePath
  const projectInitialFileExists = Boolean(
    selectedProjectFilePath && savedFileContentByPath.has(selectedProjectFilePath)
  )

  /** 项目树中 Markdown 文档的编辑配置：设计文档带草稿编辑（只读按阶段权限），审查报告只读。 */
  const projectDocConfig = (path: string): ProjectDocumentConfig | undefined => {
    if (
      path === appPath(WORKSPACE_DOC_PATHS.requirementSpec) ||
      path === appPath(WORKSPACE_DOC_PATHS.projectPlan)
    ) {
      const docKey: WorkspaceDocKey =
        path === appPath(WORKSPACE_DOC_PATHS.projectPlan) ? 'project-plan' : 'requirement-spec'
      const savedContent = savedFileContentByPath.get(path)
      const editable = displayIsDesignPhase && !artifactEditorReadOnly
      return {
        content: editedDesignDocs[docKey] ?? savedContent ?? '',
        readOnly: !editable,
        onSaveEdit: editable
          ? (draft: string) => setEditedDesignDocs((prev) => ({ ...prev, [docKey]: draft }))
          : undefined
      }
    }
    if (path === appPath(WORKSPACE_DOC_PATHS.codeReview)) {
      return {
        content: savedFileContentByPath.get(path) || '',
        readOnly: true
      }
    }
    return undefined
  }
  // 两套任务系统各自统计：有排队/执行任务时入口显示运行特效引导查看；
  // 完成任务带未执行的后续步骤（如产物验收）时，悬浮文案给出待处理数量。
  const backgroundTasksRunning: Record<BackgroundTaskSystem, boolean> = {
    async: versionBackgroundTasks.some(
      (task) => task.pool === 'async' && (task.status === 'queued' || task.status === 'running')
    ),
    tide: versionBackgroundTasks.some(
      (task) => task.pool === 'tide' && (task.status === 'queued' || task.status === 'running')
    )
  }
  // 输入区与后台队列共用这一份待继续任务数据，避免一个入口有提醒、另一个入口无从恢复。
  const pendingWorkflowContinuations = versionBackgroundTasks
    .filter(
      (task) => task.kind === 'artifact_implementation' && task.nextStep && !task.nextStep.done
    )
    .map((task) => ({ taskId: task.id, title: task.title }))
  const editingConversationTitle = editingSessionId
    ? sessions.find((session) => session.id === editingSessionId)?.title || '运行中的任务'
    : ''
  const activeRegularSessionTitle = activeSessionId
    ? sessions.find((session) => session.id === activeSessionId)?.title || '新建任务'
    : '新建任务'
  const editingWorkflowActive = Boolean(
    authorizedEditingSessionId && sessionRunStates[authorizedEditingSessionId]
  )
  const pendingRegularSession = pendingRegularSessionId
    ? sessions.find((session) => session.id === pendingRegularSessionId)
    : undefined
  const pendingRegularSessionTitle = pendingRegularSession?.title || '目标任务'
  const showReadOnlyConversationPrompt = Boolean(
    authorizedEditingSessionId && activeSessionId && activeSessionId !== authorizedEditingSessionId
  )

  /** 用户二次确认后转移当前阶段唯一的推进权；运行中或待确认的任务继续占位。 */
  const confirmRegularSessionSwitch = (): void => {
    if (!pendingRegularSessionId || editingWorkflowActive) return
    setAuthorizedEditingSessionId(pendingRegularSessionId)
    void handleOpenChatSession(pendingRegularSessionId)
    setPendingRegularSessionId(undefined)
  }
  return (
    <section
      className={cx(
        'ai-chat-panel',
        showRightPanel && 'embedded-preview-open',
        rightPanelLayout === 'full' && 'right-panel-full',
        rightPanel?.type === 'diff' && 'diff-panel-open',
        elementInspectionActive && 'element-inspection-active',
        splitDragging && 'split-dragging'
      )}
      ref={panelRef}
      style={panelStyle}
    >
      <Modal
        cancelText="取消"
        okButtonProps={{ disabled: editingWorkflowActive }}
        okText={editingWorkflowActive ? '当前不可切换' : '确认切换'}
        onCancel={() => setPendingRegularSessionId(undefined)}
        onOk={confirmRegularSessionSwitch}
        open={Boolean(pendingRegularSession)}
        title="设为当前推进任务？"
      >
        {editingWorkflowActive ? (
          <p>
            “{editingConversationTitle}”还有正在运行或等待确认的任务，请先在该任务中完成，再把推进权交给“
            {pendingRegularSessionTitle}”。
          </p>
        ) : (
          <p>
            切换后，新消息和后续工作流将在“{pendingRegularSessionTitle}”中继续；“
            {editingConversationTitle}”将转为仅查看。
          </p>
        )}
      </Modal>
      <DevelopmentStageCompleteModal
        onCancel={() => setDevelopmentCompleteModalOpen(false)}
        onConfirm={handleConfirmDevelopmentComplete}
        open={developmentCompleteModalOpen}
      />
      <TestingStageCompleteModal
        onCancel={() => setReviewCompleteModalOpen(false)}
        onConfirm={handleConfirmTestingComplete}
        open={reviewCompleteModalOpen}
      />
      {/* 开发准入门与其它阶段门禁弹框并列渲染，交互与状态归口保持一致；
          用例数量由项目规划阶段确认，是准入门中的确定信息，予以保留展示。 */}
      <TestCaseTaskTypeModal
        onCancel={handleCancelTestCaseTaskType}
        onConfirm={handleConfirmTestCaseTaskType}
        open={testCaseTaskTypeModalOpen}
        testCaseTotal={TEST_CASE_ESTIMATE_GROUPS.reduce((total, group) => total + group.total, 0)}
      />
      {/* 需求分析 → 项目规划的阶段门禁：需求文档接受后弹出，与开发准入门共用同一外壳与交互；
          各阶段门禁的确认按钮统一为「进入下一阶段」，阶段信息由标题承载。 */}
      <PhaseGateModal
        cancelText="暂不进入"
        confirmText="进入下一阶段"
        icon={<ProjectOutlined />}
        lead="需求文档已确认，当前版本已具备进入项目规划阶段的条件。"
        onCancel={handleCancelPlanningEntry}
        onConfirm={handleConfirmPlanningEntry}
        open={planningEntryModalOpen}
        subtitle="确认后由项目 Agent 接管项目计划"
        title="进入项目规划阶段？"
      />
      <PhaseNavigation
        activeView={activeView}
        backgroundTasksDrawer={backgroundTasksDrawer}
        backgroundTasksRunning={backgroundTasksRunning}
        conversationDrawerOpen={conversationDrawerOpen}
        onOpenConversationManagement={() => {
          setActiveView('chat')
          onOpenConversationManagement?.()
        }}
        onOpenBackgroundTasks={(system) => onOpenBackgroundTasks?.(system)}
        onShowFiles={handleShowFiles}
        onShowSettings={handleShowSettings}
        onShowSkills={handleShowSkills}
      />
      <div className={cx('ai-chat-assistant')}>
        {activeView === 'skills' ? (
          <SkillsPage onSkillDisabled={handleSkillDisabled} />
        ) : activeView === 'files' ? (
          <AgentFilesPage />
        ) : activeView === 'settings' ? (
          <SettingsPage
            application={application}
            onClose={() => setActiveView('chat')}
            onSaved={onApplicationUpdate}
          />
        ) : (
          <div className={cx('ai-chat-main')}>
            {activeView === 'chat' ? (
              <PageContextHeader
                conversationTitle={
                  (designSessionSwitching
                    ? renderedTaskPhase === 'planning'
                      ? '项目计划'
                      : '需求分析'
                    : sessions.find((session) => session.id === activeSessionId)?.title) ||
                  (displayIsDesignPhase
                    ? renderedTaskPhase === 'planning'
                      ? '项目计划'
                      : '需求分析'
                    : displayIsReviewPhase
                      ? '代码审查'
                      : displayIsAcceptancePhase
                        ? '应用验收'
                        : '新对话')
                }
                historical={viewingHistoricalStage}
                onRename={
                  // 只读版本与历史阶段任务不可重命名：不传回调即隐藏编辑图标。
                  versionReadOnly || viewingHistoricalStage
                    ? undefined
                    : (title) => {
                        if (activeSessionId) void handleRenameSession(activeSessionId, title)
                      }
                }
              />
            ) : null}
            {viewingHistoricalStage && activeView === 'chat' ? (
              <div className={cx('historical-task-notice')} role="status">
                <span>
                  正在查看历史阶段任务，顶部仍处于
                  {WORKBENCH_PHASE_AGENTS[activeWorkbenchPhase].label}
                  阶段。
                </span>
                <button type="button" onClick={() => setViewingTaskPhase(activeWorkbenchPhase)}>
                  返回当前任务
                </button>
              </div>
            ) : null}
            {showReadOnlyConversationPrompt && activeView === 'chat' ? (
              <div className={cx('conversation-view-notice')} role="status">
                <span>
                  当前由“{editingConversationTitle}”推进任务；你正在查看“
                  {activeRegularSessionTitle}”。
                </span>
                <button
                  disabled={editingWorkflowActive}
                  type="button"
                  onClick={() => {
                    if (activeSessionId) setPendingRegularSessionId(activeSessionId)
                  }}
                >
                  {editingWorkflowActive ? '完成当前任务后可切换' : '设为当前推进任务'}
                </button>
              </div>
            ) : null}
            {previewError && (
              <Alert
                className={cx('preview-action-error')}
                message={previewError}
                showIcon
                type="error"
              />
            )}

            <MessageList
              agentPhase={renderedTaskPhase}
              applicationLifecycle={applicationLifecycle}
              conversationPhase={conversationPhase}
              interactionsDisabled={
                versionReadOnly || viewingHistoricalStage || stageSessionSwitching || workspaceBusy
              }
              // 消息列表只按会话身份挂载；同一阶段主会话启动下一条 Workflow 时必须继续复用
              // 同一 DOM/滚动容器，不能因为阶段标签或目标产物变化而重置历史消息。
              key={activeSession?.key || draftKey}
              loading={stageSessionSwitching || loading}
              messages={stageSessionSwitching ? [] : messages}
              mentionLabels={
                displayIsDevelopmentPhase
                  ? composerMentionItems.map((item) => item.label)
                  : undefined
              }
              onDiscardArtifact={handleDiscardArtifact}
              onSubmitClarification={handleSubmitWorkflowClarification}
              onStartDetailDesign={handleStartDetailDesign}
              onOpenComposerPicker={
                displayIsDevelopmentPhase
                  ? () => setArtifactPickerRequest((count) => count + 1)
                  : undefined
              }
            />

            <div className={cx('ai-chat-composer-area')}>
              {pendingCodeChangeContext ? (
                <div className={cx('composer-code-change-slot')}>
                  <CodeChangeCard
                    codeChanges={pendingCodeChangeContext.changes}
                    compact
                    loading={loading || projectPlanDiffAccepting}
                    onApproveAll={handleApprovePendingCodeChange}
                    onOpenFile={() => undefined}
                    onRevert={() => {
                      if (pendingCodeChangeContext.messageId === undefined) return
                      requestCodeChangeRevert(
                        pendingCodeChangeContext.messageId,
                        pendingCodeChangeContext.changes
                      )
                    }}
                    revertDisabled={
                      loading ||
                      projectPlanDiffAccepting ||
                      workspaceBusy ||
                      versionReadOnly ||
                      viewingHistoricalStage ||
                      stageSessionSwitching
                    }
                    reverting={revertingCodeChangeIds.has(pendingCodeChangeContext.changes.id)}
                  />
                </div>
              ) : null}

              <ChatComposer
                activeWorkflow={activeWorkflow}
                availableFiles={workspaceSourceFiles}
                copy={copy}
                draft={draft}
                error={error}
                loading={loading}
                mentionItems={
                  displayIsDevelopmentPhase && !versionReadOnly ? composerMentionItems : undefined
                }
                artifactPickerRequest={artifactPickerRequest}
                // 待继续工作流入口只属于开发阶段对话：验收段在开发主对话播放，
                // 不随版本后台任务泄漏到分析/规划等阶段的对话里。
                pendingWorkflowContinuations={
                  displayIsDevelopmentPhase && !versionReadOnly
                    ? pendingWorkflowContinuations
                    : undefined
                }
                onDraftChange={(value) => setDraftByKey(draftKey, value)}
                onSelectedSkillsChange={(value) => setSelectedSkillsByKey(draftKey, value)}
                onSend={(workflowDebug, selectedFilePaths) =>
                  handleComposerSend(workflowDebug, selectedFilePaths)
                }
                onStopGenerating={handleStopGenerating}
                onLaunchArtifact={(item) => {
                  void handleLaunchArtifact(item)
                }}
                onResumePendingWorkflow={(taskId) => {
                  onRequestBackgroundTaskContinuation?.(taskId)
                }}
                // 开发阶段占位文案引导「产物」按钮发起；其余阶段沿用各 Agent 的默认提示。
                placeholder={
                  displayIsDevelopmentPhase && !versionReadOnly
                    ? '描述问题，或点击左下角「产物」按钮选择并直接发起实施…'
                    : undefined
                }
                readOnly={
                  versionReadOnly || viewingHistoricalStage || stageSessionSwitching || workspaceBusy
                }
                readOnlyMessage={
                  stageSessionSwitching
                    ? '正在切换阶段，请稍候'
                    : workspaceBusy
                      ? `当前由“${editingConversationTitle}”推进任务；本对话仅供查看`
                      : viewingHistoricalStage
                        ? '历史阶段任务仅供查看；如需调整，请从顶部切换应用阶段'
                        : '已生成版本只读，请先发起新迭代或回退后继续调整'
                }
                stopping={stopping}
                selectedSkills={selectedSkills}
                workspaceBusy={workspaceBusy}
                workspaceRoot={workspaceRoot}
              />
            </div>
          </div>
        )}
        {elementInspectionActive && (
          <div aria-hidden="true" className={cx('element-inspection-interaction-mask')} />
        )}
      </div>

      {showRightPanel && rightPanelLayout === 'split' && (
        <div
          aria-label="拖动调整右侧面板宽度"
          aria-orientation="vertical"
          aria-valuenow={assistantPanelWidth}
          className={cx('panel-split-handle', splitDragging && 'dragging')}
          aria-disabled={elementInspectionActive}
          onKeyDown={elementInspectionActive ? undefined : handlePanelSplitKeyDown}
          onMouseDown={elementInspectionActive ? undefined : handlePanelSplitDragStart}
          role="separator"
          tabIndex={elementInspectionActive ? -1 : 0}
          title="拖动调整左右面板宽度"
        />
      )}

      {activeView === 'chat' &&
        (!showRightPanel ||
          rightPanel?.type === 'preview' ||
          rightPanel?.type === 'doc' ||
          rightPanel?.type === 'process' ||
          rightPanel?.type === 'source' ||
          rightPanel?.type === 'development-artifacts' ||
          rightPanel?.type === 'test-cases') &&
        (rightPanelLayout === 'hidden' || Boolean(rightPanel)) && (
          <WorkbenchRightPanel
            tabs={workspaceTabs}
            activeTab={activeWorkspaceTab}
            layout={rightPanelLayout}
            onLayoutChange={handleRightPanelLayoutChange}
            onTabChange={openWorkspaceTab}
          >
            {rightPanel?.type === 'preview' && (
              <BrowserPreviewPanel
                application={application}
                pages={developmentPlanningPages}
                requestKey={rightPanel.requestKey}
                requestedUrl={rightPanel.url}
                previewBaseUrl={runtimePreviewBaseUrl}
                selectedPagePath={
                  conversationPageOption?.path ||
                  (activeHeaderTarget.type === 'page' ? activeHeaderTarget.path : '/')
                }
                applicationMode={isAcceptancePreviewTab}
                errorMessage={runtimePreviewLaunchError}
                acceptanceEnabled={isAcceptancePreviewTab}
                acceptanceAccepted={acceptanceAccepted}
                onAcceptApplication={handleAcceptApplication}
                acceptanceReadOnly={versionReadOnly}
                onSubmitAcceptanceFeedback={handleSubmitAcceptanceFeedback}
                onInspectingChange={setElementInspectionActive}
              />
            )}
            {(rightPanel?.type === 'doc' || rightPanel?.type === 'source') && (
              <SourcePanel
                diff={pendingFileDiff ?? null}
                docConfig={projectDocConfig}
                directories={workspaceScaffoldDirectories}
                files={workspaceSourceFiles}
                initialFilePath={projectInitialFileExists ? projectInitialFilePath : ''}
              />
            )}
            {rightPanel?.type === 'development-artifacts' && (
              <DevelopmentArtifactsPanel
                application={application}
                activeId={activeDevelopmentArtifactId}
                apiContracts={developmentPlanningApiContracts}
                entities={developmentPlanningEntities}
                items={developmentArtifactItems}
                pageTree={developmentPlanningPageTree}
                pagePreviewUrl={pagePreviewUrl}
                pages={developmentPlanningPages}
                onSelect={handleSelectDevelopmentArtifact}
              />
            )}
            {rightPanel?.type === 'test-cases' && (
              <TestCasesPanel
                executionStatus={
                  testExecutionSnapshot?.status ||
                  (activeWorkflow?.summary?.status === 'running' ? 'running' : 'idle')
                }
                execution={testExecutionSnapshot}
                onRetry={onRetryTestCases}
                snapshot={testCasePreparation}
              />
            )}
          </WorkbenchRightPanel>
        )}
    </section>
  )
}
