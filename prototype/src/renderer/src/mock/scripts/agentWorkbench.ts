import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-new/planning-artifacts'
import {
  buildAgentDesignDoc,
  buildAgentSource,
  type DevelopmentPlanningAgent
} from '../../agentDevelopment'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { markAgentDesigned } from '../designState'
import { nextLifecycleRevision } from './revision'

export type AgentReplayCallbacks = {
  onContent?: (content: string) => void
  onWorkflow?: (workflow: WorkflowRunPayload) => void
  onApplicationLifecycle?: (lifecycle: ApplicationLifecycle) => void
  onProcessSteps?: (steps: ProcessStepRecord[]) => void
}

type AgentExecution = {
  scope: 'agent'
  targetId: string
  resourceKeys: string[]
  threadId: string
  runId: string
  phase: string
  status: string
  startedAt: string
  updatedAt: string
  pendingInteraction?: Record<string, unknown>
}

/** 模拟流式节点间隔，让智能体流程复用现有 ProcessSteps 的运行态展示。 */
function delay(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms))
}

/** 从本次请求或恢复快照中解析智能体目标。 */
export function resolveAgentTarget(
  options: SendWorkflowMessageOptions,
  resume?: WorkflowRunPayload
): string | undefined {
  const state = resume?.state || {}
  const result = resume?.result || {}
  const targetType = String(
    options.detailTargetType || state.detailTargetType || result.detailTargetType || ''
  )
  if (targetType !== 'agent') return undefined
  const agentId = String(
    options.selectedAgentId || state.selectedAgentId || result.selectedAgentId || ''
  ).trim()
  return agentId || undefined
}

/** 从规划目录读取智能体；未知标识使用受限兜底定义，避免 mock 流程崩溃。 */
function agentMeta(agentId: string): DevelopmentPlanningAgent {
  return (
    mockPlanningArtifacts.agents.find((agent) => agent.id === agentId) || {
      id: agentId,
      label: agentId,
      purpose: '完成页面内的受控业务辅助任务',
      model: '项目默认模型',
      apiDependencies: [],
      pageIds: [],
      tools: [],
      permissions: ['遵循应用默认权限'],
      acceptanceCriteria: ['试运行结果符合职责和权限边界'],
      designed: false,
      hasDetailPlan: false
    }
  )
}

/** 创建智能体作用域的应用生命周期执行快照。 */
function agentExecution(
  threadId: string,
  runId: string,
  agentId: string,
  phase: string,
  status: string,
  pendingInteraction?: Record<string, unknown>
): AgentExecution {
  const now = new Date().toISOString()
  return {
    scope: 'agent',
    targetId: agentId,
    resourceKeys: [`agent:${agentId}`],
    threadId,
    runId,
    phase,
    status,
    startedAt: now,
    updatedAt: now,
    ...(pendingInteraction ? { pendingInteraction } : {})
  }
}

/** 组装带智能体稳定身份的 Workflow 投影。 */
function agentWorkflow(
  threadId: string,
  runId: string,
  agentId: string,
  phase: string,
  status: string,
  lifecycle?: ApplicationLifecycle,
  state: Record<string, unknown> = {},
  extra: Partial<WorkflowRunPayload> = {}
): WorkflowRunPayload {
  const identity = { selectedAgentId: agentId, detailTargetType: 'agent' }
  return {
    runId,
    threadId,
    summary: { phase, status, message: '', ...(lifecycle ? { lifecycle } : {}) },
    events: [{ type: 'workflow.node.started', nodeName: phase }],
    state: { ...state, ...identity, ...(lifecycle ? { lifecycle } : {}) },
    result: { ...identity, ...(lifecycle ? { lifecycle } : {}) },
    ...extra
  }
}

/** 生成用户必须明确确认的智能体结构化详细设计。 */
function agentReview(agent: DevelopmentPlanningAgent): Record<string, unknown> {
  return {
    target_type: 'agent',
    target_id: agent.id,
    name: agent.label,
    task: `${agent.purpose}；服务页面：${agent.pageIds.join('、') || '暂未绑定页面'}。`,
    rules: [
      '只回答需求回检相关问题，并明确说明信息来源。',
      '先理解当前问题，再按需调用工具，核验证据后生成回复。',
      '设计必须人工确认后才能构建，修订后需要重新确认。'
    ],
    limitations: [...agent.permissions, '不展示隐藏思维链，不编造工具未返回的业务数据。'],
    inputs: ['用户发送的自然语言消息', '当前页面上下文、会话历史和必要的用户身份范围'],
    outputs: ['面向用户的直接回复', '工具调用摘要、数据来源与可核验证据'],
    model: agent.model,
    conversation_experience: [
      '支持连续多轮消息，发送后立即显示用户消息和智能体生成状态。',
      '回复完成后保留本次试运行上下文，可继续追问或清空会话。'
    ],
    memory: [
      '会话内保留必要的最近对话和工具结果摘要。',
      '不跨用户共享业务数据，不依赖无界历史；长内容保存为稳定引用。'
    ],
    tools: [...agent.tools, ...agent.apiDependencies],
    knowledge_retrieval: [
      '优先检索当前应用已确认的需求、项目计划和业务知识。',
      '检索不到可靠内容时明确说明，不使用未经确认的信息补全答案。'
    ],
    design_markdown: buildAgentDesignDoc(agent)
  }
}

