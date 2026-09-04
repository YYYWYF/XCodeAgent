// 规划会话剧本：模拟后端 AG-UI 事件流，把需求分析/项目规划两阶段对话按工作流形式推进：
// 分析段（汇总上下文 → 业务分析 → 澄清门 → 生成需求文档 → 确认需求文档门）
// 计划段（读取已确认需求 → 规划页面与接口 → 生成项目计划 → 确认项目计划门）。
// 节点轨迹以 mock/workflowGraphs.ts 注册的 DAG 为唯一来源，剧本只负责选段播放；
// 节点序列对齐真实工程应用规划 Graph 的 requirements/project_planning 语义，
// 用户交互靠 summary.status='requires_user_input' 与 state.clarification，阶段判定沿用
// planningWorkflowState 的 phase 约定（'requirements' / 'project_planning'）。
// 澄清问题 / 需求文档 / 项目计划均为按真实后端 prompt 生成的数据（见 mock-data/）。

import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationLifecycleStage,
  WorkflowEvent,
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'
import type {
  ProcessStepRecord,
  SendWorkflowMessageOptions
} from '../../service/agUiAgent'
import { buildProjectPlanDoc, buildRequirementSpecDoc } from '../../workbenchArtifacts'
import { WORKSPACE_DOC_PATHS, appPath } from '../workspaceFiles'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { nextLifecycleRevision } from './revision'
import { workflowNode } from '../workflowGraphs'

type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

// —— 工作流轨迹播放器 ——
// 按 DAG 节点累积 ProcessStep 轨迹与节点事件：同一节点重复 set 时原位更新状态，
// 每次变更立即全量发射轨迹；事件随 payload 落盘，历史会话重放时可据此重建节点过程。

type PlanningTrajectory = {
  /** 把节点推进到目标状态；detailOverride 用于差异化说明同一节点的本轮动作。 */
  set: (nodeId: string, status: ProcessStepRecord['status'], detailOverride?: string) => void
  /** 当前已累积的事件快照，供 wf() 写入 payload 的 events 字段。 */
  events: () => WorkflowEvent[]
}

function createTrajectory(
  workflowId: string,
  onProcessSteps?: ReplayCallbacks['onProcessSteps']
): PlanningTrajectory {
  const steps: ProcessStepRecord[] = []
  const events: WorkflowEvent[] = []

  const set = (
    nodeId: string,
    status: ProcessStepRecord['status'],
    detailOverride?: string
  ): void => {
    const node = workflowNode(workflowId, nodeId)
    const existingIndex = steps.findIndex((step) => step.id === nodeId)
    const record: ProcessStepRecord = {
      id: nodeId,
      kind: 'workflow',
      status,
      title: node.title,
      detail: detailOverride ?? node.detail,
      sequence: existingIndex >= 0 ? steps[existingIndex].sequence : steps.length + 1,
      nodeName: nodeId
    }
    if (existingIndex >= 0) steps[existingIndex] = record
    else steps.push(record)
    // 节点事件与轨迹同步累积：启动事件与终态事件的对齐关系与后端投影一致。
    events.push({
      type: status === 'running' ? 'workflow.node.started' : 'workflow.node.completed',
      nodeName: nodeId,
      node: { id: nodeId, label: node.title },
      status
    })
    onProcessSteps?.([...steps])
  }

  return {
    set,
    events: (): WorkflowEvent[] => events.map((event) => ({ ...event }))
  }
}

// —— 设计文档的“新增文件”变更集：与构建节点共用代码审查交互（右侧 Diff 绿色新增行）——

// 新增文件的行级变更：全部行为新增（绿色），新增行数即内容行数。
function docAddedChange(id: string, path: string, content: string): WorkspaceCodeChangeFile {
  const lines = content.split('\n')
  return {
    id,
    path,
    changeType: 'added',
    additions: lines.length,
    deletions: 0,
    diff: lines.map((line) => `+${line}`).join('\n'),
    tool: 'file.write',
    sourceTool: 'doc_generator',
    executed: true
  }
}

// 组装单文件变更集；id 随快照递增，右侧 Diff 面板按 id 变化原地刷新。
function docChangeSet(id: string, file: WorkspaceCodeChangeFile): WorkspaceCodeChangeSet {
  return {
    id,
    status: 'applied',
    workspaceRoot: 'wh-branch-pms-new',
    summary: { files: 1, additions: file.additions, deletions: 0 },
    files: [file]
  }
}

