import type { ApplicationLifecycle, WorkflowRunPayload, WorkbenchExecution } from '../../typings'
import type { AgentChatMessage } from './types'
import type { SessionIdentity } from './hooks/sessionRuntime'

/** 从权威生命周期定位当前可见会话的执行，禁止借用其它会话的进度。 */
export function ownedWorkbenchExecution(
  lifecycle?: ApplicationLifecycle,
  session?: SessionIdentity
): WorkbenchExecution | undefined {
  if (!session || lifecycle?.application.id !== session.workflowId) return undefined
  const execution = Object.values(lifecycle?.activeExecutions || {})
    .filter((entry) => entry.ownerSessionId === session.sessionId)
    .sort((left, right) => right.startedAt.localeCompare(left.startedAt))[0]
  // Durable Recovery 已证明源 Run 终结时，旧 lifecycle running 不能重新锁住重试入口。
  const recovery = lifecycle.extensions.executionRecovery?.candidates.find(
    (candidate) =>
      candidate.executionKind === 'workbench' &&
      candidate.sourceRunId === execution?.runId &&
      candidate.ownerSessionId === session.sessionId &&
      candidate.threadId === execution?.threadId
  )
  if (execution && recovery?.executionStatus === 'failed') return { ...execution, status: 'failed' }
  if (execution && recovery?.executionStatus === 'interrupted')
    return { ...execution, status: 'stopped' }
  return execution
}

/** 判断后端是否仍在执行，实时连接丢失不等于业务运行已结束。 */
export function workbenchExecutionRunning(execution?: WorkbenchExecution): boolean {
  return Boolean(execution && ['starting', 'running', 'stopping'].includes(execution.status))
}

/** 将已有只读进度恢复为现有消息组件可消费的展示数据，不登记或启动新 Run。 */
export function restoreWorkbenchPresentation(
  messages: AgentChatMessage[],
  lifecycle?: ApplicationLifecycle,
  session?: SessionIdentity
): { workflow?: WorkflowRunPayload; messages: AgentChatMessage[] } {
  const execution = ownedWorkbenchExecution(lifecycle, session)
  if (!execution || (!workbenchExecutionRunning(execution) && execution.status !== 'failed'))
    return { messages }
  const projection = lifecycle?.extensions.workbenchProgress?.[execution.runId]
  const dag =
    projection?.threadId === execution.threadId &&
    projection.ownerSessionId === execution.ownerSessionId
      ? projection.dagGeneration
      : undefined
  const existingIndex = messages.findIndex(
    (item) => item.role === 'assistant' && item.workflow?.runId === execution.runId
  )
  const existing = messages[existingIndex]
  const title = execution.phase === 'prepare_build_tasks' ? '生成执行计划' : '执行当前任务'
  const status = execution.status === 'failed' ? 'failed' : 'running'
  const target = execution.developmentTarget
  const workflow: WorkflowRunPayload = {
    ...existing?.workflow,
    runId: execution.runId,
    threadId: execution.threadId,
    summary: { ...existing?.workflow?.summary, status, phase: execution.phase, lifecycle },
    state: {
      ...existing?.workflow?.state,
      selectedPageId:
        (target?.type === 'page' ? target.pageId : undefined) ||
        execution.pageId ||
        (execution.scope === 'page' ? execution.targetId : undefined),
      selectedApiContractId: target?.type === 'endpoint' ? target.apiContractId : undefined,
      selectedEndpointId: target?.type === 'endpoint' ? target.endpointId : undefined,
      selectedEntityId: target?.type === 'entity' ? target.entityId : undefined
    },
    events: existing?.workflow?.events || []
  }
  const steps = [...(existing?.processSteps || [])]
  // 已有实时消息优先保留更高 revision，读取投影不得使进度倒退。
  const stepIndex = steps.findIndex(
    (step) => step.nodeName === execution.phase && Boolean(step.dagGeneration)
  )
  const previous = steps[stepIndex]?.dagGeneration
  const snapshot =
    previous &&
    dag &&
    previous.planningRunId === dag.planningRunId &&
    previous.revision > dag.revision
      ? previous
      : dag
  if (snapshot || !steps.length) {
    const step = {
      id: `restored:${execution.runId}:${execution.phase}`,
      kind: 'workflow' as const,
      status: status as 'running' | 'failed',
      title,
      detail: status === 'failed' ? '执行计划生成失败' : '正在执行',
      sequence: 0,
      nodeName: execution.phase,
      dagGeneration: snapshot
    }
    if (stepIndex >= 0) steps[stepIndex] = { ...steps[stepIndex], ...step }
    else steps.push(step)
  }
  const restored: AgentChatMessage = {
    ...existing,
    id: existing?.id ?? -Date.parse(execution.startedAt),
    role: 'assistant',
    content: existing?.content || '',
    createdAt: existing?.createdAt ?? Date.parse(execution.startedAt),
    workflow,
    processSteps: steps
  }
  const result = [...messages]
  if (existingIndex >= 0) result[existingIndex] = restored
  else result.push(restored)
  return { workflow, messages: result }
}