/** 生成智能体实现的 DAG 切片，沿用现有构建任务卡结构。 */
function agentBuildSlice(agent: DevelopmentPlanningAgent): Record<string, unknown> {
  const agentSourcePath = buildAgentSource(agent).filePath
  const toolSourcePath = agentSourcePath
    .replace('/generated/agent/', '/generated/tool/')
    .replace(/Agent\.java$/, 'ToolAdapter.java')
  const tasks = [
    {
      id: `task-${agent.id}-definition`,
      task_id: `task-${agent.id}-definition`,
      unit_id: `agent:${agent.id}`,
      owner: 'backend',
      title: `生成 ${agent.label} 定义与系统指令`,
      status: 'completed',
      target_files: [agentSourcePath]
    },
    {
      id: `task-${agent.id}-tools`,
      task_id: `task-${agent.id}-tools`,
      unit_id: `agent:${agent.id}`,
      owner: 'backend',
      title: '绑定受控工具、API 与权限策略',
      status: 'completed',
      dependencies: [`task-${agent.id}-definition`],
      target_files: [toolSourcePath]
    },
    {
      id: `task-${agent.id}-page`,
      task_id: `task-${agent.id}-page`,
      unit_id: `page:${agent.pageIds[0] || 'application'}`,
      owner: 'frontend',
      title: '接入页面助手入口与运行状态',
      status: 'completed',
      dependencies: [`task-${agent.id}-tools`],
      target_files: [`frontend/src/pages/${agent.pageIds[0] || 'application'}/index.tsx`]
    }
  ]
  return {
    scope: { type: 'agent', id: agent.id, label: agent.label },
    target_unit_ids: [`agent:${agent.id}`, ...agent.pageIds.map((pageId) => `page:${pageId}`)],
    tasks,
    summary: { total: tasks.length, completed: tasks.length, pending: 0, running: 0, failed: 0 }
  }
}

/** 返回智能体试运行必须通过的证据矩阵。 */
function agentChecks(): unknown[] {
  return [
    {
      id: 'agent-contract',
      name: '输入输出与工具契约',
      status: 'passed',
      required: true,
      evidence: '结构化输入、工具参数与页面消费格式一致。'
    },
    {
      id: 'agent-permission',
      name: '权限与数据隔离',
      status: 'passed',
      required: true,
      evidence: '只读取当前用户可见回检单，写操作被策略拒绝。'
    },
    {
      id: 'agent-context',
      name: '上下文预算与失败回退',
      status: 'passed',
      required: true,
      evidence: '上下文保持有界；工具失败会返回可重试错误和证据。'
    }
  ]
}

