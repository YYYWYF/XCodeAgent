// 工作台剧本：模拟后端 AG-UI 事件流 + 生命周期。
// 开发阶段前台负责「设计与选择」：详细设计确认后在「选择执行方式」节点选择同步执行
// （对话内当场执行并落任务记录）或后台资源池（异步/潮汐，见 mock/backgroundTaskEngine.ts）；
// 后台任务由引擎无人值守执行到「完成」，验收入口挂在任务条目上。
// 审查/测试/验收阶段剧本见 workbenchStageFlows.ts；共享构造见 workbenchShared.ts。
import type {
  ApplicationLifecycle,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
import {
  buildEndpointSource,
  buildEntityAdapterSource,
  buildPageSource,
  type PageDesign
} from '../../workbenchArtifacts'
import { readBusinessObjectsSnapshot, saveBusinessObjects } from '../../components/BusinessObjects/store'
import {
  entityBindingPlan,
  withConfirmedBindings,
  type EntityBindingPlanRow
} from '../../components/BusinessObjects/model'
import { readDataSources } from '../../components/DataSources/catalog'
import {
  BACKGROUND_TASK_SYSTEM_LABEL,
  acceptArtifactTask,
  dispatchArtifactImplementationTask,
  findAwaitingArtifactTask,
  type BackgroundDispatchChoice,
  type BackgroundTaskExecTarget
} from '../../backgroundTasks'
import { ensureBackgroundTaskEngine } from '../backgroundTaskEngine'
import { workflowNode, workflowSegmentNodes } from '../workflowGraphs'
import { endpointArtifactId, pageArtifactId } from '../../workbenchDomain'
// 页面/接口契约基座：单一 pms-new 场景（需求回检单模块）。
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { markEndpointDesigned, markPageDesigned } from '../designState'
import { appPath } from '../workspaceFiles'
import { nextLifecycleRevision } from './revision'
import {
  MOCK_APPLICATION_PREVIEW_URL,
  addedFileChange,
  delay,
  exec,
  endpointMeta,
  makeBaseLifecycle,
  makeEmitLifecycle,
  pageMeta,
  step,
  resolveEndpointTarget,
  resolveEntityTarget,
  streamCodeFrames,
  wf,
  withProcessStepTotal,
  workflowPageId,
  type BuildFileTarget,
  type ChangeSource,
  type ReplayCallbacks,
  type WorkbenchExecutionLike
} from './workbenchShared'
export {
  replayApplicationAcceptance,
  replayApplicationTesting,
  replayCodeReview
} from './workbenchStageFlows'

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
 * 工作流打开右侧产物审查（页面预览/接口调试）并在对话区承载验收确认；
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
          step(previewNode, 'completed', 1),
          step(confirmNode, 'completed', 2, '产物已确认交付。')
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
  // 无效入口只回纯文本答复、不挂工作流卡：同一条消息里不允许工作流与文字混排。
  if (!task) {
    onContent?.(`${targetLabel}当前没有待验收的实现任务。`)
    const lifecycle = emitLifecycle('completed')
    return {
      runId,
      threadId,
      summary: { phase: 'acceptance', status: 'completed', message: '无待验收任务', ...(lifecycle ? { lifecycle } : {}) },
      events: [],
      state: { ...identity, ...(lifecycle ? { lifecycle } : {}) },
      result: { ...identity, ...(lifecycle ? { lifecycle } : {}) }
    } as unknown as WorkflowRunPayload
  }

  // 3. 启动验收：右侧打开产物审查（审查节点先执行），随后挂起验收确认节点。
  onProcessSteps?.(
    withProcessStepTotal(
      [
        step(previewNode, 'running', 1)
      ],
      acceptanceNodes.length
    )
  )
  await delay(500)
  onProcessSteps?.(
    withProcessStepTotal(
      [
        step(previewNode, 'completed', 1),
        step(confirmNode, 'requires_user_input', 2)
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
        message: `请在右侧审查确认${targetLabel}的实现内容，确认后接受产物。`,
        questions: []
      }
    },
    {
      summary: { phase: 'acceptance', status: 'requires_user_input', message: '等待产物验收' }
    }
  )
}

// 审查阶段检查矩阵（规范 / 安全 / 健康度 三项通过）。

// 把生成的完整文件内容包装成新增文件的行级 Diff（bare diff 由前端自动补统一格式头）。
// 页面产物只交付页面文件：数据能力由实体操作的实现提供，页面不再携带依赖接口文件。
function buildFileTargets(pageId: string): BuildFileTarget[] {
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
  return targets
}

