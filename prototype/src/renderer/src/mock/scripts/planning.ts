// 规划会话剧本：模拟后端 AG-UI 事件流，驱动 ApplicationPagePlanningModal 走完
// 进度 → 补充细节 → 生成需求文档 → 确认需求文档 → 生成项目计划 → 确认项目计划 → 完成。
// 阶段判定对齐 planningWorkflowState.ts：节点名用 'requirements' / 'project_planning'（phaseOrder），
// 用户交互靠 summary.status='requires_user_input' 与 state.clarification。
// 澄清问题 / 需求文档 / 项目计划均为按真实后端 prompt 生成的数据（见 mock-data/）。

import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationLifecycleStage,
  WorkflowRunPayload,
  WorkspaceCodeChangeFile,
  WorkspaceCodeChangeSet
} from '../../typings'
import type { SendWorkflowMessageOptions } from '../../service/agUiAgent'
import { buildProjectPlanDoc, buildRequirementSpecDoc } from '../../workbenchArtifacts'
import { WORKSPACE_DOC_PATHS, appPath } from '../workspaceFiles'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { nextLifecycleRevision } from './revision'

type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

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
  onWorkflow?: ReplayCallbacks['onWorkflow']
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
        { summary: { phase, status: 'running', message } }
      )
    )
    if (visible >= lines.length) break
  }
  const full = docAddedChange(`cc-${phase}-full`, path, fullDoc)
  return docChangeSet(`cc-${phase}-completed`, full)
}

// 构造 WorkflowRunPayload：phase 同时作为 summary.phase 与 nodeName。
function wf(
  threadId: string,
  phase: string,
  status: string,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  return {
    runId: `mock-run-${phase}`,
    threadId,
    summary: { phase, status, message: '' },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
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
  codeChanges?: WorkspaceCodeChangeSet
): WorkflowRunPayload =>
  wf(
    threadId,
    'requirements',
    'requires_user_input',
    {
      // 工作台复用 WorkflowRunCard 渲染应用级确认：给一个 yesno 确认项，复用现有澄清卡渲染
      // （status=requires_user_input + questions），summary.status 同步为 requires_user_input。
      // 推进仍由 mode=requirement_spec_confirmation 驱动 replayPlanning 生成项目计划。
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
    }
  )

const projectPlanConfirmationPayload = (
  threadId: string,
  appName: string | undefined,
  projectPlan: Record<string, unknown>,
  codeChanges?: WorkspaceCodeChangeSet
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
    }
  )

// 澄清问题文案里的应用名与当前应用对齐（澄清题文案里的应用名统一替换为当前应用名）。
const clarificationPayload = (
  threadId: string,
  appName: string | undefined,
  clarificationQuestions: Array<Record<string, unknown>>
): WorkflowRunPayload =>
  wf(threadId, 'requirements', 'requires_user_input', {
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
  })

// 需求或计划未确认时，回到当前阶段继续接受用户的自然语言修改意见。
const revisionPayload = (
  threadId: string,
  phase: 'requirements' | 'project_planning',
  mode: 'requirement_revision' | 'project_plan_revision'
): WorkflowRunPayload =>
  wf(threadId, phase, 'requires_user_input', {
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
  })

// 读取确认卡中的 yes/no 答案，避免“否”被误推进到下一个阶段。
function answerIsYes(value: unknown): boolean {
  if (value && typeof value === 'object' && !Array.isArray(value) && 'selected' in value) {
    const selected = (value as { selected?: unknown }).selected
    return (Array.isArray(selected) ? selected : [selected]).some((item) => String(item) === '是')
  }
  if (Array.isArray(value)) return value.some((item) => String(item) === '是')
  return value === '是' || value === true
}

// 计划阶段独立回放项目计划生成，保证没有需求确认上下文时也不会重新进入分析阶段。
async function replayProjectPlan(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks,
  appName: string | undefined,
  projectPlan: Record<string, unknown>
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle } = callbacks
  // 项目计划开始生成即进入计划阶段，不能等文件生成完成后才切换 Agent 与会话。
  onApplicationLifecycle?.(designLifecycle(options.application, 'generating_project_plan'))
  onContent?.('收到需求文档确认，开始编写项目计划，请稍后进行审阅。')
  await delay(350)
  onWorkflow?.(
    wf(threadId, 'project_planning', 'running', {}, {
      summary: {
        phase: 'project_planning',
        status: 'running',
        message: '项目 Agent 正在生成项目计划…'
      }
    })
  )
  const planChanges = await emitProgressiveDocChanges(
    threadId,
    'project_planning',
    appPath(WORKSPACE_DOC_PATHS.projectPlan),
    '正在生成项目计划…',
    buildProjectPlanDoc(projectPlan, appName),
    onWorkflow
  )
  const payload = projectPlanConfirmationPayload(threadId, appName, projectPlan, planChanges)
  onWorkflow?.(payload)
  return payload
}

