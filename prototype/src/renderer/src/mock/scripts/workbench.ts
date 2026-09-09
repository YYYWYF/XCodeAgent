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
  buildPageSource,
  type PageDesign
} from '../../workbenchArtifacts'
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
  resolveEndpointTarget,
  wf,
  withProcessStepTotal,
  workflowPageId,
  type BuildFileTarget,
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

  // 3. 启动验收：右侧打开产物审查（审查节点先执行），随后挂起验收确认节点。
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

      // ——③ 页面产物：代码变更确认接受——落终态 + 产物审查，随后进入接口产物闭环——
      if (pageChoice === 'sync' && pageSyncPending && !pageSyncConfirmed && resume) {
        onProcessSteps?.([
          step(confirmNode.id, 'completed', confirmNode.title, '页面代码变更已接受。'),
          step(previewNode.id, 'running', previewNode.title, '正在打开当前产物的审查视图。')
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
            '代码文件已保存，右侧已切换到开发产物，请审查当前产物。'
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
              preview_url: MOCK_APPLICATION_PREVIEW_URL + page.path.replace(/^\//, ''),
              review_target: { type: 'page', pageId: page.id }
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
          step('endpoint-confirm-changes', 'completed', confirmNode.title, '接口代码变更已接受。'),
          step(
            'endpoint-launch-preview',
            'running',
            previewNode.title,
            '正在打开当前产物的审查视图。'
          )
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
            '代码文件已保存，右侧已切换到开发产物，请审查当前产物。'
          )
        ])
        markEndpointDesigned('rechecks', 'ep-my-rechecks')
        onContent?.('接口交付完成。')
        return emit(
          'build',
          'completed',
          emitLifecycle(exec(runId, threadId, page.id, 'build', 'completed')),
          {},
          {
            result: {
              review_target: {
                type: 'endpoint',
                apiContractId: 'rechecks',
                endpointId: 'ep-my-rechecks'
              }
            },
            summary: { phase: 'build', status: 'completed', message: '接口实现已完成' }
          }
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
    // 同步执行的代码变更确认续跑：接受 Diff 后补播构建检查与产物审查，产物状态由文件快照推导。
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
          detail: '正在打开当前产物的审查视图。',
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
          detail: '代码文件已保存，右侧已切换到开发产物，请审查当前产物。',
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
