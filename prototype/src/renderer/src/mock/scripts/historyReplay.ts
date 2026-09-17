// 预置应用 v1.3 历史会话回放生成器。
// 不再手写"简化剧本"：直接复用交互演示的同一组剧本（设计/规划状态机、页面与接口
// 工作台、应用API数据绑定、应用测试、代码审查、应用验收），按版本终态无头快进驱动——
// 门禁用演示预置应答逐个原地落定，采集真实工作流轨迹后组装成静态历史会话。
// 生成结论与新建应用旅程同构：工作流消息不带文字正文；设计/规划一轮用户消息一来回；
// 开发/测试/审查/验收的原地确认复用同一条消息；测试按用例切换消息分段。
import type { ApplicationConfig, WorkflowRunPayload } from '../../typings'
import type { ChatSessionRecord, ChatSessionSavedFile } from '../../service/chatSessions'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
import type { AgentChatMessage } from '../../components/AiChatPanel/types'
import type { WorkbenchPhase } from '../../workbenchPhase'
import type { InitializationPlanningSeed } from '../../initializationPlanning'
import type { ReplayCallbacks } from './workbenchShared'
import { buildClarificationContinuationMessage } from '../../components/AiChatPanel/components/WorkflowRunCard'
import { workflowCodeChanges } from '../../components/AiChatPanel/utils'
import { contentFromFileDiff } from '../../components/AiChatPanel/panelHelpers'
import { replayDesignPhase } from './planning'
import {
  replayApplicationTesting,
  replayCodeReview,
  replayWorkbench
} from './workbench'
import { setReplayFastForward } from './replayClock'
import { resetWorkbenchLifecycle } from '../mockHttpAgent'
import { appPath, WORKSPACE_DOC_PATHS } from '../workspaceFiles'
import {
  createInitializationPlanningRecord,
  persistInitializationPlanningRecord,
  readInitializationPlanningRecord
} from '../../initializationPlanning'
import { appDataByWorkspace } from '../../../../../mock-data/index'

type ClarificationAnswers = Record<string, unknown>

/** 剧本回调的采集面板：由链路执行器填充，剧本本身无感知。 */
type ReplaySink = Pick<ReplayCallbacks, 'onContent' | 'onWorkflow' | 'onProcessSteps' | 'onAcceptFiles'>

/** 单条链路的最大门禁轮数：超过即认为遇到未知门禁，防御性中断避免死循环。 */
const MAX_GATE_ROUNDS = 40

/** 读取工作流载荷里的待处理门禁（模式 + 原始问题）。 */
function clarificationOf(payload: WorkflowRunPayload): {
  mode: string
  questions: Array<Record<string, unknown>>
} {
  const state = (payload.state || {}) as Record<string, unknown>
  const clarification = (state.clarification || {}) as Record<string, unknown>
  return {
    mode: String(clarification.mode || ''),
    questions: Array.isArray(clarification.questions)
      ? (clarification.questions as Array<Record<string, unknown>>)
      : []
  }
}

/**
 * 演示预置应答：按门禁模式返回"历史用户当时确认的答案"。
 * 取值与对话区各交互卡的提交动作逐字对齐（WorkflowRunCard submitClarification）。
 */
