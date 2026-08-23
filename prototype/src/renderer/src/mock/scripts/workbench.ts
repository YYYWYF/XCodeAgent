// 工作台剧本：模拟后端 AG-UI 事件流 + 生命周期，驱动
// 详情审阅 → 构建执行（Dock running）→ 授权（awaiting_authorization）→ 验收（awaiting_acceptance）→ 完成。
// Dock 模式由 lifecycle.activeExecutions[runId] 的 status / pendingInteraction.type 推导（见 planExecutionMode.ts）。

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
  buildTestReport,
  type PageDesign,
  type TestReportSnapshot
} from '../../workbenchArtifacts'
// 页面/接口契约基座：单一 pms-new 场景（需求回检单模块）。
import { WORKBENCH_PAGES as PAGES } from '../../../../../mock-data/pms-new/workbench-pages'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-new/planning-artifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { markEndpointDesigned, markPageDesigned } from '../designState'
import { appPath, WORKSPACE_DOC_PATHS } from '../workspaceFiles'
import { endpointArtifactId } from '../../workbenchDomain'
import { nextLifecycleRevision } from './revision'

const MOCK_APPLICATION_PREVIEW_URL = 'http://127.0.0.1:5190/'

type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
  onArtifactDiscovered?: (artifactIds: string[]) => void
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