// 按 resumeState 所处阶段选择回放分支。
export async function replayPlanning(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const clarification = (resume?.state?.clarification ?? resume?.result?.clarification ?? {}) as {
    mode?: string
    questions?: unknown[]
  }
  const mode = clarification?.mode
  const appName = options.application?.appName || options.application?.name
  // 按当前应用工作区取规划数据（requirement-spec / project-plan / 澄清题），三应用各自独立。
  const scenario = appDataByWorkspace(options.application?.workspaceRoot)
  const requirementSpec = scenario.requirementSpec
  const projectPlan = scenario.projectPlan
  const clarificationQuestions = scenario.clarificationQuestions

  // 恢复进行中计划：首页"查看计划"只读恢复同一线程的澄清状态，不推进任何节点。
  if (options.applicationPlanningRecovery) {
    onContent?.('已恢复应用规划，请继续完成当前确认。')
    await delay(300)
    onWorkflow?.(clarificationPayload(threadId, appName, clarificationQuestions))
    return clarificationPayload(threadId, appName, clarificationQuestions)
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
    // 用户已在下方输入框回复迭代需求（resume 存在）→ 直接进开发。
    if (resume) {
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

  // 无 resumeState：分析阶段冷启动，先进度再澄清。
  if (!resume) {
    await delay(350)
    onWorkflow?.(
      wf(
        threadId,
        'requirements',
        'running',
        {},
        {
          summary: {
            phase: 'requirements',
            status: 'running',
            message: '产品 Agent 正在分析需求并生成需求文档…'
          }
        }
      )
    )
    await delay(900)
    onWorkflow?.(clarificationPayload(threadId, appName, clarificationQuestions))
    return clarificationPayload(threadId, appName, clarificationQuestions)
  }

  if (mode === 'project_plan_confirmation') {
    if (
      options.clarificationAnswers?.confirm_project_plan !== undefined &&
      !answerIsYes(options.clarificationAnswers.confirm_project_plan)
    ) {
      onContent?.('项目计划暂不确认，请补充需要调整的页面、接口或实体范围。')
      const revision = revisionPayload(threadId, 'project_planning', 'project_plan_revision')
      onWorkflow?.(revision)
      return revision
    }
    onContent?.('项目计划已确认，设计阶段完成，即将进入开发阶段。')
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

  if (mode === 'project_plan_revision') {
    return replayProjectPlan(threadId, options, callbacks, appName, projectPlan)
  }

  if (mode === 'requirement_spec_confirmation') {
    if (
      options.clarificationAnswers?.confirm_requirement_spec !== undefined &&
      !answerIsYes(options.clarificationAnswers.confirm_requirement_spec)
    ) {
      onContent?.('需求文档暂不确认，请补充需要调整的业务目标、角色或流程。')
      const revision = revisionPayload(threadId, 'requirements', 'requirement_revision')
      onWorkflow?.(revision)
      return revision
    }
    // 需求文档已确认 → 生成项目计划 → 确认项目计划。
    // 计划工作流一启动即切换阶段，确保项目 Agent 对话接管首帧与后续 Diff。
    onApplicationLifecycle?.(designLifecycle(options.application, 'generating_project_plan'))
    onContent?.('收到需求文档确认，开始编写项目计划，请稍后进行审阅。')
    await delay(350)
    onWorkflow?.(
      wf(
        threadId,
        'project_planning',
        'running',
        {},
        {
          summary: {
            phase: 'project_planning',
            status: 'running',
            message: '项目 Agent 正在生成项目计划…'
          }
        }
      )
    )
    const planChanges = await emitProgressiveDocChanges(
      threadId,
      'project_planning',
      appPath(WORKSPACE_DOC_PATHS.projectPlan),
      '正在生成项目计划…',
      buildProjectPlanDoc(projectPlan, appName),
      onWorkflow
    )
    const planPayload = projectPlanConfirmationPayload(threadId, appName, projectPlan, planChanges)
    onWorkflow?.(planPayload)
    return planPayload
  }

  // 澄清已提交（hasQuestions）或其它 → 生成需求文档 → 确认需求文档。
  onContent?.('收到补充信息，开始编写需求文档，请稍后进行审阅。')
  await delay(350)
  onWorkflow?.(
    wf(
      threadId,
      'requirements',
      'running',
      {},
      { summary: { phase: 'requirements', status: 'running', message: '正在生成需求文档…' } }
    )
  )
  const requirementChanges = await emitProgressiveDocChanges(
    threadId,
    'requirements',
    appPath(WORKSPACE_DOC_PATHS.requirementSpec),
    '正在生成需求文档…',
    buildRequirementSpecDoc(requirementSpec, appName),
    onWorkflow
  )

  // 需求文档已生成（确认卡出现）→ 发 generating_requirement_spec，让「需求文档」文档 tab 变亮。
  onApplicationLifecycle?.(designLifecycle(options.application, 'generating_requirement_spec'))
  const payload = requirementConfirmationPayload(
    threadId,
    appName,
    requirementSpec,
    requirementChanges
  )
  onWorkflow?.(payload)
  return payload
}

// —— 工作台设计阶段：把「一次性需求确认 + 项目规划」节点逻辑挪进工作台对话 ——

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
 * 工作台设计阶段剧本 = 规划流程(复用 replayPlanning)+ 生命周期驱动阶段。
 * 规划全程生命周期 stage 属分析/计划阶段；计划确认完成后发 ready_for_workbench，
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