function cannedAnswerFor(payload: WorkflowRunPayload): ClarificationAnswers | undefined {
  const { mode, questions } = clarificationOf(payload)
  switch (mode) {
    case 'requirement_clarification': {
      // 澄清向导按题目预置答案作答（mock-data/pms-new/clarification-questions.json）。
      const answers: ClarificationAnswers = {}
      questions.forEach((question, index) => {
        const key = String(question.id || question.header || question.question || index)
        if (question.presetAnswer !== undefined) answers[key] = question.presetAnswer
      })
      return answers
    }
    case 'requirement_document_confirmation':
      return { planning_action: { action: 'confirm', artifactKey: 'requirement-spec' } }
    case 'technical_plan_confirmation':
      return { planning_action: { action: 'confirm', artifactKey: 'technical-plan' } }
    case 'ui_design_confirmation': {
      // 与交互卡口径一致：先逐页选定版式（选满即批量提交 select_template），
      // 页面生成完毕后再提交 ui_design_action 确认；直接确认会因页面未生成被状态机拒绝。
      const state = (payload.state || {}) as Record<string, unknown>
      const uiDesigns = (state.ui_designs || {}) as {
        pages?: Array<{ pageId?: string; status?: string }>
      }
      const pages = uiDesigns.pages || []
      const ready =
        pages.length > 0 &&
        pages.every((page) => ['generated', 'confirmed'].includes(String(page.status)))
      if (ready) return { ui_design_action: 'confirm' }
      const templateByPage: Record<string, string> = {
        'recheck-introduction': '多分组表单',
        'my-rechecks': '通用列表查询'
      }
      return {
        planning_action: {
          action: 'select_template',
          artifactKey: 'ui-designs',
          pages: pages
            .filter((page) => page.status !== 'confirmed')
            .map((page) => ({
              pageId: String(page.pageId),
              template: templateByPage[String(page.pageId)] || '通用列表查询'
            }))
        }
      }
    }
    case 'planning_stage_entry':
      return { planning_stage_entry: 'enter' }
    case 'development_entry_confirmation':
      return { test_case_task_type: 'async' }
    case 'detail_review':
      return { detail_review: { review_status: 'confirmed', target_changes: [], overall_note: '' } }
    case 'background_dispatch':
      // 存量迭代按同步任务叙事：代码在对话内当场生成交付，不进后台任务池。
      return { background_dispatch: 'sync' }
    case 'file_acceptance': {
      const changes = workflowCodeChanges(payload)
      const first = changes?.files?.[0]
      return first ? { file_acceptance: first.path } : undefined
    }
    case 'page_acceptance':
      return { page_acceptance: 'accepted' }
    case 'test_case_execute':
      return { confirm_test_case: '是' }
    case 'code_review':
      return { code_review: 'confirmed' }
    case 'application_acceptance':
      return { application_acceptance: 'accepted' }
    case 'api_binding': {
      // 历史用户按卡面预填草稿确认映射：原样回显来源选定步骤写入的草稿（无草稿的旧载荷兜底为已确认）。
      const state = (payload.state || {}) as Record<string, unknown>
      const clarification = (state.clarification || {}) as Record<string, unknown>
      return clarification.draft && typeof clarification.draft === 'object'
        ? { api_binding: clarification.draft }
        : { api_binding: 'confirmed' }
    }
    case 'api_source_type': {
      // 类型选择按技术规划意向作答（卡面建议项）；无意向时按数据表处理。
      const state = (payload.state || {}) as Record<string, unknown>
      const clarification = (state.clarification || {}) as Record<string, unknown>
      return { api_source_type: String(clarification.suggestion || '数据库') }
    }
    case 'api_source_select': {
      // 来源选择取目录里第一个可绑定对象：外部API选首个接口，数据表选首个连接下的首张表。
      const state = (payload.state || {}) as Record<string, unknown>
      const clarification = (state.clarification || {}) as Record<string, unknown>
      const externals = Array.isArray(clarification.externals)
        ? (clarification.externals as Array<Record<string, unknown>>)
        : []
      const external = externals[0]
      if (external) return { api_source_select: String(external.key) }
      const databases = Array.isArray(clarification.databases)
        ? (clarification.databases as Array<{ tables?: Array<{ key?: unknown }> }>)
        : []
      const table = databases[0]?.tables?.[0]
      return table && typeof table.key === 'string'
        ? { api_source_select: table.key }
        : undefined
    }
    case 'api_source_missing':
      // 目录为空在预置旅程中不出现；无预置应答让链路提前收口并给出告警，避免盲目重试。
      return undefined
    default:
      return undefined
  }
}

/**
 * 门禁应答后的消息落定：确认卡转已提交、待输入节点落为完成——
 * 与实时续跑（useWorkflowConversation.handleSubmitClarification）的消息改写同型。
 */