// 生成节点运行中把文档按行分块渐进发射 codeChanges 快照，
// 右侧 Diff 面板呈现“从 0 开始逐段写入”的绿色新增行，完成后的确认载荷携带完整变更集。
async function emitProgressiveDocChanges(
  threadId: string,
  phase: 'requirements' | 'project_planning',
  path: string,
  message: string,
  fullDoc: string,
  onWorkflow?: ReplayCallbacks['onWorkflow'],
  trajectory?: ReturnType<typeof createTrajectory>
): Promise<WorkspaceCodeChangeSet> {
  const lines = fullDoc.split('\n')
  // 约 9 块写完：块更小、节奏更密，让“逐步写入”的过程在演示中清晰可辨。
  const chunkSize = Math.max(6, Math.ceil(lines.length / 9))
  let snapshotIndex = 0
  for (let visible = chunkSize; ; visible += chunkSize) {
    await delay(300)
    const partial = docAddedChange(
      `cc-${phase}-partial-${visible}`,
      path,
      lines.slice(0, visible).join('\n')
    )
    onWorkflow?.(
      wf(
        threadId,
        phase,
        'running',
        { codeChanges: docChangeSet(`cc-${phase}-p${snapshotIndex++}`, partial) },
        { summary: { phase, status: 'running', message } },
        trajectory?.events()
      )
    )
    if (visible >= lines.length) break
  }
  const full = docAddedChange(`cc-${phase}-full`, path, fullDoc)
  return docChangeSet(`cc-${phase}-completed`, full)
}

// 构造 WorkflowRunPayload：phase 同时作为 summary.phase 与 nodeName；events 携带已播放节点事件。
function wf(
  threadId: string,
  phase: string,
  status: string,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {},
  events: WorkflowEvent[] = []
): WorkflowRunPayload {
  return {
    runId: `mock-run-${phase}`,
    threadId,
    summary: { phase, status, message: '' },
    events: events.length > 0 ? events : [{ type: 'workflow.node.started', nodeName: phase }],
    state,
    result: {},
    ...extra
  } as WorkflowRunPayload
}

// 需求文档 / 项目计划的 Markdown 渲染统一收敛到 workbenchArtifacts.ts（buildRequirementSpecDoc /
// buildProjectPlanDoc），规划会话与工作台右侧「文档」共用同一份渲染，避免两处分叉。

const requirementConfirmationPayload = (
  threadId: string,
  appName: string | undefined,
  requirementSpec: Record<string, unknown>,
  codeChanges?: WorkspaceCodeChangeSet,
  events: WorkflowEvent[] = [],
  clarificationHistory: Array<Record<string, unknown>> = []
): WorkflowRunPayload =>
  wf(
    threadId,
    'requirements',
    'requires_user_input',
    {
      // 工作台复用 WorkflowRunCard 渲染应用级确认：给一个 yesno 确认项，复用现有澄清卡渲染
      // （status=requires_user_input + questions），summary.status 同步为 requires_user_input。
      // 推进仍由 mode=requirement_spec_confirmation 驱动 replayPlanning 生成项目计划。
      clarificationHistory,
      clarification: {
        mode: 'requirement_spec_confirmation',
        status: 'requires_user_input',
        message: '请审核需求文档',
        questions: [
          {
            id: 'confirm_requirement_spec',
            header: '需求确认',
            type: 'yesno',
            question: '需求文档已生成，是否确认并继续生成项目计划？',
            allowOther: false
          }
        ]
      },
      requirement_spec: requirementSpec,
      ...(codeChanges ? { codeChanges } : {})
    },
    {
      result: { requirement_spec: requirementSpec },
      confirmationArtifact: {
        id: 'requirement_spec',
        name: '需求文档',
        path: WORKSPACE_DOC_PATHS.requirementSpec,
        format: 'markdown',
        content: buildRequirementSpecDoc(requirementSpec, appName)
      }
    },
    events
  )

