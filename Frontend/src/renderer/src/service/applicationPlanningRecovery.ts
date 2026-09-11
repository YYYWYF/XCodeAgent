import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber, HttpAgent } from '@ag-ui/client'
import type { Message } from '@ag-ui/core'
import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { readWorkflowPayload } from './agUiAgent'
import { workflowApplicationLifecycle } from './activeApplicationPlanning'
import { getApplicationLifecycle } from './applicationLifecycle'
import { getApplicationPlanningUrl } from './applicationPagePlanning'
import { createAgUiHttpAgent, isAuthenticationFailure } from './authentication'

const APPLICATION_PLANNING_RECONCILE_TIMEOUT_MS = 20_000
const APPLICATION_PLANNING_RECONCILE_ATTEMPTS = 2
const APPLICATION_PLANNING_RECONCILE_RETRY_DELAY_MS = 500

type ApplicationPlanningRecoveryEnvelope = {
  schemaVersion: 1
  status: 'completed' | 'failed'
  code?: string
  error?: {
    type?: string
    message?: string
  }
  [key: string]: unknown
}

export type ApplicationPlanningAuthoritativeSnapshot = {
  workflow: WorkflowRunPayload
  lifecycle: ApplicationLifecycle
}

/** 标识目标 planning thread 尚未产生可恢复 checkpoint。 */
export class ApplicationPlanningCheckpointNotFoundError extends Error {
  readonly code = 'application_planning_checkpoint_not_found'

  /** 保留后端返回的可读错误信息，同时提供稳定机器码。 */
  constructor(message: string) {
    super(message)
    this.name = 'ApplicationPlanningCheckpointNotFoundError'
  }
}

/** 校验 application planning recovery 的标准 action 信封。 */
function readRecoveryEnvelope(value: unknown): ApplicationPlanningRecoveryEnvelope | undefined {
  if (!value || typeof value !== 'object') return undefined
  const envelope = value as Partial<ApplicationPlanningRecoveryEnvelope>
  if (
    envelope.schemaVersion !== 1 ||
    !['completed', 'failed'].includes(String(envelope.status))
  ) {
    return undefined
  }
  return envelope as ApplicationPlanningRecoveryEnvelope
}

/** 从标准 StateSnapshot 或 RunFinished result 中读取 recovery 信封。 */
function readRecoveryState(value: unknown): ApplicationPlanningRecoveryEnvelope | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readRecoveryEnvelope((value as { workflow?: unknown }).workflow)
}

/** 在限定时间内执行一次只读 AG-UI recovery，并在超时后中止本次 HttpAgent。 */
async function readRecoveryEnvelopeOnce(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationPlanningRecoveryEnvelope> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  const agent: HttpAgent = createAgUiHttpAgent({
    url: getApplicationPlanningUrl(),
    threadId
  })
  const userMessage: Message = {
    id: randomUUID(),
    role: 'user',
    content: '读取当前应用规划权威状态。'
  }
  agent.addMessage(userMessage)

  let envelope: ApplicationPlanningRecoveryEnvelope | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'workflow-run') return
      envelope = readRecoveryEnvelope(event.value) ?? envelope
    },
    onStateSnapshotEvent: ({ event }) => {
      envelope = readRecoveryState(event.snapshot) ?? envelope
    }
  }
  let timeoutId: ReturnType<typeof globalThis.setTimeout> | undefined
  try {
    const timeout = new Promise<never>((_resolve, reject) => {
      timeoutId = globalThis.setTimeout(() => {
        agent.abortRun()
        reject(new Error('读取应用规划权威状态超时。'))
      }, APPLICATION_PLANNING_RECONCILE_TIMEOUT_MS)
    })
    const result = await Promise.race([
      agent.runAgent(
        {
          runId: randomUUID(),
          forwardedProps: {
            applicationPlanningRecovery: {
              action: 'get',
              workspaceRoot: application.workspaceRoot,
              applicationId: application.id
            }
          }
        },
        subscriber
      ),
      timeout
    ])
    envelope = readRecoveryState(result.result) ?? envelope
  } finally {
    if (timeoutId !== undefined) globalThis.clearTimeout(timeoutId)
  }
  if (!envelope) throw new Error('读取应用规划权威状态没有返回有效的 AG-UI 信封。')
  return envelope
}

/** 读取 checkpoint 与 lifecycle 的同一次后端权威投影，并校验应用和线程身份。 */
async function readApplicationPlanningAuthoritativeSnapshotOnce(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationPlanningAuthoritativeSnapshot> {
  const envelope = await readRecoveryEnvelopeOnce(application, threadId)
  if (envelope.status === 'failed') {
    const message = envelope.error?.message || '读取应用规划权威状态失败。'
    if (envelope.code === 'application_planning_checkpoint_not_found') {
      throw new ApplicationPlanningCheckpointNotFoundError(message)
    }
    throw new Error(message)
  }
  const workflow = readWorkflowPayload(envelope)
  if (!workflow) throw new Error('读取应用规划权威状态没有返回有效的 Workflow 快照。')
  if (workflow.threadId !== threadId) {
    throw new Error('应用规划权威状态与请求 thread 不匹配。')
  }
  const lifecycle =
    workflowApplicationLifecycle(workflow) ??
    (await getApplicationLifecycle(application, threadId))
  if (lifecycle.application.id !== application.id) {
    throw new Error('应用规划权威状态与请求 application 不匹配。')
  }
  return { workflow, lifecycle }
}

/** 以最多两次、单次二十秒的有界读取获取 application planning 权威状态。 */
export async function readApplicationPlanningAuthoritativeSnapshot(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationPlanningAuthoritativeSnapshot> {
  let lastError: unknown
  for (let attempt = 0; attempt < APPLICATION_PLANNING_RECONCILE_ATTEMPTS; attempt += 1) {
    try {
      return await readApplicationPlanningAuthoritativeSnapshotOnce(application, threadId)
    } catch (reason) {
      if (
        reason instanceof ApplicationPlanningCheckpointNotFoundError ||
        isAuthenticationFailure(reason)
      ) {
        throw reason
      }
      lastError = reason
      if (attempt + 1 < APPLICATION_PLANNING_RECONCILE_ATTEMPTS) {
        await new Promise<void>((resolve) =>
          globalThis.setTimeout(resolve, APPLICATION_PLANNING_RECONCILE_RETRY_DELAY_MS)
        )
      }
    }
  }
  throw lastError instanceof Error ? lastError : new Error('读取应用规划权威状态失败。')
}