function applyGateSubmission(
  message: AgentChatMessage,
  payload: WorkflowRunPayload,
  answers: ClarificationAnswers
): void {
  const state = (payload.state || {}) as Record<string, unknown>
  const clarification = (state.clarification || {}) as Record<string, unknown>
  const submitted = { ...clarification, status: 'submitted' }
  message.workflow = {
    ...payload,
    summary: { ...payload.summary, clarification: submitted },
    state: {
      ...state,
      clarification: submitted,
      clarificationAnswers: answers
    }
  } as WorkflowRunPayload
  message.processSteps = (message.processSteps || []).map((step) =>
    step.status === 'requires_user_input' ? { ...step, status: 'completed' } : step
  )
}

/** 测试工作流分段键：同一用例的后续节点复用消息，下一条用例切换新消息。 */
function testSegmentKey(payload: WorkflowRunPayload): string {
  const state = (payload.state || {}) as Record<string, unknown>
  const result = (payload.result || {}) as Record<string, unknown>
  return String(
    state.testWorkflowKey ||
      result.testWorkflowKey ||
      state.testWorkflowType ||
      state.workflowType ||
      result.testWorkflowType ||
      result.workflowType ||
      ''
  ).trim()
}

type ChainInput = {
  threadId: string
  /** 首条用户消息；suppressUserMessage 时仅作为原始请求留档不落消息。 */
  requestText: string
  suppressUserMessage?: boolean
  agentPhase: WorkbenchPhase
  /** 每次续跑是否复用当前消息；缺省复用，避免把原地确认拆成新卡。 */
  reuseMessage?: (payload: WorkflowRunPayload) => boolean
  /** 按载荷切换消息分段（测试按用例）。 */
  segmentKey?: (payload: WorkflowRunPayload) => string
  /** 门禁预置应答；返回 undefined 表示无法推进，防御性中断。 */
  answerFor: (payload: WorkflowRunPayload) => ClarificationAnswers | undefined
  invoke: (
    options: SendWorkflowMessageOptions,
    sink: ReplaySink
  ) => Promise<WorkflowRunPayload | undefined>
  baseOptions: Partial<SendWorkflowMessageOptions>
  nextTime: () => number
}

type ChainResult = {
  messages: AgentChatMessage[]
  savedFiles: ChatSessionSavedFile[]
  final?: WorkflowRunPayload
}

/** 合并同一工作流跨轮次的轨迹节点：按 id 更新状态，新节点顺序追加（与实时对话合并规则一致）。 */
function mergeSteps(
  previous: ProcessStepRecord[] | undefined,
  next: ProcessStepRecord[]
): ProcessStepRecord[] {
  const merged = new Map<string, ProcessStepRecord>()
  for (const step of previous || []) merged.set(step.id, step)
  for (const step of next) {
    const existing = merged.get(step.id)
    merged.set(
      step.id,
      existing
        ? {
            ...existing,
            ...step,
            // 历史回看里已提交门禁对应的待输入节点保持完成态，不回退为等待。
            status:
              existing.status === 'completed' && step.status === 'requires_user_input'
                ? 'completed'
                : step.status
          }
        : step
    )
  }
  return [...merged.values()]
}

/**
 * 无头驱动一条工作台剧本链到完成态：发送 → 门禁预置应答 → 续跑，循环到 completed。
 * 过程中按实时对话的组装规则收集用户/assistant 消息；接受 Diff 的门禁同时沉淀文件快照。
 */
