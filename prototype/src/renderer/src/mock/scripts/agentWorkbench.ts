import type {
  ApplicationLifecycle,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import type { ProcessStepRecord, SendWorkflowMessageOptions } from '../../service/agUiAgent'
import { mockPlanningArtifacts } from '../../../../../mock-data/pms-new/planning-artifacts'
import { appDataByWorkspace } from '../../../../../mock-data'
import {
  buildAgentDesignDoc,
  buildAgentPageIntegrationSource,
  buildAgentSource,
  buildAgentToolAdapterSource,
  missingAgentEntityIds,
  type DevelopmentPlanningAgent
} from '../../agentDevelopment'
import { registerWorkbenchLifecycle } from '../mockHttpAgent'
import { appPath } from '../workspaceFiles'
import {
  isEntityDesigned,
  markAgentDesigned,
  markEntityDesigned
} from '../designState'
import { nextLifecycleRevision } from './revision'

const MOCK_APPLICATION_PREVIEW_URL = 'http://127.0.0.1:5190/'

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
      modelId: 'default-model',
      apiDependencies: [],
      apiReferences: [],
      entityIds: [],
      pageIds: [],
      tools: [],
      permissions: ['遵循应用默认权限'],
      acceptanceCriteria: ['试运行结果符合职责和权限边界'],
      knowledgeReferences: [],
      designed: false,
      hasDetailPlan: false
    }
  )
}