const projectPlanConfirmationPayload = (
  threadId: string,
  appName: string | undefined,
  projectPlan: Record<string, unknown>,
  codeChanges?: WorkspaceCodeChangeSet,
  events: WorkflowEvent[] = []
): WorkflowRunPayload =>
  wf(
    threadId,
    'project_planning',
    'requires_user_input',
    {
      clarification: {
        mode: 'project_plan_confirmation',
        status: 'requires_user_input',
        message: '请审核项目计划',
        questions: [
          {
            id: 'confirm_project_plan',
            header: '计划确认',
            type: 'yesno',
            question: '项目计划已生成，是否确认并进入开发阶段？',
            allowOther: false
          }
        ]
      }
      ,
      ...(codeChanges ? { codeChanges } : {})
    },
    {
      confirmationArtifact: {
        id: 'project_plan',
        name: '项目计划',
        path: WORKSPACE_DOC_PATHS.projectPlan,
        format: 'markdown',
        content: buildProjectPlanDoc(projectPlan, appName)
      }
    },
    events
  )

/** 项目计划确认后的独立开发准入门：不再携带 Diff，只等待用户选择后台任务类型。 */
const developmentEntryConfirmationPayload = (
  threadId: string,
  events: WorkflowEvent[] = []
): WorkflowRunPayload =>
  wf(
    threadId,
    'development_entry_confirmation',
    'requires_user_input',
    {
      clarification: {
        mode: 'development_entry_confirmation',
        status: 'requires_user_input',
        message: '项目计划已确认，可以进入开发阶段。',
        questions: []
      },
      project_plan_confirmed: true
    },
    {
      result: { project_plan_confirmed: true },
      summary: {
        phase: 'development_entry_confirmation',
        status: 'requires_user_input',
        message: '项目计划已确认，可以进入开发阶段。'
      }
    },
    events
  )

/**
 * 需求文档确认后的项目规划准入门：与开发准入门（development_entry_confirmation）同一模式。
 * 门禁是工作流自身的待输入节点，弹框只是它的显示面；确认后由规划剧本接管阶段切换。
 */
const planningEntryConfirmationPayload = (
  threadId: string,
  events: WorkflowEvent[] = []
): WorkflowRunPayload =>
  wf(
    threadId,
    'planning_stage_entry',
    'requires_user_input',
    {
      clarification: {
        mode: 'planning_stage_entry',
        status: 'requires_user_input',
        message: '需求文档已确认，等待进入项目规划阶段。',
        questions: []
      },
      requirement_spec_confirmed: true
    },
    {
      result: { requirement_spec_confirmed: true },
      summary: {
        phase: 'planning_stage_entry',
        status: 'requires_user_input',
        message: '需求文档已确认，等待进入项目规划阶段。'
      }
    },
    events
  )

// 澄清问题文案里的应用名与当前应用对齐（澄清题文案里的应用名统一替换为当前应用名）。
const clarificationPayload = (
  threadId: string,
  appName: string | undefined,
  clarificationQuestions: Array<Record<string, unknown>>,
  events: WorkflowEvent[] = [],
  clarificationHistory: Array<Record<string, unknown>> = []
): WorkflowRunPayload =>
  wf(threadId, 'requirements', 'requires_user_input', {
    clarificationHistory,
    clarification: {
      mode: 'requirement_clarification',
      status: 'requires_user_input',
      message: '需要补充关键信息',
      questions: appName
        ? clarificationQuestions.map((question) => ({
            ...question,
            question: String(question.question || '').replace(/「[^」]*」/g, `「${appName}」`)
          }))
        : clarificationQuestions
    }
  }, {}, events)

// 需求或计划未确认时，回到当前阶段继续接受用户的自然语言修改意见。
const revisionPayload = (
  threadId: string,
  phase: 'requirements' | 'project_planning',
  mode: 'requirement_revision' | 'project_plan_revision',
  events: WorkflowEvent[] = [],
  clarificationHistory: Array<Record<string, unknown>> = []
): WorkflowRunPayload =>
  wf(threadId, phase, 'requires_user_input', {
    clarificationHistory,
    clarification: {
      mode,
      status: 'requires_user_input',
      message:
        phase === 'requirements'
          ? '请补充需求文档需要修改的内容'
          : '请补充项目计划需要调整的内容',
      questions: [
        {
          id: phase === 'requirements' ? 'requirement_revision' : 'project_plan_revision',
          header: phase === 'requirements' ? '需求修改意见' : '计划修改意见',
          type: 'text',
          question:
            phase === 'requirements'
              ? '请说明需求文档需要调整的业务目标、角色或流程。'
              : '请说明页面、接口或实体清单及依赖关系需要如何调整。',
          required: true,
          placeholder: '请输入修改意见…'
        }
      ]
    }
  }, {}, events)