/** 重放智能体详细设计、确认、构建和试运行的 AG-UI prototype 剧本。 */
export async function replayAgentWorkbench(
  threadId: string,
  agentId: string,
  options: SendWorkflowMessageOptions,
  callbacks: AgentReplayCallbacks
): Promise<WorkflowRunPayload> {
  const { onApplicationLifecycle, onContent, onProcessSteps, onWorkflow } = callbacks
  const resume = options.resumeState as WorkflowRunPayload | undefined
  const runId = resume?.runId || `mock-agent-${Date.now()}`
  const agent = agentMeta(agentId)
  const answers = (options.clarificationAnswers || {}) as Record<string, unknown>
  const appId = options.application?.id || 'app-pms-new'
  const appName = options.application?.name || '武汉分行需求回检系统'

  /** 发布权威生命周期并保留同一 runId。 */
  const emitLifecycle = (
    phase: string,
    status: string,
    pendingInteraction?: Record<string, unknown>
  ): ApplicationLifecycle => {
    const lifecycle = {
      schemaVersion: '1.2.0',
      application: { id: appId, name: appName },
      updatedAt: new Date().toISOString(),
      revision: nextLifecycleRevision(),
      initialization: { stage: 'ready_for_workbench', status: 'completed' },
      activeExecutions: {
        [runId]: agentExecution(threadId, runId, agent.id, phase, status, pendingInteraction)
      },
      extensions: {}
    } as ApplicationLifecycle
    onApplicationLifecycle?.(lifecycle)
    registerWorkbenchLifecycle(lifecycle)
    return lifecycle
  }

  /** 发布 Workflow 投影到当前 AG-UI 消息。 */
  const emit = (
    phase: string,
    status: string,
    lifecycle?: ApplicationLifecycle,
    state: Record<string, unknown> = {},
    extra: Partial<WorkflowRunPayload> = {}
  ): WorkflowRunPayload => {
    const payload = agentWorkflow(threadId, runId, agent.id, phase, status, lifecycle, state, extra)
    onWorkflow?.(payload)
    return payload
  }

  if (options.planControlAction === 'stop') {
    return emit('build', 'stopped', emitLifecycle('build', 'stopped'))
  }
  if (options.planControlAction === 'end') {
    return emit('finalize_project', 'completed', emitLifecycle('finalize_project', 'completed'))
  }

  if (answers.detail_review || resume) {
    const steps: ProcessStepRecord[] = []
    /** 追加完成节点并立即流式更新过程卡。 */
    const pushStep = (step: ProcessStepRecord): void => {
      steps.push(step)
      onProcessSteps?.([...steps])
    }

    const stages = [
      [
        'inspect_workspace',
        '检查工作区与已有产物',
        '已定位页面、接口、模型配置与现有 Agent 运行时。'
      ],
      [
        'inspect_agent_dependencies',
        '校验模型、工具与权限',
        '模型、GET /api/rechecks/my 和只读权限边界均可用。'
      ],
      ['prepare_build_tasks', '规划构建任务（DAG）', '已拆分智能体定义、工具绑定和页面接入任务。']
    ] as const
    for (const [phase, title, detail] of stages) {
      onContent?.(`正在${title}…`)
      emit(phase, 'running', emitLifecycle(phase, 'running'))
      await delay(450)
      emit(phase, 'completed', emitLifecycle(phase, 'completed'))
      pushStep({
        id: `step-${phase}`,
        kind: 'workflow',
        status: 'completed',
        title,
        detail,
        sequence: steps.length + 1
      })
    }

    onContent?.(`正在生成 ${agent.label} 与页面接入代码…`)
    emit('build', 'running', emitLifecycle('build', 'running'))
    await delay(700)
    emit('build', 'completed', emitLifecycle('build', 'completed'))
    pushStep({
      id: 'step-agent-build',
      kind: 'workflow',
      status: 'completed',
      title: '生成智能体与页面接入代码',
      detail: '',
      sequence: steps.length + 1,
      buildExecutionSlice: agentBuildSlice(agent) as ProcessStepRecord['buildExecutionSlice']
    })

    onContent?.('正在执行受控试运行与集成验证…')
    emit('integration_test', 'running', emitLifecycle('integration_test', 'running'))
    await delay(650)
    const lifecycle = emitLifecycle('integration_test', 'completed')
    pushStep({
      id: 'step-agent-test',
      kind: 'workflow',
      status: 'completed',
      title: '执行智能体试运行与集成验证',
      detail: '',
      sequence: steps.length + 1,
      checks: agentChecks() as ProcessStepRecord['checks']
    })
    markAgentDesigned(agent.id)
    onContent?.(`${agent.label} 开发完成。右侧可查看设计文档、实现源码和试运行结果。`)
    return emit(
      'integration_test',
      'completed',
      lifecycle,
      { agentPreviewReady: true },
      { summary: { phase: 'integration_test', status: 'completed', message: '智能体开发完成' } }
    )
  }

  onContent?.(`正在为 ${agent.label} 生成详细设计…`)
  await delay(650)
  const pending = {
    id: `pi-agent-design-${Date.now()}`,
    type: 'page_design_confirmation',
    basedOnRevision: 1,
    payload: { message: '智能体设计已生成，等待人工确认' },
    createdAt: new Date().toISOString()
  }
  const lifecycle = emitLifecycle('detail_confirmation', 'awaiting_user', pending)
  const payload = emit('detail_confirmation', 'requires_user_input', lifecycle, {
    clarification: {
      mode: 'detail_review',
      status: 'requires_user_input',
      message: `请审阅 ${agent.label} 的任务、规则、限制、输入、输出、模型、对话体验、记忆、工具和知识检索设计。`,
      review: {
        pages: [],
        endpoints: [],
        agents: [agentReview(agent)],
        summary: {
          page_count: 0,
          endpoint_count: 0,
          agent_count: 1,
          api_contract_count: mockPlanningArtifacts.apiContracts.length,
          selectedAgentId: agent.id,
          detailTargetType: 'agent'
        }
      }
    }
  })
  onContent?.(`已生成 ${agent.label} 详细设计，请明确确认后进入构建。`)
  return payload
}