/** 将规划目录实体转换为当前版本的 Agent 依赖状态，避免已发布版本状态泄漏到新迭代。 */
function agentDependencyEntities(
  versionKey: string | undefined,
  isCompletedDemoVersion: boolean
): Array<{
  entityId: string
  label: string
  purpose: string
  designed: boolean
  hasDetailPlan: boolean
  detailPlanStatus: string
}> {
  return mockPlanningArtifacts.entities.map((entity) => {
    const designed =
      isCompletedDemoVersion || isEntityDesigned(entity.entityId, versionKey)
    return {
      entityId: entity.entityId,
      label: entity.label,
      purpose: entity.purpose,
      designed,
      hasDetailPlan: designed,
      detailPlanStatus: designed ? 'confirmed' : 'pending'
    }
  })
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
    api_dependencies: agent.apiReferences.map((reference) => ({
      apiContractId: reference.apiContractId,
      endpointId: reference.endpointId,
      method: reference.method,
      path: reference.path,
      entityIds: reference.entityIds,
      purpose: reference.purpose
    })),
    page_integrations: agent.pageIds.map((pageId) => ({
      pageId,
      entry: '页面内助手入口与抽屉式对话'
    })),
    system_instructions: [
      '仅根据当前用户可见数据和已确认知识回答。',
      '高风险写操作必须拒绝或转人工确认。'
    ],
    context_strategy: ['保留当前会话与工具证据摘要，限制历史上下文长度。'],
    failure_handling: ['工具失败显示可重试错误、来源和当前请求状态。'],
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
type AgentBuildTarget = {
  key: string
  name: string
  path: string
  content: string
  sourceTool: string
}

/** 生成智能体定义、工具适配器和页面接入的三份实际 Diff 目标。 */
function agentBuildTargets(agent: DevelopmentPlanningAgent): AgentBuildTarget[] {
  const sources = [
    { key: 'definition', source: buildAgentSource(agent), sourceTool: 'agent_java_generator' },
    { key: 'tools', source: buildAgentToolAdapterSource(agent), sourceTool: 'agent_tool_adapter_generator' },
    { key: 'page', source: buildAgentPageIntegrationSource(agent), sourceTool: 'agent_page_integrator' }
  ]
  return sources.map(({ key, source, sourceTool }) => ({
    key,
    name: source.filePath.split('/').pop() || source.filePath,
    path: appPath(source.filePath),
    content: source.content,
    sourceTool
  }))
}

/** 把智能体源码包装为前端 Diff 面板使用的单文件变更集。 */
function agentSingleFileChangeSet(
  runId: string,
  target: AgentBuildTarget
): WorkspaceCodeChangeSet {
  const lines = target.content.split('\n')
  const change = {
    id: `cc-${runId}-agent-${target.key}`,
    path: target.path,
    changeType: 'added' as const,
    additions: lines.length,
    deletions: 0,
    diff: lines.map((line) => `+${line}`).join('\n'),
    tool: 'file.write' as const,
    sourceTool: target.sourceTool,
    executed: true
  }
  return {
    id: `cc-${runId}-agent-${target.key}-${change.additions}`,
    status: 'applied',
    workspaceRoot: appDataByWorkspace().workspaceRoot,
    summary: { files: 1, additions: change.additions, deletions: 0 },
    files: [change]
  }
}

/** 生成智能体构建 DAG，并将已接受、当前待确认和后续待执行任务区分展示。 */
function agentBuildSlice(
  agent: DevelopmentPlanningAgent,
  targets: AgentBuildTarget[],
  acceptedCount: number
): Record<string, unknown> {
  const tasks = targets.map((target, index) => ({
      id: `task-${agent.id}-${target.key}`,
      task_id: `task-${agent.id}-${target.key}`,
      unit_id: target.key === 'page' ? `page:${agent.pageIds[0] || 'application'}` : `agent:${agent.id}`,
      owner: target.key === 'page' ? 'frontend' : 'backend',
      title:
        target.key === 'definition'
          ? `生成 ${agent.label} 定义与系统指令`
          : target.key === 'tools'
            ? '绑定受控工具、API 与权限策略'
            : '接入页面助手入口与运行状态',
      status: index < acceptedCount ? 'completed' : index === acceptedCount ? 'running' : 'pending',
      ...(index > 0 ? { dependencies: [`task-${agent.id}-${targets[index - 1]?.key}`] } : {}),
      target_files: [target.path]
    }))
  const completed = tasks.filter((task) => task.status === 'completed').length
  const running = tasks.filter((task) => task.status === 'running').length
  const pending = tasks.filter((task) => task.status === 'pending').length
  return {
    scope: { type: 'agent', id: agent.id, label: agent.label },
    target_unit_ids: [`agent:${agent.id}`, ...agent.pageIds.map((pageId) => `page:${pageId}`)],
    tasks,
    summary: { total: tasks.length, completed, pending, running, failed: 0 }
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
  const versionKey = options.application?.currentVersionId
  const isCompletedDemoVersion =
    appId === 'app-pms-new' && versionKey === 'app-pms-new-v1-3'

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

  /** 生成实体依赖门禁，让用户先进入实体设计再返回 Agent 详细设计。 */
  const emitDependencyGate = (
    view: 'dependency_gate' | 'entity_design',
    entityId?: string
  ): WorkflowRunPayload => {
    const dependencyEntities = agentDependencyEntities(versionKey, isCompletedDemoVersion)
    const missingEntityIds = missingAgentEntityIds(agent, dependencyEntities)
    const entity = dependencyEntities.find((candidate) => candidate.entityId === entityId)
    const isEntityDesign = view === 'entity_design' && entity
    const pending = {
      id: `pi-agent-entity-${Date.now()}`,
      type: isEntityDesign ? 'entity_design_confirmation' : 'agent_dependency_gate',
      basedOnRevision: 1,
      payload: {
        agentId: agent.id,
        missingEntityIds,
        ...(entity ? { entityId: entity.entityId } : {})
      },
      createdAt: new Date().toISOString()
    }
    const lifecycle = emitLifecycle('inspect_agent_dependencies', 'awaiting_user', pending)
    const clarification = isEntityDesign
      ? {
          mode: 'agent_dependency_gate',
          status: 'requires_user_input',
          message: `请先确认实体“${entity.label}”的详细设计，确认后才能继续 ${agent.label} 的设计。`,
          context: { view: 'entity_design', entityId: entity.entityId },
          entity_design: {
            entity_id: entity.entityId,
            name: entity.label,
            purpose: entity.purpose,
            document: [
              `# ${entity.label} 实体设计`,
              '',
              '## 用途',
              '',
              entity.purpose,
              '',
              '## 依赖约束',
              '',
              `- 该实体由 ${agent.label} 通过已确认 API 只读使用。`,
              '- 仅保留当前用户可见数据范围，未确认前不得进入 Agent 构建。'
            ].join('\n')
          },
          missing_entities: missingEntityIds
        }
      : {
          mode: 'agent_dependency_gate',
          status: 'requires_user_input',
          message: `${agent.label} 依赖的实体尚未完成详细设计，请先处理依赖后再继续。`,
          context: { view: 'dependency_gate' },
          missing_entities: missingEntityIds.map((missingId) => {
            const missing = dependencyEntities.find((candidate) => candidate.entityId === missingId)
            return {
              entity_id: missingId,
              name: missing?.label || missingId,
              purpose: missing?.purpose || '需要完成实体详细设计'
            }
          })
        }
    return emit(
      'inspect_agent_dependencies',
      'requires_user_input',
      lifecycle,
      {
        selectedEntityId: entity?.entityId,
        agentDependencyAction: view === 'entity_design' ? 'open_design' : 'recheck',
        clarification
      }
    )
  }

  /** 生成十段式智能体设计并停在明确确认门。 */
  const emitAgentDetailReview = async (): Promise<WorkflowRunPayload> => {
    onContent?.(`正在为 ${agent.label} 生成详细设计…`)
    await delay(650)
    const pending = {
      id: `pi-agent-design-${Date.now()}`,
      type: 'agent_design_confirmation',
      basedOnRevision: 1,
      payload: { message: '智能体设计已生成，等待人工确认' },
      createdAt: new Date().toISOString()
    }
    const lifecycle = emitLifecycle('detail_confirmation', 'awaiting_user', pending)
    const payload = emit(
      'detail_confirmation',
      'requires_user_input',
      lifecycle,
      {
        selectedEntityIds: agent.entityIds,
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
      }
    )
    onContent?.(`已生成 ${agent.label} 详细设计，请明确确认后进入构建。`)
    return payload
  }

  if (options.planControlAction === 'stop') {
    return emit('build', 'stopped', emitLifecycle('build', 'stopped'))
  }
  if (options.planControlAction === 'end') {
    return emit('finalize_project', 'completed', emitLifecycle('finalize_project', 'completed'))
  }
  if (answers.agent_acceptance === 'accepted' && resume?.summary?.phase === 'acceptance') {
    markAgentDesigned(agent.id, versionKey)
    const lifecycle = emitLifecycle('acceptance', 'completed')
    onContent?.(`${agent.label} 试运行和页面预览验收通过，智能体已完成交付。`)
    return emit(
      'acceptance',
      'completed',
      lifecycle,
      { agentPreviewReady: true },
      { summary: { phase: 'acceptance', status: 'completed', message: '智能体验收通过' } }
    )
  }

  const resumeClarification = String(
    (resume?.state?.clarification as { mode?: unknown } | undefined)?.mode ||
      (resume?.result?.clarification as { mode?: unknown } | undefined)?.mode ||
      (resume?.summary?.clarification as { mode?: unknown } | undefined)?.mode ||
      ''
  )
  const dependencyAction = String(answers.agent_dependency_action || '').trim()
  if (resumeClarification === 'agent_dependency_gate') {
    const selectedEntityId = String(
      answers.entity_id ||
        resume?.state?.selectedEntityId ||
        resume?.result?.selectedEntityId ||
        ''
    ).trim()
    if (dependencyAction === 'open_design' && selectedEntityId) {
      return emitDependencyGate('entity_design', selectedEntityId)
    }
    if (dependencyAction === 'confirm_entity_design' && selectedEntityId) {
      markEntityDesigned(selectedEntityId, versionKey)
      const refreshedEntities = agentDependencyEntities(versionKey, isCompletedDemoVersion)
      const stillMissing = missingAgentEntityIds(agent, refreshedEntities)
      if (stillMissing.length > 0) return emitDependencyGate('dependency_gate')
      return emitAgentDetailReview()
    }
    return emitDependencyGate('dependency_gate')
  }

  const missingEntityIds = missingAgentEntityIds(
    agent,
    agentDependencyEntities(versionKey, isCompletedDemoVersion)
  )
  if (missingEntityIds.length > 0) {
    return emitDependencyGate('dependency_gate')
  }

  if (answers.detail_review || resume) {
    const steps: ProcessStepRecord[] = []
    const targets = agentBuildTargets(agent)
    const buildResume = resume?.summary?.phase === 'build'
    const acceptedPath = typeof answers.file_acceptance === 'string' ? answers.file_acceptance : ''
    const acceptedIndex = buildResume
      ? Math.max(
          0,
          targets.findIndex((target) => target.path === acceptedPath) + 1
        )
      : 0
    /** 追加完成节点并立即流式更新过程卡。 */
    const pushStep = (step: ProcessStepRecord): void => {
      steps.push(step)
      onProcessSteps?.([...steps])
    }

    if (!buildResume) {
      const stages = [
        [
          'inspect_workspace',
          '检查工作区与已有产物',
          '已定位页面、接口、模型配置与现有 Agent 运行时。'
        ],
        [
          'inspect_agent_dependencies',
          '校验模型、工具与权限',
          '模型、GET /api/rechecks/my、实体引用和只读权限边界均已确认。'
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
    }

    const activeTarget = targets[acceptedIndex]
    if (activeTarget) {
      onContent?.(`正在生成 ${activeTarget.name}…`)
      const buildExecutionSlice = agentBuildSlice(
        agent,
        targets,
        acceptedIndex
      ) as ProcessStepRecord['buildExecutionSlice']
      pushStep({
        id: 'step-agent-build',
        kind: 'workflow',
        status: 'running',
        title: `生成 ${activeTarget.name}`,
        detail: '代码已生成，等待你确认当前文件 Diff。',
        sequence: steps.length + 1,
        buildExecutionSlice
      })
      const pending = {
        id: `pi-agent-file-${Date.now()}`,
        type: 'file_acceptance',
        basedOnRevision: 1,
        payload: { path: activeTarget.path, name: activeTarget.name },
        createdAt: new Date().toISOString()
      }
      const lifecycle = emitLifecycle('build', 'awaiting_user', pending)
      const clarification = {
        mode: 'file_acceptance',
        status: 'requires_user_input',
        message: `已生成 ${activeTarget.name}，请在右侧确认 Diff 后继续。`
      }
      return emit(
        'build',
        'requires_user_input',
        lifecycle,
        {
          clarification,
          buildExecutionSlice,
          buildTargetPath: activeTarget.path,
          acceptedBuildFiles: targets.slice(0, acceptedIndex).map((target) => target.path)
        },
        {
          codeChanges: agentSingleFileChangeSet(runId, activeTarget),
          summary: {
            phase: 'build',
            status: 'requires_user_input',
            message: `等待接受 ${activeTarget.name}`
          }
        }
      )
    }

    pushStep({
      id: 'step-agent-build',
      kind: 'workflow',
      status: 'completed',
      title: '生成智能体与页面接入代码',
      detail: '所有文件 Diff 均已接受并保存。',
      sequence: steps.length + 1,
      buildExecutionSlice: agentBuildSlice(
        agent,
        targets,
        targets.length
      ) as ProcessStepRecord['buildExecutionSlice']
    })

    onContent?.('正在执行受控试运行与集成验证…')
    emit('integration_test', 'running', emitLifecycle('integration_test', 'running'))
    await delay(650)
    emitLifecycle('integration_test', 'completed')
    pushStep({
      id: 'step-agent-test',
      kind: 'workflow',
      status: 'completed',
      title: '执行智能体试运行与集成验证',
      detail: '',
      sequence: steps.length + 1,
      checks: agentChecks() as ProcessStepRecord['checks']
    })
    onContent?.('正在启动智能体页面预览…')
    emit('launch_project', 'running', emitLifecycle('launch_project', 'running'))
    await delay(450)
    emitLifecycle('launch_project', 'completed')
    pushStep({
      id: 'step-agent-preview',
      kind: 'workflow',
      status: 'completed',
      title: '启动智能体页面预览',
      detail: '页面入口、助手抽屉和试运行上下文已准备完成。',
      sequence: steps.length + 1
    })
    const pending = {
      id: `pi-agent-acceptance-${Date.now()}`,
      type: 'agent_acceptance',
      basedOnRevision: 1,
      payload: { message: '请在右侧完成智能体试运行和页面预览验收。' },
      createdAt: new Date().toISOString()
    }
    const acceptanceLifecycle = emitLifecycle('acceptance', 'awaiting_user', pending)
    onContent?.(`${agent.label} 已完成构建和集成测试，请在右侧试运行并确认验收。`)
    return emit(
      'acceptance',
      'requires_user_input',
      acceptanceLifecycle,
      {
        agentPreviewReady: true,
        clarification: {
          mode: 'agent_acceptance',
          status: 'requires_user_input',
          message: '请在右侧完成智能体多轮试运行，并确认页面入口、工具证据、失败重试和权限拒绝状态。'
        }
      },
      {
        summary: {
          phase: 'acceptance',
          status: 'requires_user_input',
          previewUrl: MOCK_APPLICATION_PREVIEW_URL,
          acceptanceRequest: {
            status: 'awaiting_user',
            preview_url: MOCK_APPLICATION_PREVIEW_URL,
            message: '等待智能体验收'
          },
          message: '智能体预览已启动，等待验收'
        },
        result: { preview_url: MOCK_APPLICATION_PREVIEW_URL }
      }
    )
  }

  return emitAgentDetailReview()
}