// 读取确认卡中的 yes/no 答案，避免“否”被误推进到下一个阶段。
function answerIsYes(value: unknown): boolean {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = (value as { selected?: unknown }).selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是' || value === true
}

// —— 澄清卡历史 ——
// 同一阶段工作流复用同一条消息：已提交的澄清卡写入 payload 的 clarificationHistory，
// 前端按 nodeName 内嵌回对应节点下方，保持“第 X / Y 项”向导形态的只读回看。
// 只有真正以问题卡形态出现过的交互（需求澄清）才进入历史；产物确认由“文件改动”卡承担，不留痕。

const DESIGN_CARD_NODES: Record<string, string> = {
  requirement_clarification: 'requirements_clarify',
  requirement_spec_confirmation: 'requirements_document',
  requirement_revision: 'requirements_document',
  project_plan_confirmation: 'planning_document',
  project_plan_revision: 'planning_document'
}

/** 汇总已提交卡片历史：把本次提交的澄清问答追加到续跑快照携带的历史之后。 */
function submittedCardHistory(
  resume: WorkflowRunPayload | undefined,
  answers: Record<string, unknown> | undefined
): Array<Record<string, unknown>> {
  const history = Array.isArray(resume?.state?.clarificationHistory)
    ? [...(resume?.state?.clarificationHistory as Array<Record<string, unknown>>)]
    : []
  const clarification = (resume?.state?.clarification ??
    resume?.result?.clarification) as Record<string, unknown> | undefined
  const mode = String(clarification?.mode || '')
  if (mode !== 'requirement_clarification' || !clarification || !answers) return history
  const fingerprint = JSON.stringify({ mode, answers })
  if (history.some((entry) => entry.fingerprint === fingerprint)) return history
  return [
    ...history,
    {
      nodeName: DESIGN_CARD_NODES[mode],
      mode,
      fingerprint,
      clarification: { ...clarification, status: 'submitted' },
      answers
    }
  ]
}

// 项目规划阶段独立回放项目计划生成，保证没有需求确认上下文时也不会重新进入需求分析阶段。
async function replayProjectPlan(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks,
  appName: string | undefined,
  projectPlan: Record<string, unknown>,
  extra?: { revision?: boolean }
): Promise<WorkflowRunPayload> {
  const { onWorkflow, onApplicationLifecycle } = callbacks
  const revision = Boolean(extra?.revision)
  const trajectory = createTrajectory('project_planning', callbacks.onProcessSteps)
  // 项目计划开始生成即进入项目规划阶段，不能等文件生成完成后才切换 Agent 与会话。
  // 过渡说明由节点轨迹的 detail 承载，不再向对话正文追加状态句。
  onApplicationLifecycle?.(designLifecycle(options.application, 'generating_project_plan'))
  trajectory.set('planning_context', 'completed')
  trajectory.set('planning_scope', 'running')
  await delay(350)
  trajectory.set('planning_scope', 'completed')
  trajectory.set('planning_permissions', 'running')
  await delay(450)
  trajectory.set('planning_permissions', 'completed')
  trajectory.set(
    'planning_document',
    'running',
    revision ? '根据本次调整意见重新生成项目计划。' : undefined
  )
  onWorkflow?.(
    wf(threadId, 'project_planning', 'running', {}, {
      summary: {
        phase: 'project_planning',
        status: 'running',
        message: '项目 Agent 正在生成项目计划…'
      }
    }, trajectory.events())
  )
  const planChanges = await emitProgressiveDocChanges(
    threadId,
    'project_planning',
    appPath(WORKSPACE_DOC_PATHS.projectPlan),
    '正在生成项目计划…',
    buildProjectPlanDoc(projectPlan, appName),
    onWorkflow,
    trajectory
  )
  // Diff 与接受授权归生成节点：文档渐进写入完成即进入待接受态，接受后才落完成。
  trajectory.set(
    'planning_document',
    'requires_user_input',
    revision ? '项目计划已按调整意见更新，请重新审核。' : undefined
  )
  const payload = projectPlanConfirmationPayload(
    threadId,
    appName,
    projectPlan,
    planChanges,
    trajectory.events()
  )
  onWorkflow?.(payload)
  return payload
}