async function runChain(
  input: ChainInput,
  workspaceRoot: string,
  application: ApplicationConfig
): Promise<ChainResult> {
  const { agentPhase, nextTime } = input
  const messages: AgentChatMessage[] = []
  const savedFiles: ChatSessionSavedFile[] = []
  if (!input.suppressUserMessage) {
    const time = nextTime()
    messages.push({ id: time, role: 'user', content: input.requestText, createdAt: time })
  }
  let current: AgentChatMessage = {
    id: nextTime(),
    role: 'assistant',
    content: '',
    agentPhase,
    createdAt: nextTime()
  }
  messages.push(current)
  let currentSegment = ''
  let payload: WorkflowRunPayload | undefined
  let options: SendWorkflowMessageOptions = {
    ...input.baseOptions,
    workspaceRoot,
    application,
    editorMode: 'frontend',
    message: input.requestText
  } as SendWorkflowMessageOptions

  const collect = async (): Promise<void> => {
    // 按分段缓冲、逐段落位：一轮脚本可能连续 emit 上一段完成态与下一段门禁（测试按
    // 用例），各段的载荷/轨迹/收尾文本先落进各自的缓冲，再依出现次序写入对应消息——
    // 上一段消息停在本段完成态，下一段门禁开新消息，与实时对话的分段规则一致。
    const buffers: Array<{
      key: string
      payload?: WorkflowRunPayload
      content: string
      steps: ProcessStepRecord[]
    }> = []
    const ensureBuffer = (key: string): { key: string; payload?: WorkflowRunPayload; content: string; steps: ProcessStepRecord[] } => {
      let buffer = buffers.find((item) => item.key === key)
      if (!buffer) {
        buffer = { key, content: '', steps: [] }
        buffers.push(buffer)
      }
      return buffer
    }
    const sink: ReplaySink = {
      onContent: (next) => {
        if (buffers.length) buffers[buffers.length - 1].content = next
      },
      onWorkflow: (next) => {
        ensureBuffer(input.segmentKey ? input.segmentKey(next) : currentSegment).payload = next
      },
      onProcessSteps: (next) => {
        if (buffers.length) {
          const last = buffers[buffers.length - 1]
          last.steps = mergeSteps(last.steps, next)
        }
      },
      // 确定性生成物（应用API数据适配）不过 Diff 门禁：交付即沉淀进会话文件快照。
      onAcceptFiles: (files) => {
        files.forEach((file) => {
          savedFiles.push({
            path: file.path,
            content: file.content,
            savedAt: nextTime()
          })
        })
      }
    }
    payload = undefined
    await input.invoke(options, sink)
    for (const buffer of buffers) {
      if (!buffer.payload) continue
      const isNewSegment = Boolean(input.segmentKey) && buffer.key !== currentSegment
      if (isNewSegment) {
        current = {
          id: nextTime(),
          role: 'assistant',
          content: '',
          agentPhase,
          createdAt: nextTime()
        }
        messages.push(current)
        currentSegment = buffer.key
      }
      if (current) {
        current.workflow = buffer.payload
        current.content = buffer.content
        if (buffer.steps.length) current.processSteps = mergeSteps(current.processSteps, buffer.steps)
        const changes = workflowCodeChanges(buffer.payload)
        if (changes) current.codeChanges = changes
      }
      payload = buffer.payload
    }
  }

  await collect()
  for (
    let round = 0;
    payload?.summary?.status === 'requires_user_input' && round < MAX_GATE_ROUNDS;
    round += 1
  ) {
    const answers = input.answerFor(payload)
    const { mode } = clarificationOf(payload)
    if (!answers) {
      console.warn('[historyReplay] 遇到没有预置应答的门禁，链路提前收口：', mode)
      break
    }
    applyGateSubmission(current, payload, answers)
    // 文件接受门禁：与实时"接受 Diff"一致，把变更文件沉淀进会话文件快照。
    if (mode === 'file_acceptance') {
      const changes = workflowCodeChanges(payload)
      for (const file of changes?.files || []) {
        savedFiles.push({
          path: file.path,
          content: contentFromFileDiff(file.diff, ''),
          savedAt: nextTime()
        })
      }
    }
    const continuationText =
      buildClarificationContinuationMessage(payload, answers as never) ||
      `已确认「${mode}」，请继续。`
    const reuse = input.reuseMessage ? input.reuseMessage(payload) : true
    if (!reuse) {
      const time = nextTime()
      messages.push({ id: time, role: 'user', content: continuationText, createdAt: time })
      current = {
        id: nextTime(),
        role: 'assistant',
        content: '',
        agentPhase,
        createdAt: nextTime()
      }
      messages.push(current)
    }
    options = {
      ...options,
      message: continuationText,
      clarificationAnswers: answers,
      originalRequest: input.requestText,
      resumeState: payload
    } as SendWorkflowMessageOptions
    await collect()
  }
  return { messages, savedFiles, final: payload }
}

/** 工作台链路（开发/测试/审查/验收）的原地确认都复用同一条消息；应用验收确认续跑换新消息。 */
function reuseWorkbenchMessage(payload: WorkflowRunPayload): boolean {
  return clarificationOf(payload).mode !== 'application_acceptance'
}

