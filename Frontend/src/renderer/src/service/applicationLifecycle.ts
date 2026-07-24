import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type ApplicationLifecyclePayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?: 'create' | 'get' | 'complete_template_generation'
  lifecycle?: ApplicationLifecycle
  error?: { message?: string }
}

const lifecycleReadRequests = new Map<string, Promise<ApplicationLifecycle>>()

// 读取独立应用生命周期 AG-UI 地址。
function getApplicationLifecycleUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/application-lifecycle/run`
    : '/api/agent/application-lifecycle/run'
}

// 校验生命周期 AG-UI 动作的统一响应信封。
function readApplicationLifecyclePayload(
  value: unknown
): ApplicationLifecyclePayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  const payload = value as Partial<ApplicationLifecyclePayload>
  if (
    payload.schemaVersion !== 1 ||
    typeof payload.runId !== 'string' ||
    typeof payload.threadId !== 'string' ||
    !['completed', 'failed'].includes(String(payload.status))
  ) {
    return undefined
  }
  return payload as ApplicationLifecyclePayload
}

// 从 AG-UI StateSnapshot 中读取生命周期动作结果。
function readApplicationLifecycleState(value: unknown): ApplicationLifecyclePayload | undefined {
  if (!value || typeof value !== 'object') return undefined
  return readApplicationLifecyclePayload(
    (value as { applicationLifecycle?: unknown }).applicationLifecycle
  )
}

// 通过独立 AG-UI 端点创建、读取或更新应用生命周期。
async function runApplicationLifecycleAction(
  threadId: string,
  action: Record<string, unknown>
): Promise<ApplicationLifecycle> {
  const agent = createAgUiHttpAgent({ url: getApplicationLifecycleUrl(), threadId })
  agent.addMessage({ id: randomUUID(), role: 'user', content: '同步应用生命周期状态。' })
  let payload: ApplicationLifecyclePayload | undefined
  const subscriber: AgentSubscriber = {
    onCustomEvent: ({ event }) => {
      if (event.name !== 'application-lifecycle') return
      payload = readApplicationLifecyclePayload(event.value) ?? payload
    },
    onStateSnapshotEvent: ({ event }) => {
      payload = readApplicationLifecycleState(event.snapshot) ?? payload
    }
  }
  const result = await agent.runAgent(
    { forwardedProps: { applicationLifecycle: action } },
    subscriber
  )
  payload = readApplicationLifecycleState(result.result) ?? payload
  if (!payload) throw new Error('生命周期接口没有返回有效的 AG-UI 状态。')
  if (payload.status === 'failed') {
    throw new Error(payload.error?.message || '生命周期操作失败。')
  }
  if (!payload.lifecycle) throw new Error('生命周期接口没有返回 lifecycle。')
  return payload.lifecycle
}

// 为新应用显式创建生命周期状态，不读取或推断旧数据。
export async function createApplicationLifecycle(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'create',
    workspaceRoot: application.workspaceRoot,
    application: { id: application.id, appName: application.appName }
  })
}

// 读取权威生命周期，并合并 React StrictMode 等场景产生的同工作区并发请求。
export async function getApplicationLifecycle(
  application: Pick<ApplicationConfig, 'workspaceRoot'>,
  threadId = randomUUID()
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot
  if (!workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  const currentRequest = lifecycleReadRequests.get(workspaceRoot)
  if (currentRequest) return currentRequest

  const request = runApplicationLifecycleAction(threadId, {
    action: 'get',
    workspaceRoot
  })
  lifecycleReadRequests.set(workspaceRoot, request)
  try {
    return await request
  } finally {
    if (lifecycleReadRequests.get(workspaceRoot) === request) {
      lifecycleReadRequests.delete(workspaceRoot)
    }
  }
}

// 把应用模板文件的真实生成结果提交给后端，由状态机决定 ready 或 failed。
export async function completeApplicationTemplateGeneration(
  application: ApplicationConfig,
  threadId: string,
  succeeded: boolean,
  errorMessage?: string
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'complete_template_generation',
    workspaceRoot: application.workspaceRoot,
    succeeded,
    errorMessage
  })
}