// 按 resumeState 所处阶段选择回放分支。
export async function replayPlanning(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const clarification = (resume?.state?.clarification ?? resume?.result?.clarification ?? {}) as {
    mode?: string
    questions?: unknown[]
  }
  const mode = clarification?.mode
  // 已提交澄清卡历史：随续跑快照累积，前端按节点内嵌回看；恢复/修订续跑时不得丢失。
  const history = submittedCardHistory(
    resume,
    options.clarificationAnswers as Record<string, unknown> | undefined
  )
  const appName = options.application?.appName || options.application?.name
  // 按当前应用工作区取规划数据（requirement-spec / project-plan / 澄清题），三应用各自独立。
  const scenario = appDataByWorkspace(options.application?.workspaceRoot)
  const requirementSpec = scenario.requirementSpec
  const projectPlan = scenario.projectPlan
  const clarificationQuestions = scenario.clarificationQuestions

  // 恢复进行中计划：首页"查看计划"只读恢复同一线程的澄清状态，不推进任何节点。
  if (options.applicationPlanningRecovery) {
    const trajectory = createTrajectory('requirement_analysis', callbacks.onProcessSteps)
    trajectory.set('requirements_context', 'completed')
    trajectory.set('requirements_analyze', 'completed')
    trajectory.set('requirements_clarify', 'requires_user_input')
    // 恢复查看不再追加状态句：澄清节点与卡片本身已表达待确认状态。
    await delay(300)
    const payload = clarificationPayload(threadId, appName, clarificationQuestions, trajectory.events(), history)
    onWorkflow?.(payload)
    return payload
  }

  // 新迭代（由父版本派生）：只问「本次迭代补充什么需求」，提交后直接进开发，
  // 不重复新应用的完整需求确认/项目计划流程（基于上一版本增量）。
  // 判定用 parentVersionId（createIterationVersion 置）；lifecycle.revision 不准 ——
  // 演示态 makeCompleteLifecycle revision=5，但仍是单次新建，不能据此判迭代。
  const currentVersionForIteration = options.application?.versions?.find(
    (v) => v.id === options.application?.currentVersionId
  )
  const isIterationVersion = Boolean(currentVersionForIteration?.parentVersionId)
  if (isIterationVersion) {
    // 用户已在下方输入框回复迭代需求 → 直接进开发。普通消息不携带 resumeState，
    // 因此用消息文本判定：自动开启（“开始需求分析”/“开始项目计划”）只播引导语，
    // 其它任何非空文本都视为用户描述的迭代需求。
    const userText = typeof options.message === 'string' ? options.message.trim() : ''
    const isAutoStageStart =
      userText === '开始需求分析' || userText === '开始项目计划' || userText === ''
    if (resume || !isAutoStageStart) {
      onContent?.('已收到本次迭代需求。')
      await delay(300)
      const confirmation = wf(
        threadId,
        'ready_for_workbench',
        'completed',
        {
          application_planning_confirmation: {
            confirmedAt: new Date().toISOString(),
            directories: { specs: 'docs', plans: 'docs' },
            artifacts: {}
          }
        },
        {
          result: {
            application_planning_confirmation: {
              confirmedAt: new Date().toISOString(),
              directories: { specs: 'docs', plans: 'docs' },
              artifacts: {}
            }
          }
        }
      )
      onWorkflow?.(confirmation)
      return confirmation
    }
    // 起点：一句引导词即可，用户在下方输入框描述迭代需求（无确认卡片）。
    onContent?.(
      '新迭代已创建。请在下方输入框描述本次迭代要补充或调整的需求，Agent 会基于上一版已完成的内容增量开发。'
    )
    await delay(300)
    const intro = wf(threadId, 'requirements', 'requires_user_input', {})
    onWorkflow?.(intro)
    return intro
  }

  if (!resume && options.workflowScope === 'application_workbench_planning') {
    return replayProjectPlan(threadId, options, callbacks, appName, projectPlan)
  }

  // 无 resumeState：需求分析阶段冷启动，先播放分析与澄清段节点再挂澄清门。
  if (!resume) {
    const trajectory = createTrajectory('requirement_analysis', callbacks.onProcessSteps)
    trajectory.set('requirements_context', 'running')
    await delay(400)
    trajectory.set('requirements_context', 'completed')
    trajectory.set('requirements_analyze', 'running')
    await delay(600)
    trajectory.set('requirements_analyze', 'completed')
    // 分析进度快照：运行中状态先于澄清门发射，供会话运行态识别“推进中”。
    onWorkflow?.(
      wf(threadId, 'requirements', 'running', {}, {
        summary: {
          phase: 'requirements',
          status: 'running',
          message: '产品 Agent 正在分析需求…'
        }
      }, trajectory.events())
    )
    trajectory.set('requirements_clarify', 'requires_user_input')
    await delay(300)
    const payload = clarificationPayload(threadId, appName, clarificationQuestions, trajectory.events())
    onWorkflow?.(payload)
    return payload
  }

  if (mode === 'project_plan_confirmation') {
    if (
      options.clarificationAnswers?.confirm_project_plan !== undefined &&
      !answerIsYes(options.clarificationAnswers.confirm_project_plan)
    ) {
      const trajectory = createTrajectory('project_planning', callbacks.onProcessSteps)
      trajectory.set('planning_context', 'completed')
      trajectory.set('planning_scope', 'completed')
      trajectory.set(
        'planning_document',
        'requires_user_input',
        '项目计划暂不确认，请补充需要调整的页面、接口或实体范围。'
      )
      const revision = revisionPayload(threadId, 'project_planning', 'project_plan_revision', trajectory.events(), history)
      onWorkflow?.(revision)
      return revision
    }
    const trajectory = createTrajectory('project_planning', callbacks.onProcessSteps)
    trajectory.set('planning_context', 'completed')
    trajectory.set('planning_scope', 'completed')
    trajectory.set('planning_document', 'completed', '项目计划 Diff 已接受。')
    const entryGate = developmentEntryConfirmationPayload(threadId, trajectory.events())
    onWorkflow?.(entryGate)
    return entryGate
  }

  if (mode === 'development_entry_confirmation') {
    const trajectory = createTrajectory('project_planning', callbacks.onProcessSteps)
    trajectory.set('planning_context', 'completed')
    trajectory.set('planning_scope', 'completed')
    trajectory.set('planning_permissions', 'completed')
    trajectory.set('planning_document', 'completed')
    const confirmation = wf(
      threadId,
      'ready_for_workbench',
      'completed',
      {
        application_planning_confirmation: {
          confirmedAt: new Date().toISOString(),
          directories: { specs: 'docs', plans: 'docs' },
          artifacts: {}
        }
      },
      {
        result: {
          application_planning_confirmation: {
            confirmedAt: new Date().toISOString(),
            directories: { specs: 'docs', plans: 'docs' },
            artifacts: {}
          }
        }
      },
      trajectory.events()
    )
    onWorkflow?.(confirmation)
    return confirmation
  }

  if (mode === 'planning_stage_entry') {
    // 项目规划门禁确认：切到规划会话启动规划工作流，规划剧本负责生命周期与阶段切换。
    return replayProjectPlan(threadId, options, callbacks, appName, projectPlan)
  }

  if (mode === 'project_plan_revision') {
    return replayProjectPlan(threadId, options, callbacks, appName, projectPlan, { revision: true })
  }

  if (mode === 'requirement_spec_confirmation') {
    if (
      options.clarificationAnswers?.confirm_requirement_spec !== undefined &&
      !answerIsYes(options.clarificationAnswers.confirm_requirement_spec)
    ) {
      const trajectory = createTrajectory('requirement_analysis', callbacks.onProcessSteps)
      trajectory.set('requirements_clarify', 'completed')
      trajectory.set(
        'requirements_document',
        'requires_user_input',
        '需求文档暂不确认，请补充需要调整的业务目标、角色或流程。'
      )
      const revision = revisionPayload(threadId, 'requirements', 'requirement_revision', trajectory.events(), history)
      onWorkflow?.(revision)
      return revision
    }
    // 需求文档 Diff 已接受 → 「生成需求文档」节点收口，工作流到此完成。
    // 项目规划准入门（planning_stage_entry）是阶段层逻辑：工作流在此挂起等待确认，
    // 弹框与顶部「项目规划」承载进入动作，但不在需求分析轨迹里呈现阶段切换节点。
    // 生命周期停在 awaiting_requirement_confirmation：仍属分析阶段的初始集合，
    // 门禁保持可交互，且不会像 generating_project_plan 那样提前切换阶段。
    callbacks.onApplicationLifecycle?.(
      designLifecycle(options.application, 'awaiting_requirement_confirmation')
    )
    const trajectory = createTrajectory('requirement_analysis', callbacks.onProcessSteps)
    trajectory.set('requirements_document', 'completed', '需求文档 Diff 已接受。')
    const entryGate = planningEntryConfirmationPayload(threadId, trajectory.events())
    onWorkflow?.(entryGate)
    return entryGate
  }

  // 澄清已提交（hasQuestions）或其它 → 播放文档段：生成需求文档 → 确认需求文档。
  const trajectory = createTrajectory('requirement_analysis', callbacks.onProcessSteps)
  trajectory.set('requirements_clarify', 'completed')
  // 修改意见续跑先做需求变更意图分析（对齐真实工程 design_intent_analysis），再重新生成文档。
  if (mode === 'requirement_revision') {
    trajectory.set('requirements_intent', 'running')
    await delay(500)
    trajectory.set('requirements_intent', 'completed')
  }
  trajectory.set('requirements_document', 'running')
  await delay(350)
  onWorkflow?.(
    wf(threadId, 'requirements', 'running', {}, {
      summary: { phase: 'requirements', status: 'running', message: '正在生成需求文档…' }
    }, trajectory.events())
  )
  const requirementChanges = await emitProgressiveDocChanges(
    threadId,
    'requirements',
    appPath(WORKSPACE_DOC_PATHS.requirementSpec),
    '正在生成需求文档…',
    buildRequirementSpecDoc(requirementSpec, appName),
    onWorkflow,
    trajectory
  )
  // 文档渐进写入完成即进入待接受态：Diff 与接受授权归「生成需求文档」节点，
  // 接受后由准入门续跑把节点落为完成。
  trajectory.set(
    'requirements_document',
    'requires_user_input',
    '需求文档已生成，请在右侧确认 Diff 后接受。'
  )

  // 需求文档已生成（确认卡出现）→ 发 generating_requirement_spec，让「需求文档」文档 tab 变亮。
  callbacks.onApplicationLifecycle?.(designLifecycle(options.application, 'generating_requirement_spec'))
  const payload = requirementConfirmationPayload(
    threadId,
    appName,
    requirementSpec,
    requirementChanges,
    trajectory.events(),
    history
  )
  onWorkflow?.(payload)
  return payload
}

