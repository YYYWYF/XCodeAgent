// 工作台剧本：模拟后端 AG-UI 事件流 + 生命周期。
// 开发阶段前台负责「设计与选择」：详细设计确认后在「选择执行方式」节点选择同步执行
// （对话内当场执行并落任务记录）或后台资源池（异步/潮汐，见 mock/backgroundTaskEngine.ts）；
// 后台任务由引擎无人值守执行到「完成」，验收入口挂在任务条目上。
// 审查/测试/验收阶段仍在本剧本内同步回放；用例生成进度读取统一后台任务流水（backgroundTasks.ts）。

import type {
  ApplicationLifecycle,
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
import {
  buildEndpointSource,
  buildPageSource,
  buildReviewReport,
  type PageDesign
} from '../../workbenchArtifacts'
import {
  BACKGROUND_TASK_SYSTEM_LABEL,
  acceptArtifactTask,
  dispatchArtifactImplementationTask,
  findAwaitingArtifactTask,
  getBackgroundTasks,
  readTestCaseTaskStatus,
  type BackgroundDispatchChoice,
  type BackgroundTaskExecTarget
} from '../../backgroundTasks'
import { ensureBackgroundTaskEngine } from '../backgroundTaskEngine'
import { workflowNode, workflowSegmentNodes } from '../workflowGraphs'
import { endpointArtifactId, pageArtifactId } from '../../workbenchDomain'
// 页面/接口契约基座：单一 pms-new 场景（需求回检单模块）。
import { WORKBENCH_PAGES as PAGES } from '../../../../../mock-data/pms-new/workbench-pages'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-new/planning-artifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { markEndpointDesigned, markPageDesigned } from '../designState'
import { appPath, WORKSPACE_DOC_PATHS } from '../workspaceFiles'
import { TEST_CASE_BLUEPRINTS, type TestCaseDefect } from '../../testCasePreparation'
import { nextLifecycleRevision } from './revision'
import { replayAgentWorkbench, resolveAgentTarget } from './agentWorkbench'

const MOCK_APPLICATION_PREVIEW_URL = 'http://127.0.0.1:5190/'
const MOCK_TEST_CASES = TEST_CASE_BLUEPRINTS
const MOCK_BUSINESS_TEST_CASE_TOTAL = MOCK_TEST_CASES.length

/** 六条业务用例的缺陷剧本：覆盖无缺陷、单缺陷和多缺陷三种演示复杂度。 */
const MOCK_TEST_CASE_DEFECTS: Record<string, Array<Omit<TestCaseDefect, 'status'>>> = {
  'introduction-1': [],
  'my-rechecks-1': [
    {
      id: 'DEF-001',
      severity: '一般',
      target: '我的回检页面',
      title: '列表加载时缺少空状态反馈',
      summary: '接口返回空列表时页面持续显示加载态，用户无法判断当前没有回检单。'
    }
  ],
  'my-rechecks-2': [
    {
      id: 'DEF-002',
      severity: '严重',
      target: '回检单提交表单',
      title: '必填项校验未阻止提交',
      summary: '未填写需求达成情况时仍然发起提交请求，与需求文档中的必填规则不一致。'
    },
    {
      id: 'DEF-003',
      severity: '一般',
      target: '我的回检列表',
      title: '提交成功后列表未自动刷新',
      summary: '提交回检单成功后列表未同步新增记录，用户无法立即确认提交结果。'
    }
  ],
  'my-rechecks-3': [
    {
      id: 'DEF-004',
      severity: '一般',
      target: '状态筛选组件',
      title: '切换筛选条件后页码未重置',
      summary: '用户在后续分页切换状态条件时仍保留旧页码，导致列表错误显示为空。'
    }
  ],
  'query-api-1': [
    {
      id: 'DEF-005',
      severity: '严重',
      target: 'GET /api/rechecks/my',
      title: '分页总数未按当前用户统计',
      summary: 'total 字段统计了全部用户数据，与接口仅查询当前用户回检单的契约冲突。'
    }
  ],
  'query-api-2': []
}

type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

/** 为每次工作流快照补齐规划节点总数，让折叠后的标题仍能显示执行进度。 */
function withProcessStepTotal(steps: ProcessStepRecord[], total: number): ProcessStepRecord[] {
  return steps.map((step) => ({ ...step, total }))
}

// 页面与接口 execution 共用的最小结构，供 lifecycleWith 组装 activeExecutions。
type WorkbenchExecutionLike = {
  scope: string
  targetId: string
  pageId?: string
  threadId: string
  runId: string
  phase: string
  status: string
  startedAt: string
  updatedAt: string
  resourceKeys?: string[]
  pendingInteraction?: Record<string, unknown>
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

/** 判断测试用例检查卡是否确认按当前清单执行。 */
function confirmationIsYes(value: unknown): boolean {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = (value as { selected?: unknown }).selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是'
}

// 严格递增的 lifecycle revision：与 planning.ts 共用 revision.ts 的单调计数器，
// 前端 latestApplicationLifecycle 只在 revision 更大时替换；统一计数避免任何后发
// lifecycle（设计/工作台）被按 revision 拒绝合并导致 stage 冻住。
function pageMeta(pageId?: string): { id: string; label: string; path: string; purpose: string } {
  const key = pageId || 'my-rechecks'
  const meta = PAGES[key] || PAGES['my-rechecks']
  return { id: key, label: meta.label, path: meta.path, purpose: meta.purpose }
}

function workflowPageId(workflow: WorkflowRunPayload | undefined): string | undefined {
  const summary = workflow?.summary as { selectedPageId?: string } | undefined
  return summary?.selectedPageId || (workflow?.state?.selectedPageId as string | undefined)
}

// —— 接口（endpoint）目标识别 ——

// 从本次请求或 resumeState 快照解析 endpoint 目标。
// 端点确认/构建的续传消息只带 resumeState，不重复携带 selectedEndpointId，
// 因此必须同时读 workflow.state / workflow.result 中持久化的目标身份。
function resolveEndpointTarget(
  options: SendWorkflowMessageOptions,
  resume?: WorkflowRunPayload
): { apiContractId: string; endpointId: string } | undefined {
  const state = (resume?.state || {}) as Record<string, unknown>
  const result = (resume?.result || {}) as Record<string, unknown>
  const apiContractId = String(
    options.selectedApiContractId ||
      state.selectedApiContractId ||
      result.selectedApiContractId ||
      ''
  ).trim()
  const endpointId = String(
    options.selectedEndpointId || state.selectedEndpointId || result.selectedEndpointId || ''
  ).trim()
  const detailTargetType = String(
    options.detailTargetType || state.detailTargetType || result.detailTargetType || ''
  ).trim()
  // 页面任务会携带依赖接口身份；它仍属于页面工作流，不能误走独立接口剧本。
  if (detailTargetType === 'page') return undefined
  if (detailTargetType === 'endpoint' || (apiContractId && endpointId)) {
    return apiContractId && endpointId ? { apiContractId, endpointId } : undefined
  }
  return undefined
}

// 从 API 契约目录定位 endpoint，返回展示与构建所需元信息。
function endpointMeta(
  apiContractId: string,
  endpointId: string
):
  | {
      apiContractId: string
      endpointId: string
      label: string
      method: string
      path: string
      summary: string
      contractLabel: string
    }
  | undefined {
  const contract = mockPlanningArtifacts.apiContracts.find((item) => item.id === apiContractId)
  if (!contract) return undefined
  const endpoint = contract.endpoints.find(
    (item, index) => (item.id || String(index + 1)) === endpointId
  )
  if (!endpoint) return undefined
  const method = String(endpoint.method || 'GET').toUpperCase()
  const path = String(endpoint.path || '')
  return {
    apiContractId,
    endpointId,
    label: `${method} ${path}`.trim(),
    method,
    path,
    summary: String(endpoint.summary || ''),
    contractLabel: contract.label
  }
}

// 构造执行条目（驱动底部 Dock）。
function exec(
  runId: string,
  threadId: string,
  pageId: string,
  phase: string,
  status: string,
  pendingInteraction?: Record<string, unknown>
): WorkbenchExecutionLike {
  const now = new Date().toISOString()
  return {
    scope: 'page',
    targetId: pageId,
    pageId,
    threadId,
    runId,
    phase,
    status,
    startedAt: now,
    updatedAt: now,
    ...(pendingInteraction ? { pendingInteraction } : {})
  }
}

// 构造携带 execution 的 ApplicationLifecycle。
function lifecycleWith(
  base: ApplicationLifecycle,
  runId: string,
  execution: WorkbenchExecutionLike
): ApplicationLifecycle {
  return {
    ...base,
    revision: nextLifecycleRevision(),
    activeExecutions: { [runId]: execution }
  } as ApplicationLifecycle
}

// 工作台三剧本（页面/接口/审查）共享的 base ApplicationLifecycle：ready_for_workbench 初始化基底。
// 集中构造避免 application/initialization 字段在三处重复散落。
function makeBaseLifecycle(
  application: SendWorkflowMessageOptions['application']
): ApplicationLifecycle {
  return {
    schemaVersion: '1.2.0',
    application: {
      id: application?.id || 'app-pms-new',
      name: application?.appName || application?.name || '武汉分行需求回检系统'
    },
    updatedAt: new Date().toISOString(),
    revision: 1,
    initialization: { stage: 'ready_for_workbench', status: 'completed' },
    activeExecutions: {}
  } as ApplicationLifecycle
}

// 工作台三剧本共享的 emitLifecycle：lifecycleWith 组装 + 通知回调 + 注册权威 lifecycle。
// 三剧本仅 execution 来源（exec/execEndpoint/appExec）不同，合并逻辑一致。
function makeEmitLifecycle(
  baseLifecycle: ApplicationLifecycle,
  runId: string,
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
): (execution: WorkbenchExecutionLike) => ApplicationLifecycle {
  return (execution) => {
    const lifecycle = lifecycleWith(baseLifecycle, runId, execution)
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }
}

// 组装 WorkflowRunPayload：lifecycle 同时放入 summary.lifecycle / state / result（与 withWorkflowExecutionStatus 一致）。
function wf(
  threadId: string,
  runId: string,
  phase: string,
  status: string,
  lifecycle: ApplicationLifecycle | undefined,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  const payload: Record<string, unknown> = {
    runId,
    threadId,
    summary: { phase, status, message: '', ...(lifecycle ? { lifecycle } : {}) },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
    state: lifecycle ? { ...state, lifecycle } : state,
    result: lifecycle ? { lifecycle } : {},
    ...extra
  }
  return payload as unknown as WorkflowRunPayload
}

// —— 代码实现执行通道 ——
// 开发阶段前台只负责「设计与选择」：详细设计确认后由用户在「选择执行方式」节点上
// 选择同步执行（对话内当场执行）或后台资源池（异步/潮汐）。选择后台时创建
// artifact_implementation 任务，代码生成、构建/单测、页面预览由后台引擎无人值守执行到
// 「完成」，前台工作流立即收口；选择同步时由剧本在对话内播放生成节点并落同一条任务记录。

/**
 * 按所选执行方式派发代码实现任务并确保后台引擎已启动。
 * 应用与版本身份来自请求上下文；同一主产物的重复派发由所属系统幂等收敛。
 * 同步执行的记录沉淀在常规算力域（异步系统），引擎不调度，由剧本直接推进到终态。
 */
function dispatchImplementationTask(input: {
  options: SendWorkflowMessageOptions
  title: string
  artifactIds: string[]
  primaryArtifactId: string
  execTarget: BackgroundTaskExecTarget
  choice: BackgroundDispatchChoice
}): ReturnType<typeof dispatchArtifactImplementationTask> {
  const task = dispatchArtifactImplementationTask({
    applicationId: input.options.application?.id || 'app-pms-new',
    versionId: input.options.application?.currentVersionId || 'current',
    title: input.title,
    artifactIds: input.artifactIds,
    primaryArtifactId: input.primaryArtifactId,
    execTarget: input.execTarget,
    system: input.choice === 'tide' ? 'tide' : 'async'
  })
  ensureBackgroundTaskEngine()
  return task
}

/** 从续跑答案解析所选执行方式（同步执行/异步/潮汐）；用户尚未选择时返回 undefined。 */
function resolveDispatchChoice(
  answers: Record<string, unknown>
): BackgroundDispatchChoice | undefined {
  if (answers.background_dispatch === 'sync') return 'sync'
  if (answers.background_dispatch === 'tide') return 'tide'
  if (answers.background_dispatch === 'async') return 'async'
  return undefined
}

/** 执行方式选择交互：派发前挂起的待处理交互，驱动「选择执行方式」节点呈现选项。 */
function backgroundDispatchInteraction(): Record<string, unknown> {
  return {
    id: `pi-dispatch-${Date.now()}`,
    type: 'background_dispatch',
    basedOnRevision: 1,
    payload: { message: '请选择本次实现任务的执行方式。' },
    createdAt: new Date().toISOString()
  }
}

// 产物验收交互：验收工作流挂起的待处理交互，对话卡与生命周期共享同一引用才会被判定为可操作节点。
function acceptanceInteraction(): Record<string, unknown> {
  return {
    id: `pi-acceptance-${Date.now()}`,
    type: 'page_acceptance',
    basedOnRevision: 1,
    payload: { message: '产物已就绪，请在右侧预览确认后接受。' },
    createdAt: new Date().toISOString()
  }
}

/**
 * 产物验收工作流剧本：后台实现任务到达「待验收」后，由任务入口在开发主对话启动。
 * 工作流打开右侧产物预览（页面预览/接口调试）并在对话区承载验收确认；
 * 用户确认后回写后台任务为已完成，产物状态与测试依赖由统一任务流水推导。
 */
export async function replayArtifactAcceptance(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  // 验收段节点取自开发工作流底层 DAG：这是同一工作流在「任务完成后回到主对话」的表现段。
  const acceptanceNodes = workflowSegmentNodes('development', 'acceptance')
  const previewNode = acceptanceNodes.find((node) => node.id === 'acceptance_preview')!
  const confirmNode = acceptanceNodes.find((node) => node.id === 'acceptance_confirm')!
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const runId = resume?.runId || `mock-artifact-acceptance-${Date.now()}`
  const endpointTarget = resolveEndpointTarget(options, resume)
  const page = endpointTarget
    ? undefined
    : pageMeta(options.selectedPageId || workflowPageId(resume))
  const baseLifecycle = makeBaseLifecycle(options.application)
  const appId = options.application?.id || 'app-pms-new'
  const versionId = options.application?.currentVersionId || 'current'

  // 解析验收目标产物的展示名，页面与接口共用同一套确认文案。
  const targetLabel = endpointTarget
    ? `接口 ${endpointMeta(endpointTarget.apiContractId, endpointTarget.endpointId)?.label || endpointTarget.endpointId}`
    : `页面「${page?.label || ''}」`

  /** 定位本次验收关联的后台实现任务；找不到说明任务已被处理或已失效。 */
  const artifactId = endpointTarget
    ? endpointArtifactId(endpointTarget.apiContractId, endpointTarget.endpointId)
    : page
      ? pageArtifactId(page.id)
      : ''
  const task = artifactId ? findAwaitingArtifactTask(artifactId, appId, versionId) : undefined

  const identity: Record<string, unknown> = endpointTarget
    ? {
        selectedApiContractId: endpointTarget.apiContractId,
        selectedEndpointId: endpointTarget.endpointId,
        detailTargetType: 'endpoint'
      }
    : { selectedPageId: page?.id || '', detailTargetType: 'page' }

  const emit = (
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = {
      runId,
      threadId,
      summary: { phase: 'acceptance', status, message: '', ...(lifecycle ? { lifecycle } : {}) },
      events: [{ type: 'workflow.node.started', nodeName: 'acceptance' }],
      state: { ...identity, ...state, ...(lifecycle ? { lifecycle } : {}) },
      result: { ...identity, ...(lifecycle ? { lifecycle } : {}) },
      ...extra
    } as unknown as WorkflowRunPayload
    onWorkflow?.(payload)
    return payload
  }

  /** 验收执行的 lifecycle 条目：execution phase 保持开发阶段节点，避免阶段推导把工作台切到验收阶段。 */
  const emitLifecycle = (
    status: string,
    pending?: Record<string, unknown>
  ): ApplicationLifecycle => {
    const now = new Date().toISOString()
    const execution: WorkbenchExecutionLike = endpointTarget
      ? {
          scope: 'endpoint',
          targetId: endpointTarget.endpointId,
          resourceKeys: [`endpoint:${endpointTarget.apiContractId}:${endpointTarget.endpointId}`],
          threadId,
          runId,
          phase: 'launch_project',
          status,
          startedAt: now,
          updatedAt: now,
          ...(pending ? { pendingInteraction: pending } : {})
        }
      : {
          scope: 'page',
          targetId: page?.id || '',
          pageId: page?.id,
          threadId,
          runId,
          phase: 'launch_project',
          status,
          startedAt: now,
          updatedAt: now,
          ...(pending ? { pendingInteraction: pending } : {})
        }
    const lifecycle = {
      ...baseLifecycle,
      revision: nextLifecycleRevision(),
      activeExecutions: { [runId]: execution }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  // 1. 验收确认续跑：接受后回写后台任务为已完成，确认节点沿同 id 落成已完成，工作流收口。
  if (answers.page_acceptance) {
    if (task) acceptArtifactTask(task.id)
    onProcessSteps?.(
      withProcessStepTotal(
        [
          {
            id: previewNode.id,
            kind: 'workflow',
            status: 'completed',
            title: previewNode.title,
            detail: previewNode.detail,
            sequence: 1
          },
          {
            id: confirmNode.id,
            kind: 'workflow',
            status: 'completed',
            title: confirmNode.title,
            detail: '产物已确认交付。',
            sequence: 2
          }
        ],
        acceptanceNodes.length
      )
    )
    return emit(
      'completed',
      emitLifecycle('completed'),
      {},
      {
        summary: { phase: 'acceptance', status: 'completed', message: '产物验收通过' }
      }
    )
  }

  // 2. 任务不存在：已被验收或状态已变化，直接给终态避免悬挂的确认卡。
  if (!task) {
    onContent?.(`${targetLabel}当前没有待验收的实现任务。`)
    return emit(
      'completed',
      emitLifecycle('completed'),
      {},
      {
        summary: { phase: 'acceptance', status: 'completed', message: '无待验收任务' }
      }
    )
  }

  // 3. 启动验收：右侧打开产物预览（预览节点先执行），随后挂起验收确认节点。
  onProcessSteps?.(
    withProcessStepTotal(
      [
        {
          id: previewNode.id,
          kind: 'workflow',
          status: 'running',
          title: previewNode.title,
          detail: previewNode.detail,
          sequence: 1
        }
      ],
      acceptanceNodes.length
    )
  )
  await delay(500)
  onProcessSteps?.(
    withProcessStepTotal(
      [
        {
          id: previewNode.id,
          kind: 'workflow',
          status: 'completed',
          title: previewNode.title,
          detail: previewNode.detail,
          sequence: 1
        },
        {
          id: confirmNode.id,
          kind: 'workflow',
          status: 'requires_user_input',
          title: confirmNode.title,
          detail: confirmNode.detail,
          sequence: 2
        }
      ],
      acceptanceNodes.length
    )
  )
  return emit(
    'requires_user_input',
    emitLifecycle('awaiting_user', acceptanceInteraction()),
    {
      clarification: {
        mode: 'page_acceptance',
        status: 'requires_user_input',
        message: `请在右侧预览中确认${targetLabel}的实现内容，确认后接受产物。`,
        questions: []
      }
    },
    {
      summary: { phase: 'acceptance', status: 'requires_user_input', message: '等待产物验收' }
    }
  )
}

// 审查阶段检查矩阵（规范 / 安全 / 健康度 三项通过）。
function reviewChecks(): unknown[] {
  return [
    {
      id: 'check-lint',
      name: '代码规范检测',
      status: 'passed',
      required: true,
      evidence: '命名约定、模块结构与重复代码扫描通过。'
    },
    {
      id: 'check-security',
      name: '安全扫描',
      status: 'passed',
      required: true,
      evidence: '未发现硬编码密钥、越权访问与注入风险。'
    },
    {
      id: 'check-health',
      name: '健康度评估',
      status: 'passed',
      required: true,
      evidence: '圈复杂度正常 / 重复率 0.8% / 单测覆盖 82%。'
    }
  ]
}

// —— 构建节点的代码变更集（审查阶段报告写入仍复用单文件 Diff 载荷）——

// 开发阶段的代码 Diff 由后台任务承载后，工作台剧本只剩审查报告使用单文件变更集。
type BuildFileTarget = {
  key: string
  name: string
  path: string
  content: string
  sourceTool: string
}

// 把生成的完整文件内容包装成新增文件的行级 Diff（bare diff 由前端自动补统一格式头）。
function addedFileChange(
  id: string,
  path: string,
  content: string,
  sourceTool: string
): WorkspaceCodeChangeFile {
  const lines = content.split('\n')
  return {
    id,
    path,
    changeType: 'added',
    additions: lines.length,
    deletions: 0,
    diff: lines.map((line) => `+${line}`).join('\n'),
    tool: 'file.write',
    sourceTool,
    executed: true
  }
}

// 组装单文件变更集：id 带序号，右侧页签按 id 变化原地刷新写入进度。
function singleFileChangeSet(
  runId: string,
  target: BuildFileTarget,
  content: string
): WorkspaceCodeChangeSet {
  const change = addedFileChange(
    `cc-${runId}-${target.key}`,
    target.path,
    content,
    target.sourceTool
  )
  return {
    id: `cc-${runId}-${target.key}-${change.additions}`,
    status: 'applied',
    workspaceRoot: appDataByWorkspace().workspaceRoot,
    summary: { files: 1, additions: change.additions, deletions: 0 },
    files: [change]
  }
}

/**
 * 生成同步执行的代码交付目标：页面任务一次交付页面源码（含依赖接口时一并交付后端源码）。
 * 页面文件排在首位，右侧源码区默认展示页面 Diff。
 */
function buildFileTargets(pageId: string, includeEndpoint: boolean): BuildFileTarget[] {
  const scenario = appDataByWorkspace()
  const targets: BuildFileTarget[] = []
  const pageDesign =
    (scenario.designedPageDesigns[pageId] as PageDesign | undefined) ||
    (scenario.pageDesigns[pageId] as PageDesign | undefined)
  if (pageDesign) {
    const source = buildPageSource(pageDesign, pageId)
    targets.push({
      key: 'page',
      name: source.filePath.split('/').pop() || 'index.tsx',
      path: appPath(source.filePath),
      content: source.content,
      sourceTool: 'page_generator'
    })
  }
  if (includeEndpoint) {
    const endpointDesign = scenario.endpointDesigns['ep-my-rechecks'] as Record<string, unknown>
    if (endpointDesign) {
      const source = buildEndpointSource(endpointDesign)
      targets.push({
        key: 'controller',
        name: source.filePath.split('/').pop() || 'Controller.java',
        path: appPath(source.filePath),
        content: source.content,
        sourceTool: 'backend_code_generator'
      })
    }
  }
  return targets
}

/** 生成独立接口会话的代码交付目标，与页面依赖接口使用同一套源码生成逻辑。 */
function endpointBuildTargets(apiContractId: string, endpointId: string): BuildFileTarget[] {
  const endpointDesign = appDataByWorkspace().endpointDesigns[endpointId] as
    | Record<string, unknown>
    | undefined
  if (!endpointDesign) return []
  const source = buildEndpointSource(endpointDesign)
  return [
    {
      key: `endpoint-${apiContractId}-${endpointId}`,
      name: source.filePath.split('/').pop() || 'Controller.java',
      path: appPath(source.filePath),
      content: source.content,
      sourceTool: 'backend_code_generator'
    }
  ]
}

type ChangeSource = { target: BuildFileTarget; content: string }

/** 按内容源组装变更集：id 随已生成行数变化，右侧页签按 id 原地刷新写入进度。 */
function changeSetFromContents(runId: string, sources: ChangeSource[]): WorkspaceCodeChangeSet {
  const changes = sources.map(({ target, content }, index) =>
    addedFileChange(`cc-${runId}-${target.key}-${index}`, target.path, content, target.sourceTool)
  )
  const additions = changes.reduce((total, change) => total + change.additions, 0)
  return {
    id: `cc-${runId}-${changes.length}f-${additions}`,
    status: 'applied',
    workspaceRoot: appDataByWorkspace().workspaceRoot,
    summary: { files: changes.length, additions, deletions: 0 },
    files: changes
  }
}

/** 同步执行的最终交付变更集：全部目标文件的完整内容。 */
function fullChangeSet(runId: string, targets: BuildFileTarget[]): WorkspaceCodeChangeSet {
  return changeSetFromContents(
    runId,
    targets.map((target) => ({ target, content: target.content }))
  )
}

/** 同步执行的代码变更确认交互：右侧源码区展示 Diff，接受后继续构建。 */
function fileAcceptanceInteraction(): Record<string, unknown> {
  return {
    id: `pi-file-acceptance-${Date.now()}`,
    type: 'file_acceptance',
    basedOnRevision: 1,
    payload: { message: '代码已生成，请在右侧确认 Diff 后接受。' },
    createdAt: new Date().toISOString()
  }
}

// 构造接口执行的底部 Dock 条目（scope='endpoint' + resourceKeys 供 planExecutionContextForEndpoint 匹配）。
function execEndpoint(
  runId: string,
  threadId: string,
  apiContractId: string,
  endpointId: string,
  phase: string,
  status: string,
  pendingInteraction?: Record<string, unknown>
): WorkbenchExecutionLike {
  const now = new Date().toISOString()
  return {
    scope: 'endpoint',
    targetId: endpointId,
    resourceKeys: [`endpoint:${apiContractId}:${endpointId}`],
    threadId,
    runId,
    phase,
    status,
    startedAt: now,
    updatedAt: now,
    ...(pendingInteraction ? { pendingInteraction } : {})
  }
}

// 组装接口 WorkflowRunPayload：state/result 持久化 endpoint 身份，
// 供续传消息（只带 resumeState）恢复目标与 chatSessions 保存会话归属。
function endpointWf(
  threadId: string,
  runId: string,
  phase: string,
  status: string,
  apiContractId: string,
  endpointId: string,
  detailTargetType: string,
  lifecycle: ApplicationLifecycle | undefined,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  const identity = {
    selectedApiContractId: apiContractId,
    selectedEndpointId: endpointId,
    detailTargetType
  }
  const payload: Record<string, unknown> = {
    runId,
    threadId,
    summary: { phase, status, message: '', ...(lifecycle ? { lifecycle } : {}) },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
    state: { ...state, ...identity, ...(lifecycle ? { lifecycle } : {}) },
    result: { ...identity, ...(lifecycle ? { lifecycle } : {}) },
    ...extra
  }
  return payload as unknown as WorkflowRunPayload
}

// 接口（endpoint）工作台剧本：接口详细设计 → 详情审阅确认 → 幂等派发后台实现任务。
// 与页面剧本共用 lifecycle/revision 机制，但 scope='endpoint'；
// 代码生成、构建检查与启动预览统一由后台实现任务无人值守执行。
async function replayEndpointWorkbench(
  threadId: string,
  target: { apiContractId: string; endpointId: string },
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-endpoint-${Date.now()}`
  const meta = endpointMeta(target.apiContractId, target.endpointId) || {
    apiContractId: target.apiContractId,
    endpointId: target.endpointId,
    label: target.endpointId,
    method: 'GET',
    path: '/api/unknown',
    summary: '',
    contractLabel: ''
  }
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>

  // 产物验收工作流的续跑：验收确认由独立验收剧本承载，不能落回接口实现剧本。
  if (answers.page_acceptance) {
    return replayArtifactAcceptance(threadId, options, callbacks)
  }

  const baseLifecycle = makeBaseLifecycle(options.application)

  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = endpointWf(
      threadId,
      runId,
      phase,
      status,
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      lifecycle,
      state,
      extra
    )
    onWorkflow?.(payload)
    return payload
  }

  // 1. 详情审阅确认（或续跑）→ 在「选择执行方式」节点上选择同步执行或后台资源池。
  //    同步执行由剧本在对话内当场播放生成节点并落同一条任务记录；
  //    异步/潮汐派发后台任务后前台立即收口，执行到「完成」由后台引擎推进。
  if (answers.detail_review || resume) {
    const choice = resolveDispatchChoice(answers)
    const artifactId = endpointArtifactId(meta.apiContractId, meta.endpointId)
    // 接口分支与页面分支在同一张 DAG 上汇聚到「选择执行方式」节点。
    const executionNode = workflowNode('development', 'choose_execution_endpoint')
    const choiceStep = (
      status: ProcessStepRecord['status'],
      detail: string
    ): ProcessStepRecord => ({
      id: executionNode.id,
      kind: 'workflow',
      status,
      title: executionNode.title,
      detail,
      sequence: 1
    })
    if (!choice) {
      // 尚未选择执行方式：轨迹追加待输入节点，由用户决定同步执行或进入哪个任务系统。
      onProcessSteps?.([choiceStep('requires_user_input', executionNode.detail)])
      return emit(
        'prepare_build_tasks',
        'requires_user_input',
        emitLifecycle(
          execEndpoint(
            runId,
            threadId,
            meta.apiContractId,
            meta.endpointId,
            'prepare_build_tasks',
            'awaiting_user',
            backgroundDispatchInteraction()
          )
        ),
        {
          clarification: {
            mode: 'background_dispatch',
            status: 'requires_user_input',
            message: '请选择本次接口实现任务的执行方式，选择后按所选通道执行。',
            questions: []
          }
        },
        {
          summary: {
            phase: 'prepare_build_tasks',
            status: 'requires_user_input',
            message: '等待选择执行方式'
          }
        }
      )
    }
    /** 同步执行：在对话内按阶段播放接口实现过程；同步不进任务池，产物状态由工作流与已保存文件推导。 */
    const syncImplementEndpoint = async (): Promise<WorkflowRunPayload> => {
      onProcessSteps?.([choiceStep('completed', '已选择同步任务，任务在当前对话中直接执行。')])
      // 前台构建段节点取自开发工作流底层 DAG；接口目标下的 detail 以接口口径覆盖。
      const foregroundDetailOverrides: Record<string, string> = {
        generate_code: '按契约生成接口实现与数据访问代码。'
      }
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const generateNode = foregroundNodes.find((node) => node.id === 'generate_code')!
      const buildTargets = endpointBuildTargets(meta.apiContractId, meta.endpointId)
      const endpointDetail = (nodeId: string): string =>
        foregroundDetailOverrides[nodeId] ||
        foregroundNodes.find((node) => node.id === nodeId)!.detail
      // 生成构建计划属于后台分析动作，不在对话轨迹中展示；直接进入生成代码。
      emit(
        'build_dag',
        'running',
        emitLifecycle(
          execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build_dag', 'running')
        )
      )
      await delay(900)
      // 生成代码：按行分帧渐进写入 Diff，模拟一段一段生成的过程；任务中心同步显示生成中。
      onProcessSteps?.([
        {
          id: generateNode.id,
          kind: 'workflow',
          status: 'running',
          title: generateNode.title,
          detail: endpointDetail(generateNode.id),
          sequence: 1
        }
      ])
      const generateLifecycle = emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'generate_code',
          'running'
        )
      )
      const finishedSources: ChangeSource[] = []
      for (const target of buildTargets) {
        const lines = target.content.split('\n')
        for (let visible = 8; ; visible += 8) {
          await delay(400)
          emit('generate_code', 'running', generateLifecycle, {
            codeChanges: changeSetFromContents(runId, [
              ...finishedSources,
              { target, content: lines.slice(0, visible).join('\n') }
            ])
          })
          if (visible >= lines.length) break
        }
        finishedSources.push({ target, content: target.content })
      }
      onProcessSteps?.([
        {
          id: generateNode.id,
          kind: 'workflow',
          status: 'completed',
          title: generateNode.title,
          detail: endpointDetail(generateNode.id),
          sequence: 1
        }
      ])
      // 生成代码完成：携带代码变更集，挂「确认代码变更」待输入节点（右侧源码区打开 Diff）。
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      onProcessSteps?.([
        {
          id: confirmNode.id,
          kind: 'workflow',
          status: 'requires_user_input',
          title: confirmNode.title,
          detail: confirmNode.detail,
          sequence: 1
        }
      ])
      return emit(
        'build',
        'requires_user_input',
        emitLifecycle(
          execEndpoint(
            runId,
            threadId,
            meta.apiContractId,
            meta.endpointId,
            'build',
            'awaiting_user',
            fileAcceptanceInteraction()
          )
        ),
        {
          clarification: {
            mode: 'file_acceptance',
            status: 'requires_user_input',
            message: '接口代码已生成，请在右侧确认 Diff 后接受。'
          },
          codeChanges: fullChangeSet(runId, buildTargets)
        },
        {
          summary: {
            phase: 'build',
            status: 'requires_user_input',
            message: '等待确认代码变更'
          }
        }
      )
    }
    if (choice === 'sync') return syncImplementEndpoint()
    // 接口同步执行的代码变更确认续跑：接受 Diff 后补播构建检查，并落任务终态。
    if (answers.file_acceptance && resume) {
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const buildNode = foregroundNodes.find((node) => node.id === 'build_and_test')!
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      onProcessSteps?.([
        {
          id: confirmNode.id,
          kind: 'workflow',
          status: 'completed',
          title: confirmNode.title,
          detail: '已接受本次生成的代码变更，继续构建。',
          sequence: 1
        }
      ])
      onProcessSteps?.([
        {
          id: buildNode.id,
          kind: 'workflow',
          status: 'running',
          title: buildNode.title,
          detail: buildNode.detail,
          sequence: 1
        }
      ])
      emit(
        'build_and_test',
        'running',
        emitLifecycle(
          execEndpoint(
            runId,
            threadId,
            meta.apiContractId,
            meta.endpointId,
            'build_and_test',
            'running'
          )
        )
      )
      await delay(1300)
      onProcessSteps?.([
        {
          id: buildNode.id,
          kind: 'workflow',
          status: 'completed',
          title: buildNode.title,
          detail: buildNode.detail,
          sequence: 1
        }
      ])
      // 代码变更已在对话内确认：同步交付当场完毕，不产生待验收状态；
      // 产物状态由已保存文件快照与工作流推导。
      // 设计确认即视为「已设计」：主对话可立即继续设计下一个页面或接口。
      markEndpointDesigned(meta.apiContractId, meta.endpointId)
      return emit(
        'build',
        'completed',
        emitLifecycle(
          execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'completed')
        ),
        {},
        { summary: { phase: 'build', status: 'completed', message: '接口实现已完成' } }
      )
    }
    dispatchImplementationTask({
      options,
      title: `接口 ${meta.label} 代码实现`,
      artifactIds: [artifactId],
      primaryArtifactId: artifactId,
      execTarget: {
        type: 'endpoint',
        apiContractId: meta.apiContractId,
        endpointId: meta.endpointId
      },
      choice: choice
    })
    // 设计确认即视为「已设计」：主对话可立即继续设计下一个页面或接口。
    markEndpointDesigned(meta.apiContractId, meta.endpointId)
    // 选择节点落成已完成，再追加派发收口节点：合并回话按 id 归位并接在同一轨迹末尾。
    const dispatchNode = workflowNode('development', 'background_dispatch')
    onProcessSteps?.([
      choiceStep(
        'completed',
        `已选择${BACKGROUND_TASK_SYSTEM_LABEL[choice]}，任务进入对应后台队列执行。`
      ),
      {
        id: dispatchNode.id,
        kind: 'workflow',
        status: 'completed',
        title: dispatchNode.title,
        detail: `已创建后台接口实现任务（${BACKGROUND_TASK_SYSTEM_LABEL[choice]}），可在对应任务系统查看执行进度。`,
        sequence: 1
      }
    ])
    return emit(
      'build',
      'completed',
      emitLifecycle(
        execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'completed')
      ),
      {},
      { summary: { phase: 'build', status: 'completed', message: '接口实现已转入后台执行' } }
    )
  }

  // 4. 开始接口详细设计 → 接口详情审阅。
  if (options.selectedEndpointId || options.detailTargetType) {
    // 接口设计分支与页面设计分支在同一张 DAG 上交织，共享后续的选择执行与执行链节点。
    const endpointDesignNodes = workflowSegmentNodes('development', 'endpoint_design')
    const designSteps: ProcessStepRecord[] = []
    for (let index = 0; index < endpointDesignNodes.length; index += 1) {
      await delay(360)
      designSteps.push({
        id: endpointDesignNodes[index].id,
        kind: 'workflow',
        status: 'completed',
        title: endpointDesignNodes[index].title,
        detail: endpointDesignNodes[index].detail,
        sequence: index + 1
      })
      onProcessSteps?.(withProcessStepTotal([...designSteps], endpointDesignNodes.length + 1))
    }
    const designProcessSteps = [...designSteps]
    const endpointWorkflowTotal = endpointDesignNodes.length + 1
    return replayEndpointWorkbench(
      threadId,
      target,
      {
        ...options,
        clarificationAnswers: {
          ...(options.clarificationAnswers || {}),
          detail_review: { review_status: 'confirmed', target_changes: [] }
        }
      },
      {
        ...callbacks,
        onProcessSteps: (nextSteps) => {
          onProcessSteps?.(
            withProcessStepTotal(
              [
                ...designProcessSteps,
                ...nextSteps.map((step, index) => ({
                  ...step,
                  sequence: designProcessSteps.length + index + 1
                }))
              ],
              endpointWorkflowTotal
            )
          )
        }
      }
    )
  }

  // 5. 其它 → 最小 running 态。
  const fallback = emitLifecycle(
    execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running')
  )
  return endpointWf(
    threadId,
    runId,
    'build',
    'running',
    meta.apiContractId,
    meta.endpointId,
    'endpoint',
    fallback
  )
}

/** 所有页面/API完成后进入应用概览，启动完整预览并等待用户进行应用级验收。 */
export async function replayApplicationAcceptance(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-application-acceptance-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const appName =
    options.application?.appName || options.application?.name || '武汉分行需求回检系统'

  /** 生成应用级验收执行状态，供阶段门禁识别。 */
  const appExecution = (
    status: string,
    pending?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'acceptance',
      status,
      startedAt: now,
      updatedAt: now,
      ...(pending ? { pendingInteraction: pending } : {})
    }
  }

  /** 发布应用级 lifecycle，并注册为浏览器 mock 的最新事实。 */
  const emitLifecycle = (
    status: string,
    pending?: Record<string, unknown>
  ): ApplicationLifecycle => {
    // 验收阶段快照必须携带前置测试/审查门禁，验收通过后生成版本才能连续放行。
    const lifecycle = {
      schemaVersion: '1.2.0',
      application: { id: appId, name: appName },
      updatedAt: new Date().toISOString(),
      revision: nextLifecycleRevision(),
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      extensions: {
        testExecutionStatus: 'passed',
        testCasesCompleted: MOCK_BUSINESS_TEST_CASE_TOTAL,
        testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL,
        reviewStatus: 'passed',
        acceptanceStatus: status === 'completed' ? 'passed' : 'pending',
        phaseValidity: {
          analysis: 'valid',
          planning: 'valid',
          development: 'valid',
          testing: 'valid',
          review: 'valid',
          acceptance: 'valid'
        }
      },
      activeExecutions: { [runId]: appExecution(status, pending) }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  /** 同步应用验收 Workflow 投影到对话消息。 */
  const emit = (
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, 'acceptance', status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  // 返回对话后的验收反馈只复刻到验收会话，先由产品 Agent 提示用户继续补充问题。
  if (options.acceptanceFeedback) {
    onContent?.('请告诉我验收中发现的问题，或补充具体验收意见。')
    return emit(
      'completed',
      resume?.summary?.lifecycle as ApplicationLifecycle | undefined,
      {},
      {
        summary: {
          phase: 'acceptance',
          status: 'completed',
          message: '已进入验收对话，等待补充意见'
        }
      }
    )
  }

  if (answers.application_acceptance) {
    onContent?.('应用已依据需求文档基线验收通过，可以继续生成版本。')
    const completed = emitLifecycle('completed')
    return emit(
      'completed',
      completed,
      {},
      {
        summary: { phase: 'acceptance', status: 'completed', message: '应用验收通过' }
      }
    )
  }

  const pending = {
    id: `pi-application-acceptance-${Date.now()}`,
    type: 'application_acceptance',
    basedOnRevision: 1,
    payload: { message: '请根据需求文档基线在右侧应用预览中完成验收。' },
    createdAt: new Date().toISOString()
  }
  const lifecycle = emitLifecycle('awaiting_user', pending)
  onContent?.('请根据需求文档基线完成应用验收。')
  return emit(
    'requires_user_input',
    lifecycle,
    {
      clarification: {
        mode: 'application_acceptance',
        status: 'requires_user_input',
        message: '请根据需求文档基线完成应用验收。',
        questions: []
      }
    },
    {
      result: { preview_url: MOCK_APPLICATION_PREVIEW_URL },
      summary: { phase: 'acceptance', status: 'requires_user_input', message: '等待应用验收' }
    }
  )
}

/** 测试阶段剧本：先完成非功测试，再按用例顺序确认并执行对应的用例测试 Workflow。 */
export async function replayApplicationTesting(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const clarification = (resume?.state?.clarification ?? resume?.result?.clarification ?? {}) as {
    mode?: string
  }
  const runId = `mock-application-testing-${Date.now()}`
  const appId = options.application?.id || 'app-pms-new'
  const baseLifecycle = {
    ...makeBaseLifecycle(options.application),
    extensions: {
      testExecutionStatus: 'running',
      testCasesCompleted: 0,
      testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL
    }
  } as ApplicationLifecycle
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  /** 发送当前子 Workflow 的独立步骤，避免非功测试与用例测试互相复制。 */
  const publishWorkflowSteps = (steps: ProcessStepRecord[]): void => {
    onProcessSteps?.(withProcessStepTotal(steps, steps.length))
  }
  let latestCaseWorkflow: WorkflowRunPayload | undefined

  /** 读取用例在后台生成队列中的状态；镜像缺失时返回 unknown，由调用方退化为直接执行。 */
  const readGenerationStatus = (caseId: string): 'ready' | 'waiting' | 'unknown' => {
    // 生成状态由统一后台任务流水承载；队列缺失时退化为直接执行，避免演示卡死。
    return readTestCaseTaskStatus(caseId)
  }

  /**
   * 等待指定用例在后台生成完成。已测完的工作流必须先落定，等待也不能是空窗：
   * 以一条独立的“用例生成检查”工作流承接等待过程，首个节点即检查用例生成情况，
   * 用例就绪后该工作流结束，随后才允许出现执行确认节点。
   */
  const waitForCaseGeneration = async (
    testCase: (typeof MOCK_TEST_CASES)[number],
    executionState: {
      completed: number
      defects: Record<string, TestCaseDefect[]>
      results: Record<string, 'pending' | 'running' | 'passed' | 'failed'>
    }
  ): Promise<ProcessStepRecord> => {
    const initialStep: ProcessStepRecord = {
      id: `case-generation-check-${testCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: '检查用例生成情况',
      detail: '正在检查当前用例是否已由后台生成完成。',
      sequence: 1
    }
    // 生成检查是当前用例 Workflow 的第一个节点，不能另起“测试验证工作流”。
    // 这样检查、授权和执行始终留在同一张用例工作流卡里。
    if (readGenerationStatus(testCase.id) !== 'waiting') {
      return {
        ...initialStep,
        status: 'completed',
        detail: '用例已生成完成，可以进入执行确认。'
      }
    }
    emitCaseWorkflow(
      testCase,
      executionState.completed,
      executionState.results,
      executionState.defects,
      'running',
      undefined,
      { activeCaseId: null, summaryMessage: '正在检查测试用例生成情况' }
    )
    let checkStep = initialStep
    publishWorkflowSteps([checkStep])
    for (let attempt = 0; attempt < 600; attempt += 1) {
      if (readGenerationStatus(testCase.id) !== 'waiting') break
      await delay(800)
      if (readGenerationStatus(testCase.id) !== 'waiting') break
      // 每约 1.6 秒刷新一次后台生成进度，让等待节点保持持续推进的观感。
      if (attempt % 2 === 1) {
        const caseTasks = getBackgroundTasks().filter(
          (item) => item.kind === 'test_case_generation'
        )
        const readyCount = caseTasks.filter((item) => item.status === 'completed').length
        checkStep = {
          ...checkStep,
          detail: `后台已生成 ${readyCount}/${caseTasks.length || readyCount} 条，当前用例仍在生成队列中。`
        }
        publishWorkflowSteps([checkStep])
      }
    }
    return {
      ...checkStep,
      status: 'completed',
      detail: '用例已生成完成，进入执行确认。'
    }
  }

  /** 发布用例测试 DAG 的进度；所有用例、缺陷、修复和复测均投影到同一条 Workflow。 */
  const emitCaseWorkflow = (
    testCase: (typeof MOCK_TEST_CASES)[number],
    completed: number,
    results: Record<string, 'pending' | 'running' | 'passed' | 'failed'>,
    defects: Record<string, TestCaseDefect[]>,
    status: 'running' | 'completed' | 'requires_user_input',
    clarificationState?: Record<string, unknown>,
    display?: { activeCaseId?: string | null; summaryMessage?: string }
  ): WorkflowRunPayload => {
    const activeCaseId =
      display?.activeCaseId === undefined
        ? status === 'completed'
          ? undefined
          : testCase.id
        : display.activeCaseId || undefined
    const lifecycleBase = {
      ...baseLifecycle,
      extensions: {
        ...(baseLifecycle.extensions || {}),
        // 测试阶段进入当前用例的授权节点后即算进行中；“待确认”只描述当前节点。
        testExecutionStatus:
          status === 'completed' && completed === MOCK_BUSINESS_TEST_CASE_TOTAL
            ? 'passed'
            : 'running',
        testCasesCompleted: completed,
        testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL,
        activeCaseId,
        testCaseResults: results,
        testCaseDefects: defects
      }
    } as ApplicationLifecycle
    const lifecycle = makeEmitLifecycle(
      lifecycleBase,
      runId,
      onApplicationLifecycle
    )({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'business_test',
      status: status === 'requires_user_input' ? 'awaiting_user' : status,
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString(),
      ...(status === 'requires_user_input'
        ? {
            // 对话卡和生命周期共享同一待处理交互，卡片才会被判定为当前可操作节点。
            pendingInteraction: {
              id: `test-case-confirm-${testCase.id}`,
              type: 'test_case_execute',
              basedOnRevision: 1,
              payload: { message: `请确认是否执行用例：${testCase.title}` },
              artifactRefs: [{ testCaseId: testCase.id }],
              createdAt: new Date().toISOString()
            }
          }
        : {})
    })
    latestCaseWorkflow = emit(
      'business_test',
      status,
      lifecycle,
      {
        testWorkflowKey: `case:${testCase.id}`,
        testWorkflowType: 'case',
        testCaseId: testCase.id,
        testCaseLabel: testCase.title,
        ...(clarificationState ? { clarification: clarificationState } : {}),
        testExecution: {
          completed,
          defects,
          results,
          status: lifecycle.extensions?.testExecutionStatus,
          total: MOCK_BUSINESS_TEST_CASE_TOTAL
        }
      },
      {
        summary: {
          phase: 'business_test',
          status,
          message:
            display?.summaryMessage ||
            (status === 'requires_user_input'
              ? `请确认是否执行用例：${testCase.title}`
              : status === 'running'
                ? `正在执行用例：${testCase.title}`
                : '该用例测试工作流已完成。')
        }
      }
    )
    return latestCaseWorkflow as WorkflowRunPayload
  }

  // 第一条 Workflow：启动与非功能检查，不与具体业务用例混在一起；续跑用例确认时不重复执行。
  const nonFunctionalSteps: ProcessStepRecord[] = [
    {
      id: 'startup-test',
      kind: 'workflow',
      status: 'running',
      title: '启动测试',
      detail: '启动应用并检查主路由、页面入口和基础运行环境。',
      sequence: 1
    },
    {
      id: 'non-functional-test',
      kind: 'workflow',
      status: 'pending',
      title: '非功能测试',
      detail: '检查异常反馈、响应稳定性和恢复路径。',
      sequence: 2
    }
  ]
  if (!resume) {
    const nonFunctionalLifecycle = emitLifecycle({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'application_test',
      status: 'running',
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    })
    emit(
      'application_test',
      'running',
      nonFunctionalLifecycle,
      {
        testWorkflowKey: 'non-functional',
        testWorkflowType: 'non-functional'
      },
      {
        summary: { phase: 'application_test', status: 'running', message: '正在执行非功测试工作流' }
      }
    )
    publishWorkflowSteps(nonFunctionalSteps)
    await delay(700)
    nonFunctionalSteps[0] = {
      ...nonFunctionalSteps[0],
      status: 'completed',
      detail: '应用已启动，主路由和页面入口可以访问。'
    }
    nonFunctionalSteps[1] = { ...nonFunctionalSteps[1], status: 'running' }
    publishWorkflowSteps(nonFunctionalSteps)
    await delay(700)
    nonFunctionalSteps[1] = {
      ...nonFunctionalSteps[1],
      status: 'completed',
      detail: '异常反馈、响应稳定性和恢复路径均通过。'
    }
    publishWorkflowSteps(nonFunctionalSteps)
    // 节点完成后必须额外发送 Workflow 终态，否则前端会继续把整张卡识别为运行中。
    const completedNonFunctionalLifecycle = emitLifecycle({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'application_test',
      status: 'completed',
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    })
    emit(
      'application_test',
      'completed',
      completedNonFunctionalLifecycle,
      {
        testWorkflowKey: 'non-functional',
        testWorkflowType: 'non-functional'
      },
      {
        summary: {
          phase: 'application_test',
          status: 'completed',
          message: '非功测试工作流已完成。'
        }
      }
    )
  }

  // 第二类 Workflow：每条用例对应一条用例测试 Workflow，按目录顺序串行推进。
  const caseResults: Record<string, 'pending' | 'running' | 'passed' | 'failed'> = {}
  for (const testCase of MOCK_TEST_CASES) caseResults[testCase.id] = 'pending'
  const defects: Record<string, TestCaseDefect[]> = {}
  const persistedExecution = (resume?.state?.testExecution || resume?.result?.testExecution) as
    | {
        completed?: unknown
        defects?: unknown
        results?: unknown
      }
    | undefined
  if (persistedExecution?.results && typeof persistedExecution.results === 'object') {
    Object.entries(persistedExecution.results as Record<string, unknown>).forEach(
      ([caseId, result]) => {
        if (['pending', 'running', 'passed', 'failed'].includes(String(result))) {
          caseResults[caseId] = result as (typeof caseResults)[string]
        }
      }
    )
  }
  if (persistedExecution?.defects && typeof persistedExecution.defects === 'object') {
    Object.entries(persistedExecution.defects as Record<string, unknown>).forEach(
      ([caseId, value]) => {
        if (!Array.isArray(value)) return
        const normalized = value.flatMap((item): TestCaseDefect[] => {
          if (!item || typeof item !== 'object') return []
          const defect = item as Record<string, unknown>
          if (!defect.id) return []
          return [
            {
              id: String(defect.id),
              severity: String(defect.severity) === '严重' ? '严重' : '一般',
              target: String(defect.target || '当前用例关联产物'),
              title: String(defect.title || '测试缺陷'),
              summary: String(defect.summary || ''),
              status: ['open', 'repairing', 'resolved'].includes(String(defect.status))
                ? (String(defect.status) as TestCaseDefect['status'])
                : 'open'
            }
          ]
        })
        if (normalized.length > 0) defects[caseId] = normalized
      }
    )
  }
  let completed = Number(persistedExecution?.completed || 0)
  if (!Number.isFinite(completed) || completed < 0)
    completed = Object.values(caseResults).filter((result) => result === 'passed').length

  /** 展示单条用例的执行确认节点，确认后才启动该条用例测试 Workflow。 */
  const showCaseConfirmation = async (
    testCase: (typeof MOCK_TEST_CASES)[number]
  ): Promise<WorkflowRunPayload> => {
    // 用例尚未生成完成时不能执行：由“检查用例生成情况”工作流承接等待，就绪后才进入确认。
    const generationCheckStep = await waitForCaseGeneration(testCase, {
      completed,
      defects: { ...defects },
      results: { ...caseResults }
    })
    // 授权卡出现即表示该条用例 Workflow 已启动，目录状态先进入紫色“进行中”。
    caseResults[testCase.id] = 'running'
    const workflow = emitCaseWorkflow(
      testCase,
      completed,
      { ...caseResults },
      { ...defects },
      'requires_user_input',
      {
        mode: 'test_case_execute',
        status: 'requires_user_input',
        questions: [
          {
            id: 'confirm_test_case',
            header: '测试用例确认',
            type: 'yesno',
            question: `用例“${testCase.title}”已准备完成，是否执行？`,
            allowOther: false
          }
        ]
      }
    )
    onProcessSteps?.(
      withProcessStepTotal(
        [
          generationCheckStep,
          {
            id: `case-confirm-${testCase.id}`,
            kind: 'workflow',
            status: 'requires_user_input',
            title: '确认执行用例',
            detail: '请查看当前用例内容，并确认是否执行。',
            sequence: 2
          }
        ],
        2
      )
    )
    return workflow
  }

  const resumeCaseId = String(resume?.state?.testCaseId || resume?.result?.testCaseId || '')
  const requestedCaseIndex = MOCK_TEST_CASES.findIndex((testCase) => testCase.id === resumeCaseId)
  const currentCaseIndex =
    requestedCaseIndex >= 0
      ? requestedCaseIndex
      : Math.min(MOCK_TEST_CASES.length - 1, Math.max(0, completed))
  const currentCase = MOCK_TEST_CASES[currentCaseIndex]
  const confirmedCurrentCase = confirmationIsYes(answers.confirm_test_case)
  // 当前测试契约只允许从“本条用例是否执行”的确认节点续跑；
  // 不再兼容旧的“汇总用例后统一确认”模式，避免把六条用例压成一条 Workflow。
  if (!resume || clarification.mode === 'test_case_execute') {
    if (!confirmedCurrentCase) return showCaseConfirmation(currentCase)
  }

  const caseWorkflowSteps: ProcessStepRecord[] = [
    {
      id: `case-confirm-${currentCase.id}`,
      kind: 'workflow',
      status: 'completed',
      title: '确认执行用例',
      detail: '用户已确认执行当前用例。',
      sequence: 1
    },
    {
      id: `case-test-${currentCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: `执行用例：${currentCase.title}`,
      detail: `正在执行 ${currentCase.id.toUpperCase()}。`,
      sequence: 2
    },
    {
      id: `case-defects-${currentCase.id}`,
      kind: 'workflow',
      status: 'pending',
      title: '生成缺陷清单',
      detail: '等待测试脚本执行完成后汇总缺陷。',
      sequence: 3
    }
  ]
  caseResults[currentCase.id] = 'running'
  emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
  publishWorkflowSteps([...caseWorkflowSteps])
  await delay(520)

  const scriptedDefects = (MOCK_TEST_CASE_DEFECTS[currentCase.id] || []).map<TestCaseDefect>(
    (defect) => ({ ...defect, status: 'open' })
  )
  caseWorkflowSteps[1] = {
    ...caseWorkflowSteps[1],
    status: 'completed',
    detail:
      scriptedDefects.length > 0
        ? `测试脚本执行完成，发现 ${scriptedDefects.length} 条缺陷。`
        : '测试脚本执行完成，未发现异常。'
  }
  caseWorkflowSteps[2] = {
    ...caseWorkflowSteps[2],
    status: 'running',
    detail: '正在整理执行证据并关联受影响产物。'
  }
  if (scriptedDefects.length > 0) defects[currentCase.id] = scriptedDefects
  publishWorkflowSteps([...caseWorkflowSteps])
  emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
  await delay(420)

  caseWorkflowSteps[2] = {
    ...caseWorkflowSteps[2],
    status: 'completed',
    detail:
      scriptedDefects.length > 0
        ? `已生成 ${scriptedDefects.length} 条缺陷并同步到右侧用例详情。`
        : '缺陷清单为空，本轮无需返修。'
  }

  // 有缺陷的用例继续完成“返修 → 回归”；无缺陷用例在缺陷清单节点后直接结束。
  if (scriptedDefects.length > 0) {
    defects[currentCase.id] = scriptedDefects.map((defect) => ({
      ...defect,
      status: 'repairing'
    }))
    caseWorkflowSteps.push({
      id: `case-repair-${currentCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: `修复缺陷（${scriptedDefects.length}）`,
      detail: '正在调度开发返修节点并逐项处理缺陷。',
      sequence: 4
    })
    publishWorkflowSteps([...caseWorkflowSteps])
    emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
    await delay(520)

    defects[currentCase.id] = scriptedDefects.map((defect) => ({
      ...defect,
      status: 'resolved'
    }))
    caseWorkflowSteps[3] = {
      ...caseWorkflowSteps[3],
      status: 'completed',
      detail: `${scriptedDefects.length} 条缺陷均已修复，准备回归验证。`
    }
    caseWorkflowSteps.push({
      id: `case-retest-${currentCase.id}`,
      kind: 'workflow',
      status: 'running',
      title: '回归验证',
      detail: '重新执行当前用例，确认缺陷关闭且未引入回归问题。',
      sequence: 5
    })
    publishWorkflowSteps([...caseWorkflowSteps])
    emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'running')
    await delay(420)
    caseWorkflowSteps[4] = {
      ...caseWorkflowSteps[4],
      status: 'completed',
      detail: '回归验证通过，关联缺陷已关闭。'
    }
  }

  caseResults[currentCase.id] = 'passed'
  completed += 1
  publishWorkflowSteps([...caseWorkflowSteps])
  emitCaseWorkflow(currentCase, completed, { ...caseResults }, { ...defects }, 'completed')
  const nextCase = MOCK_TEST_CASES[currentCaseIndex + 1]
  if (nextCase) return showCaseConfirmation(nextCase)
  return latestCaseWorkflow as WorkflowRunPayload
}

// 应用级审查阶段剧本(复用应用概览会话):
// 审查阶段剧本：审查 Agent 做非功能检查(代码审查/规范检测/健康度)，通过后进入用户验收。
export async function replayCodeReview(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-review-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const baseLifecycle = {
    ...makeBaseLifecycle(options.application),
    extensions: {
      testExecutionStatus: 'passed',
      testCasesCompleted: MOCK_BUSINESS_TEST_CASE_TOTAL,
      testCasesTotal: MOCK_BUSINESS_TEST_CASE_TOTAL,
      phaseValidity: {
        analysis: 'valid',
        planning: 'valid',
        development: 'valid',
        testing: 'valid',
        review: 'valid'
      }
    }
  } as ApplicationLifecycle

  const appExec = (
    phase: string,
    status: string,
    pending?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase,
      status,
      startedAt: now,
      updatedAt: now,
      ...(pending ? { pendingInteraction: pending } : {})
    }
  }
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  // 审查报告写入确认后，一次性提交完整审查通过快照，避免同 revision 的补充状态被生命周期 store 丢弃。
  if (
    answers.code_review ||
    (answers.file_acceptance && resume?.summary?.phase === 'code_review')
  ) {
    await delay(300)
    const now = new Date().toISOString()
    const reviewed = {
      ...baseLifecycle,
      updatedAt: now,
      revision: nextLifecycleRevision(),
      extensions: {
        ...baseLifecycle.extensions,
        reviewStatus: 'passed',
        acceptanceStatus: 'pending',
        phaseValidity: {
          analysis: 'valid',
          planning: 'valid',
          development: 'valid',
          testing: 'valid',
          review: 'valid',
          acceptance: 'valid'
        }
      },
      activeExecutions: {
        [runId]: appExec('code_review', 'completed')
      }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(reviewed)
    registerWorkbenchLifecycle(reviewed)
    return emit(
      'code_review',
      'completed',
      reviewed,
      {},
      {
        summary: {
          phase: 'code_review',
          status: 'completed',
          message: '审查阶段已完成，已进入验收阶段'
        }
      }
    )
  }

  // 审查报告只需要确认右侧 Diff 写入；保存完成后直接结束审查并开放生成版本。
  if (answers.file_acceptance && resume?.summary?.phase === 'code_review') {
    const finalized = emitLifecycle(appExec('finalize_project', 'completed'))
    return emit(
      'finalize_project',
      'completed',
      finalized,
      {},
      {
        summary: {
          phase: 'finalize_project',
          status: 'completed',
          message: '审查报告已保存，可生成版本'
        }
      }
    )
  }

  // 2. 进入审查后直接走子图(规范检测 → 安全扫描 → 健康度)，不再询问是否启动。
  //    子节点 emit 复用 code_review running 的 lifecycle,不调 emitLifecycle 更新 applicationLifecycle,
  //    避免 lint/security/health 进入 activeExecutions 后全部 completed(terminal)导致
  //    executionPhase 跌回 development（审查中途回到开发）。workflow payload 的 phase 仍逐节点切换（节点卡动态）。
  if (
    !(answers.code_review || (answers.file_acceptance && resume?.summary?.phase === 'code_review'))
  ) {
    const reviewRunning = emitLifecycle(appExec('code_review', 'running'))
    emit('code_review', 'running', reviewRunning)
    const steps: Record<string, unknown>[] = []
    const reviewStepTotal = 4
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.(withProcessStepTotal(steps as ProcessStepRecord[], reviewStepTotal))
    }

    // 规范检测
    emit('lint_check', 'running', reviewRunning)
    await delay(1000)
    emit('lint_check', 'completed', reviewRunning)
    pushStep({
      id: 'step-lint',
      kind: 'workflow',
      status: 'completed',
      title: '代码规范检测',
      detail: '命名约定、模块结构与重复代码扫描通过。',
      sequence: 1
    })

    // 安全扫描
    emit('security_scan', 'running', reviewRunning)
    await delay(1000)
    emit('security_scan', 'completed', reviewRunning)
    pushStep({
      id: 'step-security',
      kind: 'workflow',
      status: 'completed',
      title: '安全扫描',
      detail: '未发现硬编码密钥、越权访问与注入风险。',
      sequence: 2
    })

    // 健康度评估
    emit('health_check', 'running', reviewRunning)
    await delay(1000)
    emit('health_check', 'completed', reviewRunning)
    pushStep({
      id: 'step-health',
      kind: 'workflow',
      status: 'completed',
      title: '健康度评估',
      detail: '圈复杂度正常 / 重复率 0.8% / 单测覆盖 82%。',
      sequence: 3,
      checks: reviewChecks()
    })

    const report = buildReviewReport({
      completed: MOCK_BUSINESS_TEST_CASE_TOTAL,
      status: 'passed',
      total: MOCK_BUSINESS_TEST_CASE_TOTAL
    })
    const reportTarget: BuildFileTarget = {
      key: 'code-review',
      name: 'code-review.md',
      path: appPath(WORKSPACE_DOC_PATHS.codeReview),
      content: report,
      sourceTool: 'review_agent'
    }
    const reportLines = report.split('\n')
    for (let visible = 12; ; visible += 12) {
      await delay(260)
      emit('code_review', 'running', reviewRunning, {
        codeChanges: singleFileChangeSet(
          runId,
          reportTarget,
          reportLines.slice(0, visible).join('\n')
        )
      })
      if (visible >= reportLines.length) break
    }
    pushStep({
      id: 'step-report',
      kind: 'workflow',
      status: 'completed',
      title: '生成代码审查报告',
      detail: '报告已生成，等待确认写入工作区。',
      sequence: 4
    })
    const reviewPending = {
      id: `pi-code-review-file-${Date.now()}`,
      type: 'file_acceptance',
      basedOnRevision: 1,
      payload: { message: '代码审查报告已生成，请在右侧确认 Diff 后接受。' },
      createdAt: new Date().toISOString()
    }
    const reviewLifecycle = emitLifecycle(appExec('code_review', 'awaiting_user', reviewPending))
    return emit(
      'code_review',
      'requires_user_input',
      reviewLifecycle,
      {
        clarification: {
          mode: 'file_acceptance',
          status: 'requires_user_input',
          message: '代码审查报告已生成，请确认右侧 Diff。'
        },
        codeChanges: singleFileChangeSet(runId, reportTarget, report)
      },
      {
        summary: {
          phase: 'code_review',
          status: 'requires_user_input',
          message: '等待接受代码审查报告'
        }
      }
    )
  }

  // 以上分支覆盖审查启动、报告授权和结论确认；未识别的控制参数不应静默生成错误状态。
  throw new Error('代码审查工作流未处理当前状态。')
}

export async function replayWorkbench(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const agentTarget = resolveAgentTarget(options, resume)
  if (agentTarget) {
    return replayAgentWorkbench(threadId, agentTarget, options, callbacks)
  }
  // 接口目标优先于页面：选中接口或续传快照带接口身份时走接口剧本。
  const endpointTarget = resolveEndpointTarget(options, resume)
  if (endpointTarget) {
    return replayEndpointWorkbench(threadId, endpointTarget, options, callbacks)
  }
  const runId = resume?.runId || `mock-run-${Date.now()}`
  const page = pageMeta(options.selectedPageId || workflowPageId(resume))
  const includesRecheckEndpoint = page.id === 'my-rechecks'
  const pageTaskIdentity = {
    selectedPageId: page.id,
    ...(includesRecheckEndpoint
      ? {
          selectedApiContractId: 'rechecks',
          selectedEndpointId: 'ep-my-rechecks'
        }
      : {}),
    detailTargetType: 'page'
  }
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  // 按当前应用工作区取页面详设数据（pms-new 单页详设）。
  const baseLifecycle = makeBaseLifecycle(options.application)

  // 产物验收工作流的续跑：验收确认由独立验收剧本承载，不能落回页面派发剧本。
  if (answers.page_acceptance) {
    return replayArtifactAcceptance(threadId, options, callbacks)
  }

  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(
      threadId,
      runId,
      phase,
      status,
      lifecycle,
      { ...pageTaskIdentity, ...state },
      extra
    )
    onWorkflow?.(payload)
    return payload
  }
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)

  // 1. 详情审阅确认（或续跑）→ 在「选择执行方式」节点上选择同步执行或后台资源池。
  //    同步执行由剧本在对话内当场播放生成节点并落同一条任务记录；
  //    异步/潮汐派发后台任务后前台立即收口，页面与依赖接口由同一任务联合交付。
  if (answers.detail_review || resume) {
    // ——「我的回检」：页面 + 依赖接口双产物流程（状态机推进）——
    // 每个产物一个完整闭环：选择执行方式 → 生成代码 → 确认变更 → 下一产物。
    // 页面闭环先走，接口闭环后走；同步任务当场交付，后台任务进对应队列。
    // 进度状态全部累积在 resume.state（stateSyncProgress），每轮据此决定当前动作。
    if (includesRecheckEndpoint) {
      const artifactId = pageArtifactId(page.id)
      const endpointArtifact = endpointArtifactId('rechecks', 'ep-my-rechecks')
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const generateNode = foregroundNodes.find((node) => node.id === 'generate_code')!
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      const previewNode = foregroundNodes.find((node) => node.id === 'launch_preview')!
      const pageChoiceNode = workflowNode('development', 'choose_execution')
      const endpointChoiceNode = workflowNode('development', 'choose_execution_endpoint')
      const resumeState = (resume?.state || {}) as Record<string, unknown>
      const endpointTitle = '接口 GET /api/rechecks/my'
      const pageChoice = (resolveDispatchChoice(answers) ??
        (typeof resumeState.pageChoice === 'string'
          ? (resumeState.pageChoice as BackgroundDispatchChoice)
          : undefined)) as BackgroundDispatchChoice | undefined
      const endpointChoiceRaw = answers.background_dispatch_endpoint ?? resumeState.endpointChoice
      const endpointChoice =
        endpointChoiceRaw === 'sync' ||
        endpointChoiceRaw === 'async' ||
        endpointChoiceRaw === 'tide'
          ? (endpointChoiceRaw as BackgroundDispatchChoice)
          : undefined
      const choiceLabel = (choice: BackgroundDispatchChoice): string =>
        BACKGROUND_TASK_SYSTEM_LABEL[choice === 'tide' ? 'tide' : 'async']
      const allTargets = buildFileTargets(page.id, true)
      const pageTarget = allTargets.find((target) => target.key === 'page')
      const endpointTarget = allTargets.find((target) => target.key === 'controller')
      // 各阶段进度标记（累积在 resume.state，决定状态机当前应执行的动作）
      const pageSyncConfirmed = Boolean(resumeState.pageSyncConfirmed)
      const pageSyncPending = Boolean(resumeState.pageSyncPending)
      const pageDispatched = Boolean(resumeState.pageDispatched)
      const endpointSyncConfirmed = Boolean(resumeState.endpointSyncConfirmed)
      const endpointSyncPending = Boolean(resumeState.endpointSyncPending)
      const endpointDispatched = Boolean(resumeState.endpointDispatched)
      const step = (
        id: string,
        status: ProcessStepRecord['status'],
        title: string,
        detail: string
      ): ProcessStepRecord => ({ id, kind: 'workflow', status, title, detail, sequence: 1 })

      /** 把本轮新产生的进度合并进累积状态，作为后续挂起的持久化上下文。 */
      const mergeProgress = (extra: Record<string, unknown>): Record<string, unknown> => ({
        ...resumeState,
        ...extra
      })

      // ——① 页面产物：选择执行方式（挂起）——
      if (!pageChoice) {
        onContent?.(
          '「' +
            page.label +
            '」页面与依赖接口 ' +
            endpointTitle +
            ' 的详细设计已确认，请先为页面选择执行方式。'
        )
        onProcessSteps?.([
          step(
            pageChoiceNode.id,
            'requires_user_input',
            pageChoiceNode.title,
            pageChoiceNode.detail
          )
        ])
        return emit(
          'build',
          'requires_user_input',
          emitLifecycle(
            exec(
              runId,
              threadId,
              page.id,
              'build',
              'awaiting_user',
              backgroundDispatchInteraction()
            )
          ),
          {
            clarification: {
              mode: 'background_dispatch',
              status: 'requires_user_input',
              message: '请为页面「' + page.label + '」选择执行方式。',
              questions: []
            },
            dispatchTarget: 'page'
          },
          {
            summary: {
              phase: 'build',
              status: 'requires_user_input',
              message: '等待页面执行方式选择'
            }
          }
        )
      }

      // ——② 页面产物：同步执行（渐进生成 + 代码变更确认挂起）——
      if (pageChoice === 'sync' && !pageSyncConfirmed && !pageSyncPending) {
        onProcessSteps?.([
          step(
            pageChoiceNode.id,
            'completed',
            pageChoiceNode.title,
            '已选择同步任务，页面实现在当前对话中直接执行。'
          ),
          step(generateNode.id, 'running', generateNode.title, generateNode.detail)
        ])
        const generateLifecycle = emitLifecycle(
          exec(runId, threadId, page.id, 'generate_code', 'running')
        )
        if (pageTarget) {
          const lines = pageTarget.content.split(String.fromCharCode(10))
          for (let visible = 8; ; visible += 8) {
            await delay(400)
            emit('generate_code', 'running', generateLifecycle, {
              codeChanges: changeSetFromContents(runId, [
                {
                  target: pageTarget,
                  content: lines.slice(0, visible).join(String.fromCharCode(10))
                }
              ])
            })
            if (visible >= lines.length) break
          }
        }
        onProcessSteps?.([
          step(generateNode.id, 'completed', generateNode.title, generateNode.detail),
          step(confirmNode.id, 'requires_user_input', confirmNode.title, confirmNode.detail)
        ])
        onContent?.('页面代码已生成，请在右侧源码区确认 Diff 并接受。')
        return emit(
          'build',
          'requires_user_input',
          emitLifecycle(
            exec(runId, threadId, page.id, 'build', 'awaiting_user', fileAcceptanceInteraction())
          ),
          mergeProgress({
            clarification: {
              mode: 'file_acceptance',
              status: 'requires_user_input',
              message: '页面代码已生成，请在右侧确认 Diff 后接受。'
            },
            codeChanges: pageTarget ? fullChangeSet(runId, [pageTarget]) : undefined,
            pageChoice: 'sync',
            pageSyncConfirmed: false,
            pageSyncPending: true
          }),
          {
            summary: {
              phase: 'build',
              status: 'requires_user_input',
              message: '等待确认页面代码变更'
            }
          }
        )
      }

      // ——③ 页面产物：代码变更确认接受——落终态 + 页面预览，随后进入接口产物闭环——
      if (pageChoice === 'sync' && pageSyncPending && !pageSyncConfirmed && resume) {
        onProcessSteps?.([
          step(confirmNode.id, 'completed', confirmNode.title, '页面代码变更已接受。'),
          step(previewNode.id, 'running', previewNode.title, '正在启动当前页面预览。')
        ])
        emit(
          'launch_project',
          'running',
          emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'running'))
        )
        await delay(420)
        markPageDesigned(page.id)
        onContent?.('页面「' + page.label + '」交付完成，请继续为依赖接口选择执行方式。')
        onProcessSteps?.([
          step(
            pageChoiceNode.id,
            'completed',
            pageChoiceNode.title,
            '页面实现在当前对话中直接执行完毕。'
          ),
          step(
            previewNode.id,
            'completed',
            previewNode.title,
            '代码文件已保存，右侧已切换到浏览器预览。'
          ),
          step(
            endpointChoiceNode.id,
            'requires_user_input',
            endpointChoiceNode.title,
            endpointChoiceNode.detail
          )
        ])
        return emit(
          'build',
          'requires_user_input',
          emitLifecycle(
            exec(
              runId,
              threadId,
              page.id,
              'build',
              'awaiting_user',
              backgroundDispatchInteraction()
            )
          ),
          mergeProgress({
            clarification: {
              mode: 'background_dispatch',
              status: 'requires_user_input',
              message: '请为依赖接口 ' + endpointTitle + ' 选择执行方式。',
              questions: []
            },
            dispatchTarget: 'endpoint',
            pageChoice: 'sync',
            pageSyncConfirmed: true,
            pageSyncPending: false,
            // 页面 Diff 已接受落库，清掉挂起态携带的旧变更，避免上一轮授权条残留。
            codeChanges: undefined
          }),
          {
            result: {
              preview_url: MOCK_APPLICATION_PREVIEW_URL + page.path.replace(/^\//, '')
            },
            summary: {
              phase: 'build',
              status: 'requires_user_input',
              message: '页面已交付，等待接口执行方式选择'
            }
          }
        )
      }

      // ——④ 页面产物：后台派发——
      if (pageChoice !== 'sync' && !pageDispatched) {
        dispatchImplementationTask({
          options,
          title: '页面「' + page.label + '」代码实现',
          artifactIds: [artifactId],
          primaryArtifactId: artifactId,
          execTarget: { type: 'page', pageId: page.id, includeEndpoint: false },
          choice: pageChoice
        })
        markPageDesigned(page.id)
        onProcessSteps?.([
          step(
            pageChoiceNode.id,
            'completed',
            pageChoiceNode.title,
            '已选择' + choiceLabel(pageChoice) + '，页面实现转入后台执行。'
          ),
          step(
            'dispatch-page-' + page.id,
            'completed',
            '派发页面实现任务',
            '已创建后台页面实现任务（' +
              choiceLabel(pageChoice) +
              '），可在「' +
              BACKGROUND_TASK_SYSTEM_LABEL[pageChoice] +
              '」抽屉查看进度。'
          )
        ])
      }

      // ——⑤ 接口产物：选择执行方式（挂起）——
      if (!endpointChoice) {
        onContent?.('页面已交付，请为依赖接口 ' + endpointTitle + ' 选择执行方式。')
        onProcessSteps?.([
          step(
            pageChoiceNode.id,
            'completed',
            pageChoiceNode.title,
            '页面实现任务已派发至所选任务系统。'
          ),
          step(
            endpointChoiceNode.id,
            'requires_user_input',
            endpointChoiceNode.title,
            endpointChoiceNode.detail
          )
        ])
        return emit(
          'build',
          'requires_user_input',
          emitLifecycle(
            exec(
              runId,
              threadId,
              page.id,
              'build',
              'awaiting_user',
              backgroundDispatchInteraction()
            )
          ),
          {
            clarification: {
              mode: 'background_dispatch',
              status: 'requires_user_input',
              message: '请为依赖接口 ' + endpointTitle + ' 选择执行方式。',
              questions: []
            },
            dispatchTarget: 'endpoint',
            pageChoice,
            // 页面任务刚在本轮派发过，必须落进持久化状态，否则下一轮会重复派发。
            ...(pageChoice !== 'sync' ? { pageDispatched: true } : {})
          },
          {
            summary: {
              phase: 'build',
              status: 'requires_user_input',
              message: '等待接口执行方式选择'
            }
          }
        )
      }

      // ——⑥ 接口产物：同步执行（渐进生成 + 代码变更确认挂起）——
      if (endpointChoice === 'sync' && !endpointSyncConfirmed && !endpointSyncPending) {
        // 接口构建链使用 endpoint- 前缀的独立节点：轨迹按 id 合并只能往后追加，
        // 复用页面轮的节点 id 会把已完成的历史节点回写成运行态，看起来像倒退。
        onProcessSteps?.([
          step(
            endpointChoiceNode.id,
            'completed',
            endpointChoiceNode.title,
            '已选择同步任务，接口实现在当前对话中直接执行。'
          ),
          step(
            'endpoint-generate-code',
            'running',
            generateNode.title,
            '生成接口控制器与数据访问代码。'
          )
        ])
        const generateLifecycle = emitLifecycle(
          exec(runId, threadId, page.id, 'generate_code', 'running')
        )
        if (endpointTarget) {
          const lines = endpointTarget.content.split(String.fromCharCode(10))
          for (let visible = 8; ; visible += 8) {
            await delay(400)
            emit('generate_code', 'running', generateLifecycle, {
              codeChanges: changeSetFromContents(runId, [
                {
                  target: endpointTarget,
                  content: lines.slice(0, visible).join(String.fromCharCode(10))
                }
              ])
            })
            if (visible >= lines.length) break
          }
        }
        onProcessSteps?.([
          step('endpoint-generate-code', 'completed', generateNode.title, generateNode.detail),
          step(
            'endpoint-confirm-changes',
            'requires_user_input',
            confirmNode.title,
            '请确认接口实现生成的代码变更。'
          )
        ])
        onContent?.('接口代码已生成，请在右侧源码区确认 Diff 并接受。')
        return emit(
          'build',
          'requires_user_input',
          emitLifecycle(
            exec(runId, threadId, page.id, 'build', 'awaiting_user', fileAcceptanceInteraction())
          ),
          mergeProgress({
            clarification: {
              mode: 'file_acceptance',
              status: 'requires_user_input',
              message: '接口代码已生成，请在右侧确认 Diff 后接受。'
            },
            codeChanges: endpointTarget ? fullChangeSet(runId, [endpointTarget]) : undefined,
            endpointChoice: 'sync',
            endpointSyncConfirmed: false,
            endpointSyncPending: true
          }),
          {
            summary: {
              phase: 'build',
              status: 'requires_user_input',
              message: '等待确认接口代码变更'
            }
          }
        )
      }

      // ——⑦ 接口产物：代码变更确认接受——落终态并收口——
      if (endpointChoice === 'sync' && endpointSyncPending && !endpointSyncConfirmed && resume) {
        onProcessSteps?.([
          step(
            'endpoint-confirm-changes',
            'completed',
            confirmNode.title,
            '接口代码变更已接受。'
          ),
          step('endpoint-launch-preview', 'running', previewNode.title, '正在启动当前页面预览。')
        ])
        emit(
          'launch_project',
          'running',
          emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'running'))
        )
        await delay(420)
        onProcessSteps?.([
          step(
            'endpoint-launch-preview',
            'completed',
            previewNode.title,
            '代码文件已保存，右侧已切换到浏览器预览。'
          )
        ])
        markEndpointDesigned('rechecks', 'ep-my-rechecks')
        onContent?.('接口交付完成。')
        return emit(
          'build',
          'completed',
          emitLifecycle(exec(runId, threadId, page.id, 'build', 'completed')),
          {},
          { summary: { phase: 'build', status: 'completed', message: '接口实现已完成' } }
        )
      }

      // ——⑧ 接口产物：后台派发——
      if (!endpointDispatched) {
        dispatchImplementationTask({
          options,
          title: endpointTitle + ' 代码实现',
          artifactIds: [endpointArtifact],
          primaryArtifactId: endpointArtifact,
          execTarget: { type: 'endpoint', apiContractId: 'rechecks', endpointId: 'ep-my-rechecks' },
          choice: endpointChoice
        })
        markEndpointDesigned('rechecks', 'ep-my-rechecks')
        onProcessSteps?.([
          step(
            endpointChoiceNode.id,
            'completed',
            endpointChoiceNode.title,
            '已选择' + choiceLabel(endpointChoice) + '，接口实现转入后台执行。'
          ),
          step(
            'dispatch-endpoint-' + endpointArtifact,
            'completed',
            '派发接口实现任务',
            '已创建后台接口实现任务（' +
              choiceLabel(endpointChoice) +
              '），可在「' +
              BACKGROUND_TASK_SYSTEM_LABEL[endpointChoice] +
              '」抽屉查看进度。'
          )
        ])
      }

      // ——⑨ 收口：全部产物进入执行/交付——
      onContent?.(
        '页面与依赖接口的实现任务已全部发起（' +
          [choiceLabel(pageChoice), choiceLabel(endpointChoice)].join('、') +
          '），可在对应任务系统查看进度。'
      )
      return emit(
        'build',
        'completed',
        emitLifecycle(exec(runId, threadId, page.id, 'build', 'completed')),
        {},
        { summary: { phase: 'build', status: 'completed', message: '实现任务已全部发起' } }
      )
    }
    // 同步执行的代码变更确认续跑：接受 Diff 后补播构建检查与页面预览，产物状态由文件快照推导。
    if (answers.file_acceptance && resume) {
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      const buildNode = foregroundNodes.find((node) => node.id === 'build_and_test')!
      const previewNode = foregroundNodes.find((node) => node.id === 'launch_preview')!
      onProcessSteps?.([
        {
          id: confirmNode.id,
          kind: 'workflow',
          status: 'completed',
          title: confirmNode.title,
          detail: '已接受本次生成的代码变更，继续构建。',
          sequence: 1
        }
      ])
      onProcessSteps?.([
        {
          id: buildNode.id,
          kind: 'workflow',
          status: 'running',
          title: buildNode.title,
          detail: buildNode.detail,
          sequence: 1
        }
      ])
      emit(
        'build_and_test',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'build_and_test', 'running'))
      )
      await delay(1300)
      onProcessSteps?.([
        {
          id: buildNode.id,
          kind: 'workflow',
          status: 'completed',
          title: buildNode.title,
          detail: buildNode.detail,
          sequence: 1
        }
      ])
      onProcessSteps?.([
        {
          id: previewNode.id,
          kind: 'workflow',
          status: 'running',
          title: previewNode.title,
          detail: '正在启动当前页面预览。',
          sequence: 1
        }
      ])
      emit(
        'launch_project',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'running'))
      )
      await delay(420)
      // 代码变更已在对话内确认：同步交付当场完毕，不产生待验收状态。
      // 设计确认即标记「已设计」：下一页面的模板/详设卡可以立即自动投放。
      markPageDesigned(page.id)
      if (includesRecheckEndpoint) markEndpointDesigned('rechecks', 'ep-my-rechecks')
      onProcessSteps?.([
        {
          id: previewNode.id,
          kind: 'workflow',
          status: 'completed',
          title: previewNode.title,
          detail: '代码文件已保存，右侧已切换到浏览器预览。',
          sequence: 1
        }
      ])
      return emit(
        'launch_project',
        'completed',
        emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'completed')),
        {},
        {
          result: {
            preview_url: `${MOCK_APPLICATION_PREVIEW_URL}${page.path.replace(/^\//, '')}`
          },
          summary: {
            phase: 'launch_project',
            status: 'completed',
            message: '代码产物已完成，已进入页面预览'
          }
        }
      )
    }
    const choice = resolveDispatchChoice(answers)
    const artifactId = pageArtifactId(page.id)
    const relatedArtifactIds = includesRecheckEndpoint
      ? [artifactId, endpointArtifactId('rechecks', 'ep-my-rechecks')]
      : [artifactId]
    // 「选择执行方式」节点来自开发工作流底层 DAG（页面/接口两条设计分支在此汇聚）：
    // 挂起时为待输入节点（交互卡内嵌其上），选择后按同 id 落成已完成，轨迹按 id 合并保持连续。
    const executionNode = workflowNode('development', 'choose_execution')
    const choiceStep = (
      status: ProcessStepRecord['status'],
      detail: string
    ): ProcessStepRecord => ({
      id: executionNode.id,
      kind: 'workflow',
      status,
      title: executionNode.title,
      detail,
      sequence: 1
    })
    if (!choice) {
      // 尚未选择执行方式：轨迹追加待输入节点，由用户决定同步执行或进入哪个任务系统。
      onProcessSteps?.([choiceStep('requires_user_input', executionNode.detail)])
      return emit(
        'prepare_build_tasks',
        'requires_user_input',
        emitLifecycle(
          exec(
            runId,
            threadId,
            page.id,
            'prepare_build_tasks',
            'awaiting_user',
            backgroundDispatchInteraction()
          )
        ),
        {
          clarification: {
            mode: 'background_dispatch',
            status: 'requires_user_input',
            message: '请选择本次实现任务的执行方式，选择后按所选通道执行。',
            questions: []
          }
        },
        {
          summary: {
            phase: 'prepare_build_tasks',
            status: 'requires_user_input',
            message: '等待选择执行方式'
          }
        }
      )
    }
    /** 同步执行：在对话内按阶段播放代码生成过程；同步不进任务池，产物状态由工作流与已保存文件推导。 */
    const syncImplementPage = async (): Promise<WorkflowRunPayload> => {
      onProcessSteps?.([choiceStep('completed', '已选择同步任务，任务在当前对话中直接执行。')])
      // 前台构建段节点取自开发工作流底层 DAG，与后台执行链共用同一套节点定义。
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const generateNode = foregroundNodes.find((node) => node.id === 'generate_code')!
      const buildTargets = buildFileTargets(page.id, includesRecheckEndpoint)
      // 生成构建计划属于后台分析动作，不在对话轨迹中展示；直接进入生成代码。
      emit(
        'build_dag',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'build_dag', 'running'))
      )
      await delay(900)
      // 生成代码：按行分帧渐进写入 Diff，模拟一段一段生成的过程。
      onProcessSteps?.([
        {
          id: generateNode.id,
          kind: 'workflow',
          status: 'running',
          title: generateNode.title,
          detail: generateNode.detail,
          sequence: 1
        }
      ])
      const generateLifecycle = emitLifecycle(
        exec(runId, threadId, page.id, 'generate_code', 'running')
      )
      const finishedSources: ChangeSource[] = []
      for (const target of buildTargets) {
        const lines = target.content.split('\n')
        for (let visible = 8; ; visible += 8) {
          await delay(400)
          emit('generate_code', 'running', generateLifecycle, {
            codeChanges: changeSetFromContents(runId, [
              ...finishedSources,
              { target, content: lines.slice(0, visible).join('\n') }
            ])
          })
          if (visible >= lines.length) break
        }
        finishedSources.push({ target, content: target.content })
      }
      onProcessSteps?.([
        {
          id: generateNode.id,
          kind: 'workflow',
          status: 'completed',
          title: generateNode.title,
          detail: generateNode.detail,
          sequence: 1
        }
      ])
      // 生成代码完成：携带代码变更集，挂「确认代码变更」待输入节点（右侧源码区打开 Diff）。
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      onProcessSteps?.([
        {
          id: confirmNode.id,
          kind: 'workflow',
          status: 'requires_user_input',
          title: confirmNode.title,
          detail: confirmNode.detail,
          sequence: 1
        }
      ])
      onContent?.(`「${page.label}」的代码已生成，请在右侧源码区确认 Diff 并接受，随后继续构建。`)
      return emit(
        'build',
        'requires_user_input',
        emitLifecycle(
          exec(runId, threadId, page.id, 'build', 'awaiting_user', fileAcceptanceInteraction())
        ),
        {
          clarification: {
            mode: 'file_acceptance',
            status: 'requires_user_input',
            message: '代码已生成，请在右侧确认 Diff 后接受。'
          },
          codeChanges: fullChangeSet(runId, buildTargets)
        },
        {
          summary: {
            phase: 'build',
            status: 'requires_user_input',
            message: '等待确认代码变更'
          }
        }
      )
    }
    if (choice === 'sync') return syncImplementPage()
    dispatchImplementationTask({
      options,
      title: `页面「${page.label}」代码实现`,
      artifactIds: relatedArtifactIds,
      primaryArtifactId: artifactId,
      execTarget: { type: 'page', pageId: page.id, includeEndpoint: includesRecheckEndpoint },
      choice: choice
    })
    // 设计确认即标记「已设计」：下一页面的模板/详设卡可以立即自动投放。
    markPageDesigned(page.id)
    if (includesRecheckEndpoint) markEndpointDesigned('rechecks', 'ep-my-rechecks')
    // 选择节点落成已完成，再追加派发收口节点：合并回话按 id 归位并接在同一轨迹末尾。
    const dispatchNode = workflowNode('development', 'background_dispatch')
    onProcessSteps?.([
      choiceStep(
        'completed',
        `已选择${BACKGROUND_TASK_SYSTEM_LABEL[choice]}，任务进入对应后台队列执行。`
      ),
      {
        id: dispatchNode.id,
        kind: 'workflow',
        status: 'completed',
        title: dispatchNode.title,
        detail: `已创建后台代码实现任务（${BACKGROUND_TASK_SYSTEM_LABEL[choice]}），可在对应任务系统查看执行进度。`,
        sequence: 1
      }
    ])
    return emit(
      'build',
      'completed',
      emitLifecycle(exec(runId, threadId, page.id, 'build', 'completed')),
      {},
      { summary: { phase: 'build', status: 'completed', message: '页面实现已转入后台执行' } }
    )
  }

  // 2. 开始页面设计（DetailConfirmationPageSelector 点"开始生成"）→ 详情审阅 → 派发后台任务。
  if (options.selectedPageId || options.detailTargetType || options.originalRequest) {
    // 注：不在开始设计时 markPageDesigned——「已设计」仅在详情审阅确认（派发后台任务）时标记。
    // 生成中以 processSteps 持续承载设计节点，保持同一条研发工作流轨迹。
    // 设计节点来自开发工作流底层 DAG 的「详细设计」段；标题与顺序以 DAG 为唯一来源。
    const designDetailOverrides: Record<string, string> = includesRecheckEndpoint
      ? {
          design_context: '整合已确认的应用约束、项目计划、页面目标和依赖接口契约。',
          design_scope: '明确页面职责、核心用户路径，以及页面与 GET /api/rechecks/my 的调用边界。',
          design_breakdown: '拆解筛选、列表、状态反馈，并绑定接口请求参数和响应数据。',
          design_edge: '补齐接口异常、空数据和页面验收标准。'
        }
      : {}
    const designSteps = workflowSegmentNodes('development', 'design')
      .filter((node) => node.id !== 'choose_execution')
      .map((node) => ({
        id: node.id,
        title: node.title,
        detail: designDetailOverrides[node.id] || node.detail
      }))
    const steps: ProcessStepRecord[] = []
    // 设计阶段的节点总数 = 设计节点 + 派发收口节点；代码节点已移入后台任务。
    const workflowTotal = designSteps.length + 1
    const pushStep = (step: ProcessStepRecord): void => {
      steps.push(step)
      onProcessSteps?.(withProcessStepTotal([...steps], workflowTotal))
    }
    onWorkflow?.(
      wf(threadId, runId, 'detail_confirmation', 'running', undefined, {
        summary: {
          phase: 'detail_confirmation',
          status: 'running',
          message: includesRecheckEndpoint ? '正在生成页面与依赖接口设计…' : '正在生成页面详细设计…'
        }
      })
    )
    for (let i = 0; i < designSteps.length; i += 1) {
      const stage = designSteps[i]
      await delay(650)
      pushStep({
        id: `step-design-${i}`,
        kind: 'workflow',
        status: 'completed',
        title: stage.title,
        detail: stage.detail,
        nodeName: 'detail_confirmation',
        sequence: i + 1
      })
    }
    onWorkflow?.(
      wf(threadId, runId, 'detail_confirmation', 'completed', undefined, {
        summary: {
          phase: 'detail_confirmation',
          status: 'completed',
          message: includesRecheckEndpoint ? '页面与依赖接口设计已生成' : '页面详细设计已生成'
        }
      })
    )
    const designProcessSteps = [...steps]
    return replayWorkbench(
      threadId,
      {
        ...options,
        clarificationAnswers: {
          ...(options.clarificationAnswers || {}),
          detail_review: { review_status: 'confirmed', target_changes: [] }
        }
      },
      {
        ...callbacks,
        onProcessSteps: (nextSteps) => {
          onProcessSteps?.(
            withProcessStepTotal(
              [
                ...designProcessSteps,
                ...nextSteps.map((step, index) => ({
                  ...step,
                  sequence: designProcessSteps.length + index + 1
                }))
              ],
              workflowTotal
            )
          )
        }
      }
    )
  }

  // 3. 其它（自由聊天/未知）→ 最小 running 态，不崩。
  const fallback = emitLifecycle(exec(runId, threadId, page.id, 'build', 'running'))
  return emit('build', 'running', fallback)
}
