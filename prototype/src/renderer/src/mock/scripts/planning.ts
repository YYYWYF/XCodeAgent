// 规划会话剧本：模拟后端 AG-UI 事件流，驱动 ApplicationPagePlanningModal 走完
// 进度 → 补充细节 → 生成需求文档 → 确认需求文档 → 生成项目计划 → 确认项目计划 → 完成。
// 阶段判定对齐 planningWorkflowState.ts：节点名用 'requirements' / 'project_planning'（phaseOrder），
// 用户交互靠 summary.status='requires_user_input' 与 state.clarification。
// 澄清问题 / 需求文档 / 项目计划均为按真实后端 prompt 生成的数据（见 mock-data/）。

import type {
  ApplicationConfig,
  ApplicationLifecycle,
  ApplicationLifecycleStage,
  WorkflowRunPayload
} from '../../typings'
import type { SendWorkflowMessageOptions } from '../../service/agUiAgent'
import { buildProjectPlanDoc, buildRequirementSpecDoc } from '../../workbenchArtifacts'
import { appDataByWorkspace } from '../../../../../mock-data/index'
import { nextLifecycleRevision } from './revision'

type ReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
}

const delay = (ms: number): Promise<void> => new Promise((resolve) => setTimeout(resolve, ms))

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
  requirementSpec: Record<string, unknown>
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
      requirement_spec: requirementSpec
    },
    {
      result: { requirement_spec: requirementSpec },
      confirmationArtifact: {
        id: 'requirement_spec',
        name: '需求文档',
        path: 'specs/requirement.md',
        format: 'markdown',
        content: buildRequirementSpecDoc(requirementSpec, appName)
      }
    }
  )