/** 会话首末时间戳；空会话兜底为当前时间。 */
function sessionSpan(messages: AgentChatMessage[]): { createdAt: number; updatedAt: number } {
  const times = messages.map((message) => message.createdAt)
  return {
    createdAt: times.length ? Math.min(...times) : Date.now(),
    updatedAt: times.length ? Math.max(...times) : Date.now()
  }
}

/** 只保留有内容的消息；空 assistant 消息（无卡片、无正文、无轨迹）不进历史。 */
function meaningfulMessages(messages: AgentChatMessage[]): AgentChatMessage[] {
  return messages.filter(
    (message) =>
      message.role === 'user' ||
      Boolean(message.content.trim()) ||
      Boolean(message.workflow) ||
      Boolean(message.processSteps?.length)
  )
}

let historyCache: ChatSessionRecord[] | undefined

/**
 * 生成预置应用 v1.3 的全阶段历史会话（进程内只回放一次）。
 * 链路顺序对齐真实旅程：设计/规划确认门 → 应用页面与接口实现 → 应用API数据绑定 →
 * 应用测试 → 代码审查 → 应用验收；结束后复位剧本注册态，冷启动校准仍走静态基线。
 */
export async function generateHistorySessions(workspaceRoot: string): Promise<ChatSessionRecord[]> {
  if (historyCache) return historyCache
  const scenario = appDataByWorkspace(workspaceRoot)
  const application = scenario.app
  const versionId = application.currentVersionId || 'app-pms-new-v1-3'
  const day = 86400000
  let clock = Date.now() - 8 * day
  const nextTime = (): number => (clock += 4 * 60000)
    const buildChain = async (
      input: Omit<ChainInput, 'nextTime'>
    ): Promise<ChainResult> => runChain({ ...input, nextTime }, workspaceRoot, application)

  setReplayFastForward(true)
  try {
    // —— 设计/规划：把当前版本规划记录重置为新迭代初始态，完整回放全部确认门。——
    // 链路结束后记录停在 ready_for_workbench（产物确认 + 文档齐备），与预置基线一致。
    const seed = {
      requirementSpec: scenario.requirementSpec,
      productPlan: scenario.productPlan,
      uiDesigns: scenario.uiDesigns,
      technicalPlan: scenario.technicalPlan
    } as InitializationPlanningSeed
    const shadowApp = { ...application, versions: undefined } as ApplicationConfig
    persistInitializationPlanningRecord(
      createInitializationPlanningRecord(shadowApp, seed),
      application
    )

    // 设计/规划链：一轮确认一条用户回复 + 一条新 assistant 消息；进入计划阶段确认轮
    // 开始，消息归属（与会话线程）从需求分析切到项目计划，与实时会话路由一致。
    const analysisMessages: AgentChatMessage[] = []
    const planningMessages: AgentChatMessage[] = []
    let bucket = analysisMessages
    let threadId = 'thread-analysis-v1-3'
    let current: AgentChatMessage | undefined
    let payload: WorkflowRunPayload | undefined
    let designOptions: SendWorkflowMessageOptions = {
      application,
      workspaceRoot,
      editorMode: 'frontend',
      message: '基于已生成版本的 v1.2 发起迭代。回检单越来越多，需要按审核状态筛选并持续跟踪处理状态。'
    } as SendWorkflowMessageOptions

    const collectDesign = async (): Promise<void> => {
      current = {
        id: nextTime(),
        role: 'assistant',
        content: '',
        agentPhase: bucket === analysisMessages ? 'analysis' : 'planning',
        createdAt: nextTime()
      }
      bucket.push(current)
      payload = undefined
      await replayDesignPhase(threadId, designOptions, {
        onContent: (next) => {
          if (current) current.content = next
        },
        onWorkflow: (next) => {
          payload = next
        },
        onProcessSteps: (next) => {
          if (current) current.processSteps = next
        }
      })
      if (payload) {
        current.workflow = payload
        const changes = workflowCodeChanges(payload)
        if (changes) current.codeChanges = changes
      }
    }

    const requestText = String(designOptions.message)
    const time = nextTime()
    analysisMessages.push({ id: time, role: 'user', content: requestText, createdAt: time })
    await collectDesign()
    for (
      let round = 0;
      payload?.summary?.status === 'requires_user_input' && round < MAX_GATE_ROUNDS;
      round += 1
    ) {
      const answers = cannedAnswerFor(payload)
      const { mode } = clarificationOf(payload)
      if (!answers) {
        console.warn('[historyReplay] 规划链遇到没有预置应答的门禁：', mode)
        break
      }
      if (current) applyGateSubmission(current, payload, answers)
      const continuationText =
        buildClarificationContinuationMessage(payload, answers as never) ||
        `已确认「${mode}」，请继续。`
      // 进入计划阶段的确认轮开始，后续轮次归属项目计划会话（含会话线程）。
      if (mode === 'planning_stage_entry') {
        bucket = planningMessages
        threadId = 'thread-project-plan-v1-3'
      }
      const replyTime = nextTime()
      bucket.push({ id: replyTime, role: 'user', content: continuationText, createdAt: replyTime })
      current = {
        id: nextTime(),
        role: 'assistant',
        content: '',
        agentPhase: bucket === analysisMessages ? 'analysis' : 'planning',
        createdAt: nextTime()
      }
      bucket.push(current)
      designOptions = {
        ...designOptions,
        message: continuationText,
        clarificationAnswers: answers,
        resumeState: payload,
        originalRequest: requestText
      } as SendWorkflowMessageOptions
      await collectDesign()
    }

    // —— 开发：回检介绍应用页面 → 我的回检应用页面 → 我的回检查询接口 → 回检单应用API数据绑定。——
    const introPage = await buildChain({
      threadId: 'thread-development-v1-3',
      requestText: '开始实现：回检介绍',
      agentPhase: 'development',
      reuseMessage: reuseWorkbenchMessage,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayWorkbench('thread-development-v1-3', options, sink),
      baseOptions: { selectedPageId: 'recheck-introduction', detailTargetType: 'page' },
    })
    const myRechecksPage = await buildChain({
      threadId: 'thread-development-v1-3',
      requestText: '开始实现：我的回检（包含 GET /api/rechecks/my）',
      agentPhase: 'development',
      reuseMessage: reuseWorkbenchMessage,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayWorkbench('thread-development-v1-3', options, sink),
      baseOptions: { selectedPageId: 'my-rechecks', detailTargetType: 'page' },
    })
    const myRechecksEndpoint = await buildChain({
      threadId: 'thread-development-v1-3',
      requestText: '开始实现：GET /api/rechecks/my',
      agentPhase: 'development',
      reuseMessage: reuseWorkbenchMessage,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayWorkbench('thread-development-v1-3', options, sink),
      baseOptions: {
        selectedApiContractId: 'rechecks',
        selectedEndpointId: 'ep-my-rechecks',
        detailTargetType: 'endpoint'
      },
    })
    const myRechecksApi = await buildChain({
      threadId: 'thread-development-v1-3',
      requestText: '开始开发应用API：查询我的回检',
      agentPhase: 'development',
      reuseMessage: reuseWorkbenchMessage,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayWorkbench('thread-development-v1-3', options, sink),
      baseOptions: { selectedObjectId: 'query_my_rechecks', detailTargetType: 'app-api' },
    })
    const reviewerApi = await buildChain({
      threadId: 'thread-development-v1-3',
      requestText: '开始开发应用API：查询审核人信息',
      agentPhase: 'development',
      reuseMessage: reuseWorkbenchMessage,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayWorkbench('thread-development-v1-3', options, sink),
      baseOptions: { selectedObjectId: 'query_recheck_reviewer', detailTargetType: 'app-api' },
    })
    const developmentMessages = [
      ...introPage.messages,
      ...myRechecksPage.messages,
      ...myRechecksEndpoint.messages,
      ...myRechecksApi.messages,
      ...reviewerApi.messages
    ]
    const developmentFiles = [
      ...introPage.savedFiles,
      ...myRechecksPage.savedFiles,
      ...myRechecksEndpoint.savedFiles,
      ...myRechecksApi.savedFiles,
      ...reviewerApi.savedFiles
    ]

    // —— 测试：非功测试 + 六条用例逐一确认执行（每条用例一段消息）。——
    const testing = await buildChain({
      threadId: 'thread-testing-v1-3',
      requestText: '开始应用测试',
      suppressUserMessage: true,
      agentPhase: 'testing',
      reuseMessage: reuseWorkbenchMessage,
      segmentKey: testSegmentKey,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayApplicationTesting('thread-testing-v1-3', options, sink),
      baseOptions: {},
    })

    // —— 审查：规范/安全/健康度扫描 → 报告 Diff 接受 → 审查完成。——
    const review = await buildChain({
      threadId: 'thread-review-v1-3',
      requestText: '开始代码审查',
      suppressUserMessage: true,
      agentPhase: 'review',
      reuseMessage: reuseWorkbenchMessage,
      answerFor: cannedAnswerFor,
      invoke: (options, sink) => replayCodeReview('thread-review-v1-3', options, sink),
      baseOptions: {},
    })


    // 规划记录随设计/规划链路落定：正式文档随会话文件快照沉淀，供应用文件树只读查看。
    const planningRecord = readInitializationPlanningRecord({
      id: application.id,
      currentVersionId: versionId
    })
    const documents = (planningRecord?.documents || {}) as Record<string, string>
    const docFile = (
      key: string,
      path: string,
      at: number
    ): ChatSessionSavedFile | undefined => {
      const content = documents[key]
      return content ? { path: appPath(path), content, savedAt: at } : undefined
    }
    const analysisSpan = sessionSpan(analysisMessages)
    const planningSpan = sessionSpan(planningMessages)
    const developmentSpan = sessionSpan(developmentMessages)
    const testingSpan = sessionSpan(testing.messages)
    const reviewSpan = sessionSpan(review.messages)

    const analysisSaved = [
      docFile('requirement-spec', WORKSPACE_DOC_PATHS.requirementSpec, analysisSpan.updatedAt)
    ].filter((file): file is ChatSessionSavedFile => Boolean(file))
    const planningSaved = [
      docFile('technical-plan', WORKSPACE_DOC_PATHS.technicalPlan, planningSpan.updatedAt)
    ].filter((file): file is ChatSessionSavedFile => Boolean(file))

    const sessions: ChatSessionRecord[] = [
      {
        id: 'session-analysis-v1-3',
        title: '需求分析',
        sessionKind: 'analysis',
        editorMode: 'frontend',
        threadId: 'thread-analysis-v1-3',
        versionId,
        workspaceRoot,
        savedFiles: analysisSaved,
        ...analysisSpan,
        messages: meaningfulMessages(analysisMessages)
      },
      {
        id: 'session-planning-v1-3',
        title: '项目计划',
        sessionKind: 'planning',
        editorMode: 'frontend',
        threadId: 'thread-project-plan-v1-3',
        versionId,
        workspaceRoot,
        savedFiles: planningSaved,
        ...planningSpan,
        messages: meaningfulMessages(planningMessages)
      },
      {
        id: 'session-development-v1-3',
        title: '应用开发',
        sessionKind: 'development',
        editorMode: 'frontend',
        threadId: 'thread-development-v1-3',
        versionId,
        workspaceRoot,
        savedFiles: developmentFiles,
        ...developmentSpan,
        messages: meaningfulMessages(developmentMessages)
      },
      {
        id: 'session-testing-v1-3',
        title: '应用测试',
        sessionKind: 'testing',
        editorMode: 'frontend',
        threadId: 'thread-testing-v1-3',
        versionId,
        workspaceRoot,
        ...testingSpan,
        messages: meaningfulMessages(testing.messages)
      },
      {
        id: 'session-review-v1-3',
        title: '代码审查',
        sessionKind: 'review',
        editorMode: 'frontend',
        threadId: 'thread-review-v1-3',
        versionId,
        workspaceRoot,
        savedFiles: review.savedFiles,
        ...reviewSpan,
        messages: meaningfulMessages(review.messages)
      }
    ]
    historyCache = sessions
    return sessions
  } finally {
    setReplayFastForward(false)
    resetWorkbenchLifecycle()
  }
}