/** 生成独立接口会话的代码交付目标。 */
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

/** 剧本 emit 闭包的统一形状：页面/接口剧本的快照发射器签名一致。 */
type EmitFn = (
  phase: string,
  status: string,
  lifecycle: ApplicationLifecycle | undefined,
  state?: Record<string, unknown>,
  extra?: Partial<WorkflowRunPayload>
) => WorkflowRunPayload

/**
 * 页面/接口同步执行的共享前台构建编排：构建计划 → 分帧生成代码 → 挂「确认代码变更」节点。
 * exec 由两个剧本各自提供（execution 域不同），其余节奏、状态与载荷完全一致。
 */
async function runForegroundBuild(input: {
  runId: string
  buildTargets: BuildFileTarget[]
  emit: EmitFn
  emitLifecycle: (execution: WorkbenchExecutionLike) => ApplicationLifecycle
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
  exec: (phase: string, status: string, pendingInteraction?: Record<string, unknown>) => WorkbenchExecutionLike
  /** 个别节点在特定产物域下的文案覆盖（如接口的生成代码节点）。 */
  generateCodeDetail?: string
  fileAcceptanceMessage: string
}): Promise<WorkflowRunPayload> {
  const { emit, emitLifecycle, onProcessSteps, exec } = input
  const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
  const generateNode = foregroundNodes.find((node) => node.id === 'generate_code')!
  // 生成构建计划属于后台分析动作，不在对话轨迹中展示；直接进入生成代码。
  emit('build_dag', 'running', emitLifecycle(exec('build_dag', 'running')))
  await delay(900)
  const nodeDetail = (node: { id: string; detail: string }): string =>
    input.generateCodeDetail && node.id === 'generate_code' ? input.generateCodeDetail : node.detail
  // 生成代码：按行分帧渐进写入 Diff，模拟一段一段生成的过程。
  onProcessSteps?.([step(generateNode, 'running', 1, nodeDetail(generateNode))])
  const generateLifecycle = emitLifecycle(exec('generate_code', 'running'))
  await streamCodeFrames(input.buildTargets, { linesPerFrame: 8, intervalMs: 400 }, (finished, partial) => {
    emit('generate_code', 'running', generateLifecycle, {
      codeChanges: changeSetFromContents(input.runId, [...finished, partial])
    })
  })
  onProcessSteps?.([step(generateNode, 'completed', 1, nodeDetail(generateNode))])
  // 生成代码完成：携带代码变更集，挂「确认代码变更」待输入节点（右侧源码区打开 Diff）。
  const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
  onProcessSteps?.([step(confirmNode, 'requires_user_input', 1)])
  return emit(
    'build',
    'requires_user_input',
    emitLifecycle(exec('build', 'awaiting_user', fileAcceptanceInteraction())),
    {
      clarification: {
        mode: 'file_acceptance',
        status: 'requires_user_input',
        message: input.fileAcceptanceMessage
      },
      codeChanges: fullChangeSet(input.runId, input.buildTargets)
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
// 代码生成、构建检查与产物审查统一由后台实现任务无人值守执行。
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
    const buildTargets = endpointBuildTargets(meta.apiContractId, meta.endpointId)
    const syncImplementEndpoint = (): Promise<WorkflowRunPayload> =>
      runForegroundBuild({
        runId,
        buildTargets,
        emit,
        emitLifecycle,
        onProcessSteps,
        exec: (phase, status, pendingInteraction) =>
          execEndpoint(runId, threadId, meta.apiContractId, meta.endpointId, phase, status, pendingInteraction),
        generateCodeDetail: '按契约生成接口实现与数据访问代码。',
        fileAcceptanceMessage: '接口代码已生成，请在右侧确认 Diff 后接受。'
      })

    if (choice === 'sync') return syncImplementEndpoint()
    // 接口同步执行的代码变更确认续跑：接受 Diff 后补播构建检查，并落任务终态。
    if (answers.file_acceptance && resume) {
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const buildNode = foregroundNodes.find((node) => node.id === 'build_and_test')!
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      onProcessSteps?.([
        step(confirmNode, 'completed', 1, '已接受本次生成的代码变更，继续构建。')
      ])
      onProcessSteps?.([
        step(buildNode, 'running', 1)
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
        step(buildNode, 'completed', 1)
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
      step(dispatchNode, 'completed', 1, `已创建后台接口实现任务（${BACKGROUND_TASK_SYSTEM_LABEL[choice]}），可在对应任务系统查看执行进度。`)
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
export async function replayWorkbench(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  // 页面/实体工作流全程不写正文文本：指引由步骤详情与授权条承载，消息里不混排文字。
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  // 实体目标优先：实体开发独立于页面与接口目标（对话区确认绑定 + 生成数据适配逻辑）。
  const entityTarget = resolveEntityTarget(options, resume)
  if (entityTarget) {
    return replayEntityWorkbench(threadId, entityTarget, options, callbacks)
  }
  // 接口目标优先于页面：选中接口或续传快照带接口身份时走接口剧本。
  const endpointTarget = resolveEndpointTarget(options, resume)
  if (endpointTarget) {
    return replayEndpointWorkbench(threadId, endpointTarget, options, callbacks)
  }
  const runId = resume?.runId || `mock-run-${Date.now()}`
  const page = pageMeta(options.selectedPageId || workflowPageId(resume))
  const pageTaskIdentity = {
    selectedPageId: page.id,
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
  //    异步/潮汐派发后台任务后前台立即收口；页面产物只交付页面文件，数据由实体操作提供。
  if (answers.detail_review || resume) {
    // 同步执行的代码变更确认续跑：接受 Diff 后补播构建检查与产物审查，产物状态由文件快照推导。
    if (answers.file_acceptance && resume) {
      const foregroundNodes = workflowSegmentNodes('development', 'foreground_build')
      const confirmNode = foregroundNodes.find((node) => node.id === 'confirm_changes')!
      const buildNode = foregroundNodes.find((node) => node.id === 'build_and_test')!
      const previewNode = foregroundNodes.find((node) => node.id === 'launch_preview')!
      onProcessSteps?.([
        step(confirmNode, 'completed', 1, '已接受本次生成的代码变更，继续构建。')
      ])
      onProcessSteps?.([
        step(buildNode, 'running', 1)
      ])
      emit(
        'build_and_test',
        'running',
        emitLifecycle(exec(runId, threadId, page.id, 'build_and_test', 'running'))
      )
      await delay(1300)
      onProcessSteps?.([
        step(buildNode, 'completed', 1)
      ])
      onProcessSteps?.([
        step(previewNode, 'running', 1, '正在打开当前产物的审查视图。')
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
      onProcessSteps?.([
        step(previewNode, 'completed', 1, '代码文件已保存，右侧已切换到开发产物，请审查当前产物。')
      ])
      return emit(
        'launch_project',
        'completed',
        emitLifecycle(exec(runId, threadId, page.id, 'launch_project', 'completed')),
        {},
        {
          result: {
            preview_url: `${MOCK_APPLICATION_PREVIEW_URL}${page.path.replace(/^\//, '')}`,
            review_target: { type: 'page', pageId: page.id }
          },
          summary: {
            phase: 'launch_project',
            status: 'completed',
            message: '代码产物已完成，已切换到产物审查'
          }
        }
      )
    }
    const choice = resolveDispatchChoice(answers)
    const artifactId = pageArtifactId(page.id)
    const relatedArtifactIds = [artifactId]
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
    const buildTargets = buildFileTargets(page.id)
    const syncImplementPage = (): Promise<WorkflowRunPayload> =>
      runForegroundBuild({
        runId,
        buildTargets,
        emit,
        emitLifecycle,
        onProcessSteps,
        exec: (phase, status, pendingInteraction) =>
          exec(runId, threadId, page.id, phase, status, pendingInteraction),
        fileAcceptanceMessage: '代码已生成，请在右侧确认 Diff 后接受。'
      })

    if (choice === 'sync') return syncImplementPage()
    dispatchImplementationTask({
      options,
      title: `页面「${page.label}」代码实现`,
      artifactIds: relatedArtifactIds,
      primaryArtifactId: artifactId,
      execTarget: { type: 'page', pageId: page.id, includeEndpoint: false },
      choice: choice
    })
    // 设计确认即标记「已设计」：下一页面的模板/详设卡可以立即自动投放。
    markPageDesigned(page.id)
    // 选择节点落成已完成，再追加派发收口节点：合并回话按 id 归位并接在同一轨迹末尾。
    const dispatchNode = workflowNode('development', 'background_dispatch')
    onProcessSteps?.([
      choiceStep(
        'completed',
        `已选择${BACKGROUND_TASK_SYSTEM_LABEL[choice]}，任务进入对应后台队列执行。`
      ),
      step(dispatchNode, 'completed', 1, `已创建后台代码实现任务（${BACKGROUND_TASK_SYSTEM_LABEL[choice]}），可在对应任务系统查看执行进度。`)
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
    // 页面详设四步使用开发工作流 DAG 的中性文案：页面调用 实体.操作()，不再绑定依赖接口。
    const designSteps = workflowSegmentNodes('development', 'design')
      .filter((node) => node.id !== 'choose_execution')
      .map((node) => ({
        id: node.id,
        title: node.title,
        detail: node.detail
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
          message: '正在生成页面详细设计…'
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
          message: '页面详细设计已生成'
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

// —— 实体（business-object）工作台剧本 ——
// 读取实体结构 → 对话区「确认绑定」卡逐操作确认数据实现 → 生成数据适配逻辑 → 确认代码变更。
// 绑定/映射细节全部在对话卡内完成确认，右侧只按确认结果静态呈现；同步单通道执行，不进任务池。
async function replayEntityWorkbench(
  threadId: string,
  target: { objectId: string },
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload | undefined> {
  // 实体工作流全程不写正文文本：指引由步骤详情与交互卡承载，消息里不混排文字。
  const { onWorkflow, onApplicationLifecycle, onProcessSteps } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-entity-${Date.now()}`
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const scenario = appDataByWorkspace()
  // 实体绑定按版本隔离：读写都必须落在当前工作版本自己的缓存键上。
  const entityVersionId = options.application?.currentVersionId || 'current'
  const objects = readBusinessObjectsSnapshot(scenario.requirementSpec, entityVersionId)
  const object = objects.find((item) => item.id === target.objectId) || objects[0]
  // 轨迹节点取自开发工作流底层 DAG 的实体开发段 + 通用「确认代码变更」节点。
  const entityNodes = workflowSegmentNodes('development', 'entity')
  const structureNode = entityNodes.find((node) => node.id === 'entity_read_structure')!
  const bindingNode = entityNodes.find((node) => node.id === 'entity_confirm_binding')!
  const adapterNode = entityNodes.find((node) => node.id === 'entity_generate_adapter')!
  const confirmNode = workflowNode('development', 'confirm_changes')
  const stepTotal = entityNodes.length + 1

  // 实体缺位（规划数据被清理等）时直接给完成态，避免悬挂的等待轨迹。
  if (!object) {
    const fallbackLifecycle = makeEmitLifecycle(
      makeBaseLifecycle(options.application),
      runId,
      onApplicationLifecycle
    )
    return wf(
      threadId,
      runId,
      'build',
      'completed',
      fallbackLifecycle({ scope: 'entity', targetId: target.objectId, threadId, runId, phase: 'build', status: 'completed', startedAt: '', updatedAt: '' }),
      { selectedObjectId: target.objectId, detailTargetType: 'business-object' },
      { summary: { phase: 'build', status: 'completed', message: '未找到该实体的规划数据' } }
    )
  }

  const identity = {
    selectedObjectId: object.id,
    detailTargetType: 'business-object'
  }
  const entityWf = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
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
  const baseLifecycle = makeBaseLifecycle(options.application)
  const emitLifecycle = makeEmitLifecycle(baseLifecycle, runId, onApplicationLifecycle)
  const emit = (
    phase: string,
    status: string,
    lifecycle: ApplicationLifecycle | undefined,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = entityWf(phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }
  const execEntity = (
    phase: string,
    status: string,
    pendingInteraction?: Record<string, unknown>
  ): WorkbenchExecutionLike => {
    const now = new Date().toISOString()
    return {
      scope: 'entity',
      targetId: object.id,
      resourceKeys: [`business-object:${object.id}`],
      threadId,
      runId,
      phase,
      status,
      startedAt: now,
      updatedAt: now,
      ...(pendingInteraction ? { pendingInteraction } : {})
    }
  }
  /** 实体绑定确认交互：对话卡内逐操作确认数据实现，右侧不承载编辑动作。 */
  const entityBindingInteraction = (plan: EntityBindingPlanRow[]): Record<string, unknown> => ({
    id: `pi-entity-binding-${Date.now()}`,
    type: 'entity_binding',
    basedOnRevision: 1,
    payload: { objectName: object.name, operations: plan },
    createdAt: new Date().toISOString()
  })

  // 1. 绑定确认续跑：把意向写回实体演示状态（右侧随之呈现已绑定），再当场生成数据适配逻辑。
  if (answers.entity_binding) {
    const sources = readDataSources()
    const confirmedObject = withConfirmedBindings(object, sources)
    const confirmedCount = confirmedObject.operations.filter(
      (operation) => operation.implementation.confirmed
    ).length
    saveBusinessObjects(
      scenario.requirementSpec,
      objects.map((item) => (item.id === confirmedObject.id ? confirmedObject : item)),
      entityVersionId
    )
    onProcessSteps?.(
      withProcessStepTotal(
        [
          step(structureNode, 'completed', 1),
          step(bindingNode, 'completed', 2, `已确认 ${confirmedCount} 个操作的数据实现与字段映射，绑定结果已写入实体开发产物。`),
          step(adapterNode, 'running', 3)
        ],
        stepTotal
      )
    )
    // 生成代码：按行分帧渐进写入 Diff，右侧源码区逐帧跟随（generate_code 阶段）。
    const generateLifecycle = emitLifecycle(execEntity('generate_code', 'running'))
    emit('generate_code', 'running', generateLifecycle)
    const adapterSource = buildEntityAdapterSource(confirmedObject)
    const adapterTarget: BuildFileTarget = {
      key: `entity-${object.id}`,
      name: adapterSource.filePath.split('/').pop() || 'EntityAdapter.java',
      path: appPath(adapterSource.filePath),
      content: adapterSource.content,
      sourceTool: 'entity_adapter_generator'
    }
    await streamCodeFrames([adapterTarget], { linesPerFrame: 8, intervalMs: 400 }, (_finished, partial) => {
      emit('generate_code', 'running', generateLifecycle, {
        codeChanges: changeSetFromContents(runId, [partial])
      })
    })
    onProcessSteps?.(
      withProcessStepTotal(
        [
          step(structureNode, 'completed', 1),
          step(bindingNode, 'completed', 2, `已确认 ${confirmedCount} 个操作的数据实现与字段映射，绑定结果已写入实体开发产物。`),
          step(adapterNode, 'completed', 3, '已按确认的绑定生成查询、组合、转换与本地业务规则。'),
          step(confirmNode, 'requires_user_input', 4)
        ],
        stepTotal
      )
    )
    return emit(
      'build',
      'requires_user_input',
      emitLifecycle(execEntity('build', 'awaiting_user', fileAcceptanceInteraction())),
      {
        clarification: {
          mode: 'file_acceptance',
          status: 'requires_user_input',
          message: '实体数据适配代码已生成，请在右侧确认 Diff 后接受。'
        },
        codeChanges: fullChangeSet(runId, [adapterTarget])
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

  // 2. 代码变更确认续跑：接受 Diff 后工作流收口，实体状态由绑定确认结果推导为已完成。
  if (answers.file_acceptance && resume) {
    onProcessSteps?.(
      withProcessStepTotal(
        [
          step(structureNode, 'completed', 1),
          step(bindingNode, 'completed', 2, '已确认全部操作的数据实现与字段映射。'),
          step(adapterNode, 'completed', 3, '已按确认的绑定生成查询、组合、转换与本地业务规则。'),
          step(confirmNode, 'completed', 4, '已接受本次生成的代码变更，实体交付完成。')
        ],
        stepTotal
      )
    )
    // 完成态不追加纯文本正文：收口只留工作流卡，避免同一条消息里工作流与文字混排。
    return emit(
      'build',
      'completed',
      emitLifecycle(execEntity('build', 'completed')),
      {},
      { summary: { phase: 'build', status: 'completed', message: '实体开发已完成' } }
    )
  }

  // 3. 启动：读取实体结构后挂「绑定操作的数据实现」待输入节点，绑定方案整卡呈现在对话区。
  emit('detail_confirmation', 'running', emitLifecycle(execEntity('detail_confirmation', 'running')))
  onProcessSteps?.(
    withProcessStepTotal(
      [
        step(structureNode, 'running', 1)
      ],
      stepTotal
    )
  )
  await delay(650)
  const plan = entityBindingPlan(object, readDataSources())
  onProcessSteps?.(
    withProcessStepTotal(
      [
        step(structureNode, 'completed', 1),
        step(bindingNode, 'requires_user_input', 2)
      ],
      stepTotal
    )
  )
  // 启动不追加纯文本正文：绑定方案的说明由确认卡自身承载，工作流消息里不混排文字（对齐页面工作流）。
  const clarification = {
    mode: 'entity_binding',
    status: 'requires_user_input',
    message: `请确认「${object.name}」各操作的数据实现，确认后生成数据适配逻辑。`,
    objectName: object.name,
    operations: plan
  }
  return emit(
    'detail_confirmation',
    'requires_user_input',
    emitLifecycle(
      execEntity('detail_confirmation', 'awaiting_user', entityBindingInteraction(plan))
    ),
    { clarification },
    {
      summary: {
        phase: 'detail_confirmation',
        status: 'requires_user_input',
        message: '等待确认数据实现绑定'
      }
    }
  )
}