const projectPlanConfirmationPayload = (
  threadId: string,
  appName: string | undefined,
  projectPlan: Record<string, unknown>
): WorkflowRunPayload =>
  wf(
    threadId,
    'project_planning',
    'requires_user_input',
    {
      clarification: {
        mode: 'project_plan_confirmation',
        status: 'requires_user_input',
        message: '请审核项目规划',
        questions: [
          {
            id: 'confirm_project_plan',
            header: '计划确认',
            type: 'yesno',
            question: '项目计划已生成，是否确认并生成构建任务清单？',
            allowOther: false
          }
        ]
      }
    },
    {
      confirmationArtifact: {
        id: 'project_plan',
        name: '项目计划',
        path: 'plans/project-plan.md',
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

// 按 resumeState 所处阶段选择回放分支。
export async function replayPlanning(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onContent, onWorkflow, onApplicationLifecycle } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const clarification = (resume?.state?.clarification ??
    resume?.result?.clarification ??
    {}) as { mode?: string; questions?: unknown[] }
  const mode = clarification?.mode
  const hasQuestions = Array.isArray(clarification?.questions) && clarification.questions.length > 0
  const appName = options.application?.appName || options.application?.name
  // 按当前应用工作区取规划数据（requirement-spec / project-plan / 澄清题），三应用各自独立。
  const scenario = appDataByWorkspace(options.application?.workspaceRoot)
  const requirementSpec = scenario.requirementSpec
  const projectPlan = scenario.projectPlan
  const buildTaskPlan = scenario.buildTaskPlan
  const clarificationQuestions = scenario.clarificationQuestions

  // 恢复进行中计划：首页"查看计划"只读恢复同一线程的澄清状态，不推进任何节点。
  if (options.applicationPlanningRecovery) {
    onContent?.('正在恢复待确认的应用规划…')
    await delay(300)
    onWorkflow?.(clarificationPayload(threadId, appName, clarificationQuestions))
    return clarificationPayload(threadId, appName, clarificationQuestions)
  }

  // 无 resumeState：冷启动，先进度再澄清。
  if (!resume) {
    onContent?.('正在解析应用场景…\n正在识别核心角色：项目经理 / 回检填报人 / 回检审核人…')
    await delay(350)
    onWorkflow?.(wf(threadId, 'requirements', 'running', {}, { summary: { phase: 'requirements', status: 'running', message: '正在分析需求并生成需求文档…' } }))
    onContent?.('正在生成需求文档大纲：目标 · 角色 · 页面 · 流程 · 验收标准…')
    await delay(900)
    onWorkflow?.(clarificationPayload(threadId, appName, clarificationQuestions))
    return clarificationPayload(threadId, appName, clarificationQuestions)
  }

  if (mode === 'build_task_plan_confirmation') {
    // 已确认构建任务计划 → 进入开发阶段。
    onContent?.('构建任务计划已确认，规划阶段完成。\n即将进入开发阶段，开始页面与接口的详细设计。')
    await delay(300)
    const confirmation = wf(
      threadId,
      'ready_for_workbench',
      'completed',
      {
        application_planning_confirmation: {
          confirmedAt: new Date().toISOString(),
          directories: { specs: 'specs', plans: 'plans' },
          artifacts: {}
        }
      },
      {
        result: {
          application_planning_confirmation: {
            confirmedAt: new Date().toISOString(),
            directories: { specs: 'specs', plans: 'plans' },
            artifacts: {}
          }
        }
      }
    )
    onWorkflow?.(confirmation)
    await delay(200)
    return confirmation
  }

  if (mode === 'project_plan_confirmation') {
    // 已确认项目计划 → 先根据项目计划生成构建任务计划(DAG)→ 出确认卡(与需求/项目计划一致)。
    // 对齐原工程 prepare_build_tasks 节点,但在原型里显式化:右栏「构建任务」tab 走生成中→内容,
    // 并给设计→开发一个用户确认断点(点了才进开发),而非原工程的透明自动步骤。
    onContent?.('项目计划已确认，将生成构建任务清单。\n正在根据项目计划拆解构建任务（DAG）：构建单元 · 任务 · 依赖…')
    onWorkflow?.(wf(threadId, 'prepare_build_tasks', 'running', {}, { summary: { phase: 'prepare_build_tasks', status: 'running', message: '正在生成构建任务计划…' } }))
    await delay(1400)
    // 构建任务已生成 → 发 generating_build_task_plan,右栏「构建任务」tab 内容就绪、变亮。
    onApplicationLifecycle?.(designLifecycle(options.application, 'generating_build_task_plan'))
    onContent?.('构建任务计划已生成,请确认后进入开发阶段。')
    await delay(300)
    const buildTaskPayload = wf(
      threadId,
      'prepare_build_tasks',
      'requires_user_input',
      {
        clarification: {
          mode: 'build_task_plan_confirmation',
          status: 'requires_user_input',
          message: '请确认构建任务计划',
          questions: [
            {
              id: 'confirm_build_task_plan',
              header: '构建任务确认',
              type: 'yesno',
              question: '构建任务计划已生成，是否确认并进入开发阶段？',
              allowOther: false
            }
          ]
        }
      },
      {
        result: { build_task_plan: buildTaskPlan }
      }
    )
    onWorkflow?.(buildTaskPayload)
    return buildTaskPayload
  }

  if (mode === 'requirement_spec_confirmation') {
    // 需求文档已确认 → 生成项目计划 → 确认项目计划。
    onContent?.('需求文档已确认，将生成项目计划。\n正在根据需求拆解页面与接口…\n正在匹配技术栈：React + AntD · Spring Boot · MySQL…')
    await delay(350)
    onWorkflow?.(wf(threadId, 'project_planning', 'running', {}, { summary: { phase: 'project_planning', status: 'running', message: '正在生成项目计划…' } }))
    onContent?.('正在输出 plans 目录规划产物…')
    await delay(1100)
    // 项目计划已生成（确认卡出现）→ 发 generating_project_plan，让「项目计划」文档 tab 变亮。
    onApplicationLifecycle?.(designLifecycle(options.application, 'generating_project_plan'))
    const planPayload = projectPlanConfirmationPayload(threadId, appName, projectPlan)
    onWorkflow?.(planPayload)
    return planPayload
  }

  // 澄清已提交（hasQuestions）或其它 → 生成需求文档 → 确认需求文档。
  onContent?.('正在根据补充信息完善需求…')
  await delay(350)
  onWorkflow?.(wf(threadId, 'requirements', 'running', {}, { summary: { phase: 'requirements', status: 'running', message: '正在生成需求文档…' } }))
  onContent?.('正在整理页面与角色清单…')
  await delay(1100)
  // 需求文档已生成（确认卡出现）→ 发 generating_requirement_spec，让「需求文档」文档 tab 变亮。
  onApplicationLifecycle?.(designLifecycle(options.application, 'generating_requirement_spec'))
  const payload = requirementConfirmationPayload(threadId, appName, requirementSpec)
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
 * 规划全程生命周期 stage 属设计阶段(product)；计划确认完成后发 ready_for_workbench，
 * 前端 deriveWorkbenchPhase 据此自动切到开发阶段。
 */
export async function replayDesignPhase(
  threadId: string,
  options: SendWorkflowMessageOptions,
  callbacks: ReplayCallbacks
): Promise<WorkflowRunPayload> {
  callbacks.onApplicationLifecycle?.(designLifecycle(options.application, 'collecting_requirement'))
  const result = await replayPlanning(threadId, options, callbacks)
  if (result.summary.phase === 'ready_for_workbench' && result.summary.status === 'completed') {
    callbacks.onApplicationLifecycle?.(designLifecycle(options.application, 'ready_for_workbench'))
  }
  return result
}