// 页面验收交互。
function acceptanceInteraction(): Record<string, unknown> {
  return {
    id: `pi-acceptance-${Date.now()}`,
    type: 'page_acceptance',
    basedOnRevision: 1,
    payload: { message: '页面已准备好，等待最终验收。' },
    createdAt: new Date().toISOString()
  }
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

// —— 构建节点的代码变更集（对齐正式工程 build 后的 code_changes 载荷，驱动右侧 Diff 过程）——

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

// 页面/接口源码按单文件 Diff 呈现；路由已随应用初始化脚手架存在，不进入页面开发授权。
// —— 构建链按文件拆解（接口 → 页面）：一个文件对应一个单一职责开发节点 ——

type BuildFileTarget = {
  key: string
  name: string
  path: string
  content: string
  sourceTool: string
}

/** 根据已接受的文件路径计算续跑起始轮次，多个源码文件仍属于同一个代码工作流节点。 */
function acceptedBuildIndex(answers: Record<string, unknown>, targets: BuildFileTarget[]): number {
  const accepted = typeof answers.file_acceptance === 'string' ? answers.file_acceptance : ''
  const index = targets.findIndex((target) => target.path === accepted)
  return index >= 0 ? index + 1 : 0
}

/** 为每个源码文件生成单一职责的开发节点，页面与接口不共用同一个编写节点。 */
function codeBuildStepId(target: BuildFileTarget): string {
  return `step-code-${target.key}`
}

/** 返回源码文件对应的单一开发节点名称。 */
function codeBuildStepTitle(target: BuildFileTarget): string {
  return target.sourceTool === 'backend_code_generator' ? '接口代码实现' : '页面代码实现'
}

// 构建链的目标文件清单：新应用全部产物都是新增文件，右侧按单文件 Diff 呈现。
function buildFileTargets(pageId: string, includeEndpoint: boolean): BuildFileTarget[] {
  const scenario = appDataByWorkspace()
  const targets: BuildFileTarget[] = []
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
  return targets
}

/** 生成独立接口会话的代码交付目标，和页面依赖接口使用同一套单文件 Diff 契约。 */
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

// 组装单文件变更集：id 带序号，右侧页签按 id 变化原地刷新写入进度。
function singleFileChangeSet(
  runId: string,
  target: BuildFileTarget,
  content: string
): WorkspaceCodeChangeSet {
  const change = addedFileChange(`cc-${runId}-${target.key}`, target.path, content, target.sourceTool)
  return {
    id: `cc-${runId}-${target.key}-${change.additions}`,
    status: 'applied',
    workspaceRoot: appDataByWorkspace().workspaceRoot,
    summary: { files: 1, additions: change.additions, deletions: 0 },
    files: [change]
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

// 接口（endpoint）工作台剧本：接口详细审阅 → 构建链（检查工作区 → 获取数据库信息 →
// 规划任务 → 生成接口代码 → 集成测试）→ 验收。与页面剧本共用 lifecycle/revision 机制，
// 但 scope='endpoint'，构建链多一个 inspect_database_context 节点。
async function replayEndpointWorkbench(
  threadId: string,
  target: { apiContractId: string; endpointId: string },
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
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

  // 1. 结束 / 暂停计划。
  if (options.planControlAction === 'end') {
    emitLifecycle(
      execEndpoint(
        runId,
        threadId,
        meta.apiContractId,
        meta.endpointId,
        'finalize_project',
        'completed'
      )
    )
    return endpointWf(
      threadId,
      runId,
      'finalize_project',
      'completed',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      undefined,
      {},
      { summary: { phase: 'finalize_project', status: 'completed', message: '计划已结束' } }
    )
  }
  if (options.planControlAction === 'stop') {
    const lifecycle = emitLifecycle(
      execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'stopped')
    )
    return endpointWf(
      threadId,
      runId,
      'build',
      'stopped',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      lifecycle
    )
  }

  // 2. 验收通过 → 计划完成。
  if (answers.page_acceptance) {
    const lifecycle = emitLifecycle(
      execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'acceptance', 'completed')
    )
    onContent?.(`接口 ${meta.label} 已完成交付，可继续设计其他页面或接口。`)
    return endpointWf(
      threadId,
      runId,
      'acceptance',
      'completed',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      lifecycle
    )
  }

  // 3. 详情审阅确认 → 完整构建链 → 等待验收。
  if (answers.detail_review || resume) {
    const endpointTargets = endpointBuildTargets(meta.apiContractId, meta.endpointId)
    const endpointTarget = endpointTargets[0]

    // 单文件接口 Diff 被接受后只需完成文件落库和工作流收口，不能再次重跑检查与任务准备节点。
    if (
      resume?.summary?.phase === 'build' &&
      endpointTarget &&
      answers.file_acceptance === endpointTarget.path
    ) {
      markEndpointDesigned(meta.apiContractId, meta.endpointId)
      return emit(
        'build',
        'completed',
        emitLifecycle(
          execEndpoint(
            runId,
            threadId,
            meta.apiContractId,
            meta.endpointId,
            'build',
            'completed'
          )
        ),
        {},
        { summary: { phase: 'build', status: 'completed', message: '接口代码产物已完成' } }
      )
    }

    const steps: Record<string, unknown>[] = []
    const endpointStepTotal = Math.max(1, endpointTargets.length)
    const buildStepId = endpointTarget ? codeBuildStepId(endpointTarget) : 'step-code-endpoint'
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.(
        withProcessStepTotal(steps as ProcessStepRecord[], endpointStepTotal)
      )
    }
    const updateStep = (id: string, patch: Record<string, unknown>): void => {
      const index = steps.findIndex((step) => step.id === id)
      if (index < 0) return
      steps[index] = { ...steps[index], ...patch }
      onProcessSteps?.(
        withProcessStepTotal(steps as ProcessStepRecord[], endpointStepTotal)
      )
    }

    // 接口开发只负责一个接口源码节点；启动、非功能和业务测试统一放到测试阶段。
    pushStep({
      id: buildStepId,
      kind: 'workflow',
      status: 'running',
      title: endpointTarget ? codeBuildStepTitle(endpointTarget) : '接口代码实现',
      detail: '正在检查工作区、读取数据源并准备接口代码。',
      sequence: 1
    })

    // inspect_workspace
    emit(
      'inspect_workspace',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'inspect_workspace',
          'completed'
        )
      )
    )
    updateStep(buildStepId, { detail: '工作区结构已确认，正在读取数据库上下文。' })
    await delay(600)

    // inspect_database_context（接口有数据来源，比页面多这一节点）
    emit(
      'inspect_database_context',
      'running',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'inspect_database_context',
          'running'
        )
      )
    )
    await delay(700)
    emit(
      'inspect_database_context',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'inspect_database_context',
          'completed'
        )
      )
    )
    updateStep(buildStepId, { detail: `已读取接口数据上下文，正在准备接口实现任务。` })
    await delay(300)

    // prepare_build_tasks
    emit(
      'prepare_build_tasks',
      'running',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'prepare_build_tasks',
          'running'
        )
      )
    )
    await delay(800)
    emit(
      'prepare_build_tasks',
      'completed',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'prepare_build_tasks',
          'completed'
        )
      )
    )
    updateStep(buildStepId, { detail: `已为接口 ${meta.label} 准备代码实现任务。` })
    await delay(300)

    // build
    emit(
      'build',
      'running',
      emitLifecycle(
        execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running')
      )
    )
    await delay(1200)
    const endpointFileAccepted =
      !endpointTarget || answers.file_acceptance === endpointTarget.path
    if (endpointTarget && !endpointFileAccepted) {
      const lines = endpointTarget.content.split('\n')
      for (let visible = 12; ; visible += 12) {
        await delay(260)
        emit(
          'build',
          'running',
          emitLifecycle(
            execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'running')
          ),
          { codeChanges: singleFileChangeSet(runId, endpointTarget, lines.slice(0, visible).join('\n')) }
        )
        if (visible >= lines.length) break
      }
      const pending = {
        id: `pi-endpoint-file-${Date.now()}`,
        type: 'file_acceptance',
        basedOnRevision: 1,
        payload: { message: `接口代码已生成，请确认 ${endpointTarget.name} 的 Diff。` },
        createdAt: new Date().toISOString()
      }
      return endpointWf(
        threadId,
        runId,
        'build',
        'requires_user_input',
        meta.apiContractId,
        meta.endpointId,
        'endpoint',
        emitLifecycle(
          execEndpoint(
            runId,
            threadId,
            meta.apiContractId,
            meta.endpointId,
            'build',
            'awaiting_user',
            pending
          )
        ),
        {
          clarification: {
            mode: 'file_acceptance',
            status: 'requires_user_input',
            message: `接口代码已生成，请确认 ${endpointTarget.name} 的 Diff。`
          },
          codeChanges: singleFileChangeSet(runId, endpointTarget, endpointTarget.content)
        },
        { summary: { phase: 'build', status: 'requires_user_input', message: `等待接受 ${endpointTarget.name}` } }
      )
    }
    emit(
      'build',
      'completed',
      emitLifecycle(
        execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, 'build', 'completed')
      )
    )
    updateStep(buildStepId, {
      status: 'completed',
      title: endpointTarget ? codeBuildStepTitle(endpointTarget) : '接口代码实现',
      detail: '接口代码已生成并完成文件 Diff 授权。'
    })
    await delay(300)
    markEndpointDesigned(meta.apiContractId, meta.endpointId)
    return endpointWf(
      threadId,
      runId,
      'build',
      'completed',
      meta.apiContractId,
      meta.endpointId,
      'endpoint',
      emitLifecycle(
        execEndpoint(
          runId,
          threadId,
          meta.apiContractId,
          meta.endpointId,
          'build',
          'completed'
        )
      ),
      {},
      { summary: { phase: 'build', status: 'completed', message: '接口代码产物已完成' } }
    )
  }

  // 4. 开始接口详细设计 → 接口详情审阅。
  if (options.selectedEndpointId || options.detailTargetType) {
    const designSteps: ProcessStepRecord[] = []
    const designNodes = [
      ['汇总接口上下文', '读取已确认的应用约束、项目计划和接口契约。'],
      ['梳理请求与响应', '明确查询参数、响应结构和页面调用关系。'],
      ['补齐数据来源与边界', '确认数据来源、异常返回和接口验收标准。']
    ] as const
    for (let index = 0; index < designNodes.length; index += 1) {
      await delay(360)
      designSteps.push({
        id: `endpoint-design-${index}`,
        kind: 'workflow',
        status: 'completed',
        title: designNodes[index][0],
        detail: designNodes[index][1],
        sequence: index + 1
      })
      onProcessSteps?.(
        withProcessStepTotal(
          [...designSteps],
          designNodes.length + 1
        )
      )
    }
    const designProcessSteps = [...designSteps]
    const endpointWorkflowTotal = designNodes.length + 1
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
    const lifecycle = {
      schemaVersion: '1.2.0',
      application: { id: appId, name: appName },
      updatedAt: new Date().toISOString(),
      revision: nextLifecycleRevision(),
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      activeExecutions: { [runId]: appExecution(status, pending) }
    } as ApplicationLifecycle
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  /** 同步应用验收 Workflow 投影到对话消息。 */
  const emit = (
    status: string,
    lifecycle: ApplicationLifecycle,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = wf(threadId, runId, 'acceptance', status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  if (answers.application_acceptance) {
    onContent?.('应用整体验收已通过，即将进入测试阶段。')
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
    payload: { message: '请在右侧完整应用预览中完成整体验收。' },
    createdAt: new Date().toISOString()
  }
  const lifecycle = emitLifecycle('awaiting_user', pending)
  onContent?.('所有页面与接口代码产物均已交付。请在右侧预览完整应用，确认后进入测试阶段。')
  return emit(
    'requires_user_input',
    lifecycle,
    {
      clarification: {
        mode: 'application_acceptance',
        status: 'requires_user_input',
        message: '请完成应用整体验收。',
        questions: [
          {
            id: 'application_acceptance',
            header: '应用验收',
            question: '完整应用的页面、接口与关键业务流程是否符合预期？',
            type: 'yesno',
            presetAnswer: { selected: ['是'] }
          }
        ]
      }
    },
    {
      result: { preview_url: MOCK_APPLICATION_PREVIEW_URL },
      summary: { phase: 'acceptance', status: 'requires_user_input', message: '等待应用验收' }
    }
  )
}

/** 测试阶段剧本：启动、非功能和业务测试均通过后生成测试报告。 */
export async function replayApplicationTesting(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-application-testing-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const round = 1
  const report: TestReportSnapshot = {
    round,
    status: 'passed',
    basedOnRevision: 1,
    defects: []
  }
  const reportTarget: BuildFileTarget = {
    key: 'test-report',
    name: 'test-report.md',
    path: appPath(WORKSPACE_DOC_PATHS.testReport),
    content: buildTestReport(report),
    sourceTool: 'test_agent'
  }
  const baseLifecycle = {
    ...makeBaseLifecycle(options.application),
    extensions: {
      testReportStatus: 'running',
      testReportRound: report.round,
      testReportBasedOnRevision: report.basedOnRevision,
      testReportDefects: report.defects,
    }
  } as ApplicationLifecycle
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const reportLifecycleBase = {
    ...baseLifecycle,
    extensions: {
      ...baseLifecycle.extensions,
      testReportStatus: report.status,
      testReportRound: report.round,
      testReportBasedOnRevision: report.basedOnRevision,
      testReportDefects: report.defects,
    }
  } as ApplicationLifecycle
  const emitReportLifecycle = makeEmitLifecycle(reportLifecycleBase, runId, onApplicationLifecycle)
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

  // 测试报告 Diff 接受后直接完成测试阶段，不再追加二次确认卡片。
  if (answers.file_acceptance && resume?.summary?.phase === 'test_report') {
    const reportLifecycle = emitReportLifecycle({
      scope: 'application',
      targetId: appId,
      threadId,
      runId,
      phase: 'test_report',
      status: 'completed',
      startedAt: new Date().toISOString(),
      updatedAt: new Date().toISOString()
    })
    return emit(
      'test_report',
      'completed',
      reportLifecycle,
      {
        testReport: report
      },
      {
        summary: {
          phase: 'test_report',
          status: 'completed',
          message: '测试报告已确认，可进入审查阶段'
        }
      }
    )
  }

  const testSteps: ProcessStepRecord[] = []
  const publishTestSteps = (): void => onProcessSteps?.(withProcessStepTotal([...testSteps], 4))
  const addTestStep = (step: ProcessStepRecord): void => {
    const currentIndex = testSteps.findIndex((item) => item.id === step.id)
    if (currentIndex >= 0) testSteps[currentIndex] = step
    else testSteps.push(step)
    publishTestSteps()
  }

  // 先进入测试阶段再启动第一个节点，确保用户能看到 Agent 从“整理输入”开始逐步执行，
  // 不会因为阶段切换滞后而只看到已经生成好的测试报告。
  const running = emitLifecycle({
    scope: 'application',
    targetId: appId,
    threadId,
    runId,
    phase: 'application_test',
    status: 'running',
    startedAt: new Date().toISOString(),
    updatedAt: new Date().toISOString()
  })
  emit('application_test', 'running', running)
  addTestStep({
    id: 'application-test-startup',
    kind: 'workflow',
    status: 'running',
    title: '启动测试',
    detail: '启动应用并检查主路由、页面入口和基础运行环境。',
    sequence: 1
  })
  await delay(900)
  addTestStep({
    id: 'application-test-startup',
    kind: 'workflow',
    status: 'completed',
    title: '启动测试',
    detail: '应用已启动，主路由和页面入口可以访问。',
    sequence: 1
  })
  addTestStep({
    id: 'application-test-non-functional',
    kind: 'workflow',
    status: 'running',
    title: '非功能测试',
    detail: '检查异常反馈、响应稳定性、权限边界和恢复路径。',
    sequence: 2
  })
  await delay(900)
  addTestStep({
    id: 'application-test-non-functional',
    kind: 'workflow',
    status: 'completed',
    title: '非功能测试',
    detail: '异常反馈、权限边界和恢复路径均通过。',
    sequence: 2
  })
  addTestStep({
    id: 'application-test-business',
    kind: 'workflow',
    status: 'running',
    title: '业务测试',
    detail: '执行需求文档和项目计划中的核心业务旅程。',
    sequence: 3
  })
  await delay(900)
  addTestStep({
    id: 'application-test-business',
    kind: 'workflow',
    status: 'completed',
    title: '业务测试',
    detail: '核心业务旅程全部通过。',
    sequence: 3
  })
  const runningReport = { ...report, status: 'running' as const }
  addTestStep({
    id: 'application-test-report',
    kind: 'workflow',
    status: 'running',
    title: '生成测试报告',
    detail: '汇总测试结果、缺陷证据和受影响产物。',
    sequence: 4
  })
  const reportLines = reportTarget.content.split('\n')
  for (let visible = 12; ; visible += 12) {
    await delay(260)
    emit(
      'test_report',
      'running',
      emitLifecycle({
        scope: 'application',
        targetId: appId,
        threadId,
        runId,
        phase: 'test_report',
        status: 'running',
        startedAt: new Date().toISOString(),
        updatedAt: new Date().toISOString()
      }),
      {
        testReport: runningReport,
        codeChanges: singleFileChangeSet(
          runId,
          reportTarget,
          reportLines.slice(0, visible).join('\n')
        )
      }
    )
    if (visible >= reportLines.length) break
  }
  addTestStep({
    id: 'application-test-report',
    kind: 'workflow',
    status: 'completed',
    title: '生成测试报告',
    detail: '测试报告已生成，结论为合格。',
    sequence: 4
  })
  const reportPending = {
    id: `pi-test-report-file-${Date.now()}`,
    type: 'file_acceptance',
    basedOnRevision: report.basedOnRevision,
    payload: { message: '测试报告已生成，请在右侧确认 Diff 后接受。' },
    createdAt: new Date().toISOString()
  }
  // 报告尚未被用户接受前，只保留“测试进行中”的生命周期。缺陷和受影响产物必须在
  // 测试报告保存确认后才提交，避免刚进入测试阶段就把左侧开发产物错误地重置为进行中。
  const reportLifecycle = emitLifecycle({
    scope: 'application',
    targetId: appId,
    threadId,
    runId,
    phase: 'test_report',
    status: 'awaiting_user',
    startedAt: new Date().toISOString(),
    updatedAt: new Date().toISOString(),
    pendingInteraction: reportPending
  })
  return emit(
    'test_report',
    'requires_user_input',
    reportLifecycle,
    {
      testReport: report,
      clarification: {
        mode: 'file_acceptance',
        status: 'requires_user_input',
        message: '测试报告已生成，请确认右侧 Diff。'
      },
      codeChanges: singleFileChangeSet(runId, reportTarget, reportTarget.content)
    },
    {
      summary: {
        phase: 'test_report',
        status: 'requires_user_input',
        message: '测试报告已生成，等待接受 Diff'
      }
    }
  )
}

// 应用级审查阶段剧本(复用应用概览会话):
// 应用验收通过 → 审查 Agent 做非功能检查(代码审查/规范检测/健康度),审查通过 → 可发布。
export async function replayCodeReview(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-review-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const baseLifecycle = {
    ...makeBaseLifecycle(options.application),
    extensions: {
      testReportStatus: 'passed',
      testReportRound: 2,
      testReportBasedOnRevision: 1,
      testReportDefects: [],
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

  // 1. 审查确认通过 → finalize_project completed(可发布)。
  if (answers.code_review) {
    onContent?.('审查确认通过,应用已就绪,现在可以生成版本了。')
    await delay(300)
    const finalized = emitLifecycle(appExec('finalize_project', 'completed'))
    return emit(
      'finalize_project',
      'completed',
      finalized,
      {},
      {
        summary: { phase: 'finalize_project', status: 'completed', message: '审查通过,可生成版本' }
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
      { summary: { phase: 'finalize_project', status: 'completed', message: '审查报告已保存，可生成版本' } }
    )
  }

  // 2. 进入审查后直接走子图(规范检测 → 安全扫描 → 健康度)，不再询问是否启动。
  //    子节点 emit 复用 code_review running 的 lifecycle,不调 emitLifecycle 更新 applicationLifecycle,
  //    避免 lint/security/health 进入 activeExecutions 后全部 completed(terminal)导致
  //    executionPhase 跌回 development（审查中途回到开发）。workflow payload 的 phase 仍逐节点切换（节点卡动态）。
  if (!(answers.code_review || (answers.file_acceptance && resume?.summary?.phase === 'code_review'))) {
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

    const report = buildReviewReport({ round: 2, status: 'passed', basedOnRevision: 1, defects: [] })
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
        codeChanges: singleFileChangeSet(runId, reportTarget, reportLines.slice(0, visible).join('\n'))
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
        summary: { phase: 'code_review', status: 'requires_user_input', message: '等待接受代码审查报告' }
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

  // 1. 结束 / 暂停计划。
  if (options.planControlAction === 'end') {
    emitLifecycle(exec(runId, threadId, page.id, 'finalize_project', 'completed'))
    return emit(
      'finalize_project',
      'completed',
      undefined,
      {},
      { summary: { phase: 'finalize_project', status: 'completed', message: '计划已结束' } }
    )
  }
  if (options.planControlAction === 'stop') {
    const lifecycle = emitLifecycle(exec(runId, threadId, page.id, 'build', 'stopped'))
    return emit('build', 'stopped', lifecycle)
  }

  // 2. 验收通过 → 计划完成。
  if (answers.page_acceptance) {
    const lifecycle = emitLifecycle(exec(runId, threadId, page.id, 'acceptance', 'completed'))
    onContent?.(`「${page.label}」已完成交付，可继续设计其他页面或接口。`)
    return emit('acceptance', 'completed', lifecycle)
  }

  // 3. 授权决策 → 继续构建 → 等待验收。
  if (answers.agent_approval) {
    const running = emitLifecycle(exec(runId, threadId, page.id, 'build', 'running'))
    emit('build', 'running', running)
    await delay(900)
    const awaiting = emitLifecycle(
      exec(runId, threadId, page.id, 'build', 'awaiting_user', acceptanceInteraction())
    )
    // 验收态 workflow 需带 clarification.mode='page_acceptance'，
    // 否则 pageAcceptanceContinuationMessage 返回空、验收提交被拦截。
    return emit(
      'acceptance',
      'requires_user_input',
      awaiting,
      {
        lifecycle: awaiting,
        clarification: {
          mode: 'page_acceptance',
          status: 'requires_user_input',
          message: '请预览页面并完成最终验收。',
          questions: []
        }
      },
      {
        summary: {
          phase: 'acceptance',
          status: 'requires_user_input',
          message: '页面已准备好，等待最终验收'
        }
      }
    )
  }

  // 4. 详情审阅确认 → 完整代码构建 → 等待文件授权。
  // 开发阶段只负责把代码产物写入工作区；启动、非功能和业务测试统一由测试阶段执行。
  // agent_approval 仅数据库高危操作才触发，普通页面构建默认不走。
  if (answers.detail_review || resume) {
    const steps: Record<string, unknown>[] = []
    const buildTargets = buildFileTargets(page.id, includesRecheckEndpoint)
    const workflowStepTotal = Math.max(1, buildTargets.length + 1)
    const acceptedCount = acceptedBuildIndex(answers, buildTargets)
    const activeTarget = buildTargets[acceptedCount]

    const activeStepId = activeTarget
      ? codeBuildStepId(activeTarget)
      : buildTargets.length > 0
        ? codeBuildStepId(buildTargets[buildTargets.length - 1])
        : 'step-code-page'
    const pushStep = (step: Record<string, unknown>): void => {
      steps.push(step)
      onProcessSteps?.(withProcessStepTotal(steps as ProcessStepRecord[], workflowStepTotal))
    }
    const updateStep = (id: string, patch: Record<string, unknown>): void => {
      const index = steps.findIndex((step) => step.id === id)
      if (index < 0) return
      steps[index] = { ...steps[index], ...patch }
      onProcessSteps?.(withProcessStepTotal(steps as ProcessStepRecord[], workflowStepTotal))
    }

    /** 文件全部确认后运行预览节点，并让右侧面板在节点完成后进入浏览器预览。 */
    const completeWithPreview = async (): Promise<WorkflowRunPayload> => {
      markPageDesigned(page.id)
      if (page.id === 'my-rechecks') markEndpointDesigned('rechecks', 'ep-my-rechecks')
      const previewStep = {
        id: `step-preview-${page.id}`,
        kind: 'workflow',
        status: 'running',
        title: '启动应用预览',
        detail: '正在启动应用预览。',
        sequence: workflowStepTotal
      }
      const runningSteps = buildTargets.map((target, index) => ({
        id: codeBuildStepId(target),
        kind: 'workflow',
        status: 'completed',
        title: codeBuildStepTitle(target),
        detail: '文件变更已接受。',
        sequence: index + 1
      }))
      runningSteps.push(previewStep)
      onProcessSteps?.(
        withProcessStepTotal(runningSteps as ProcessStepRecord[], workflowStepTotal)
      )
      emit(
        'launch_project',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'running'))
      )
      await delay(420)
      const completedSteps = [...runningSteps]
      completedSteps[completedSteps.length - 1] = {
        ...completedSteps[completedSteps.length - 1],
        status: 'completed',
        detail: '代码文件已保存，右侧已切换到浏览器预览。'
      }
      onProcessSteps?.(
        withProcessStepTotal(completedSteps as ProcessStepRecord[], workflowStepTotal)
      )
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
            message: '代码产物已完成，已进入应用预览'
          }
        }
      )
    }

    // 最后一个文件的 Diff 接受就是开发交付终态；文件已在前置动作中落库，
    // 这里直接提交“启动预览”节点，不能再把已完成的检查/任务准备流程播放一遍。
    if (
      resume?.summary?.phase === 'build' &&
      buildTargets.length > 0 &&
      typeof answers.file_acceptance === 'string' &&
      acceptedCount >= buildTargets.length
    ) {
      return completeWithPreview()
    }

    // 一个源码文件对应一个单一职责节点；页面与接口依赖节点按顺序分别交付。
    for (let index = 0; index < acceptedCount; index += 1) {
      const target = buildTargets[index]
      pushStep({
        id: codeBuildStepId(target),
        kind: 'workflow',
        status: 'completed',
        title: codeBuildStepTitle(target),
        detail: '文件变更已接受。',
        sequence: index + 1
      })
    }
    if (activeTarget) {
      pushStep({
        id: codeBuildStepId(activeTarget),
        kind: 'workflow',
        status: 'pending',
        title: codeBuildStepTitle(activeTarget),
        detail: '正在检查工作区并准备生成代码。',
        sequence: acceptedCount + 1
      })
    }

    // 代码文件全部接受后进入预览节点；启动、非功能和业务测试统一放到测试阶段。
    const runRemainingNodes = async (): Promise<WorkflowRunPayload> => {
      markPageDesigned(page.id)
      if (page.id === 'my-rechecks') markEndpointDesigned('rechecks', 'ep-my-rechecks')
      return completeWithPreview()
    }

    // 工作区检查、依赖上下文和任务准备属于整条开发工作流的前置节点，
    // 接受接口文件后续跑页面文件时不重复执行，直接进入下一个代码节点。
    if (acceptedCount === 0) {
      emit(
        'inspect_workspace',
        'completed',
        emitLifecycle(exec(runId, threadId, page.id, 'inspect_workspace', 'completed'))
      )
      updateStep(activeStepId, { detail: '工作区结构已确认，正在准备页面实现任务。' })
      await delay(250)

      if (includesRecheckEndpoint) {
        emit(
          'inspect_database_context',
          'running',
          emitLifecycle(exec(runId, threadId, page.id, 'inspect_database_context', 'running'))
        )
        await delay(300)
        emit(
          'inspect_database_context',
          'completed',
          emitLifecycle(exec(runId, threadId, page.id, 'inspect_database_context', 'completed'))
        )
        updateStep(activeStepId, {
          detail: '已确认 GET /api/rechecks/my 的契约、数据来源与页面响应绑定。'
        })
        await delay(120)
      }

      emit(
        'prepare_build_tasks',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'prepare_build_tasks', 'running'))
      )
      await delay(450)
      emit(
        'prepare_build_tasks',
        'completed',
        emitLifecycle(exec(runId, threadId, page.id, 'prepare_build_tasks', 'completed'))
      )
      updateStep(activeStepId, {
        detail: includesRecheckEndpoint
          ? `已把「${page.label}」页面与 GET /api/rechecks/my 编排为同一个实现任务。`
          : `已为「${page.label}」准备代码实现任务。`
      })
      await delay(120)
    }

    updateStep(activeStepId, {
      status: 'running',
      detail:
        acceptedCount > 0
          ? `已接受接口代码，开始生成「${activeTarget?.name || page.label}」页面代码。`
          : '开始生成当前代码文件，完成后等待一次 Diff 授权。'
    })

    // build：逐文件写入（接口 → 页面）。每个文件先在对应页签内渐进呈现单文件 Diff，
    // 再暂停等待“接受”（消息中的文件改动卡）；一个文件接受后才继续下一个文件。
    emit(
      'build',
      'running',
      emitLifecycle(exec(runId, threadId, page.id, 'build', 'running')),
      {}
    )
    if (acceptedCount > 0) {
      updateStep(activeStepId, {
        detail: `已接受前置代码文件，继续执行${activeTarget ? codeBuildStepTitle(activeTarget) : '开发'}节点。`
      })
    }
    if (activeTarget) {
      const target = activeTarget
      const lines = target.content.split('\n')
      const chunkSize = 12
      updateStep(activeStepId, { detail: `正在生成 ${target.name}，等待确认文件 Diff。` })
      for (let visible = chunkSize; ; visible += chunkSize) {
        await delay(380)
        emit(
          'build',
          'running',
          emitLifecycle(exec(runId, threadId, page.id, 'build', 'running')),
          {
            codeChanges: singleFileChangeSet(
              runId,
              target,
              lines.slice(0, visible).join('\n')
            )
          }
        )
        if (visible >= lines.length) break
      }
      await delay(250)
      const filePending = {
        id: `pi-file-${acceptedCount}-${Date.now()}`,
        type: 'file_acceptance',
        basedOnRevision: 1,
        payload: { message: `等待接受 ${target.name}` },
        createdAt: new Date().toISOString()
      }
      return emit(
        'build',
        'requires_user_input',
        emitLifecycle(exec(runId, threadId, page.id, 'build', 'awaiting_user', filePending)),
        {
          clarification: {
            mode: 'file_acceptance',
            status: 'requires_user_input',
            message: `「${target.name}」已生成，请在右侧确认 Diff 后接受。`
          },
          codeChanges: singleFileChangeSet(runId, target, target.content)
        },
        {
          summary: {
            phase: 'build',
            status: 'requires_user_input',
            message: `等待接受 ${target.name}`
          }
        }
      )
    }
    updateStep(activeStepId, {
      status: 'completed',
      title: activeTarget ? codeBuildStepTitle(activeTarget) : '代码实现',
      detail: '代码文件均已接受，开发节点完成。'
    })
    await delay(300)

    // 文件全部接受后结束开发阶段；测试阶段会重新创建应用级测试会话。
    return await runRemainingNodes()
  }

  // 5. 开始页面设计（DetailConfirmationPageSelector 点"开始生成"）→ 详情审阅。
  if (options.selectedPageId || options.detailTargetType || options.originalRequest) {
    // 注：不在开始设计时 markPageDesigned——「已设计」仅在用户确认详细设计(detail_review)后标记。
    // 详情审阅需要 lifecycle 快照中的 pendingInteraction（page_design_confirmation）才能提交确认。
    // 生成中以 processSteps 持续承载设计与代码节点，保持同一条研发工作流轨迹。
    const designSteps: Array<{ id: string; title: string; detail: string }> = [
      {
        id: 'design-context',
        title: '汇总应用上下文',
        detail: includesRecheckEndpoint
          ? '整合已确认的应用约束、项目计划、页面目标和依赖接口契约。'
          : '整合已确认的应用约束、项目计划与页面目标。'
      },
      {
        id: 'design-scope',
        title: '梳理页面范围',
        detail: includesRecheckEndpoint
          ? '明确页面职责、核心用户路径，以及页面与 GET /api/rechecks/my 的调用边界。'
          : '明确页面职责、核心功能与关键用户路径。'
      },
      {
        id: 'design-breakdown',
        title: '拆解功能与数据',
        detail: includesRecheckEndpoint
          ? '拆解筛选、列表、状态反馈，并绑定接口请求参数和响应数据。'
          : '整理功能点、数据展示与交互依赖。'
      },
      {
        id: 'design-edge',
        title: '补齐边界与验收',
        detail: includesRecheckEndpoint
          ? '补齐接口异常、空数据、权限边界和页面验收标准。'
          : '定义异常态、边界约束与验收标准。'
      }
    ]
    const steps: ProcessStepRecord[] = []
    const buildTargets = buildFileTargets(page.id, includesRecheckEndpoint)
    const workflowTotal = designSteps.length + Math.max(1, buildTargets.length + 1)
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
      if (includesRecheckEndpoint && stage.id === 'design-breakdown') {
        // 只有完成“拆解功能与数据”后才确认页面依赖接口，并把接口加入同一会话。
        callbacks.onArtifactDiscovered?.([endpointArtifactId('rechecks', 'ep-my-rechecks')])
      }
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

  // 6. 其它（自由聊天/未知）→ 最小 running 态，不崩。
  const fallback = emitLifecycle(exec(runId, threadId, page.id, 'build', 'running'))
  return emit('build', 'running', fallback)
}