// —— 工作台需求分析/项目规划阶段：把「一次性需求确认 + 项目规划」节点逻辑挪进工作台对话 ——

function designLifecycle(
  app: ApplicationConfig | undefined,
  stage: ApplicationLifecycleStage
): ApplicationLifecycle {
  return {
    schemaVersion: '1.2.0',
    application: { id: app?.id || 'app-pms-new', name: app?.name || '应用' },
    updatedAt: new Date().toISOString(),
    revision: nextLifecycleRevision(),
    initialization: { stage, status: 'running' },
    activeExecutions: {},
    extensions: {}
  }
}

/**
 * 工作台需求分析/项目规划阶段剧本 = 规划流程(复用 replayPlanning)+ 生命周期驱动阶段。
 * 规划全程生命周期 stage 属需求分析/项目规划阶段；计划确认完成后发 ready_for_workbench，
 * 前端 executionPhase 据此自动切到开发阶段。
 */
export async function replayDesignPhase(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  if (options.workflowScope !== 'application_workbench_planning') {
    callbacks.onApplicationLifecycle?.(designLifecycle(options.application, 'collecting_requirement'))
  }
  const result = await replayPlanning(threadId, options, callbacks)
  if (result.summary.phase === 'ready_for_workbench' && result.summary.status === 'completed') {
    callbacks.onApplicationLifecycle?.(designLifecycle(options.application, 'ready_for_workbench'))
  }
  return result
}
