import { randomUUID } from '@ag-ui/client'
import type { AgentSubscriber } from '@ag-ui/client'
import type { ApplicationConfig, ApplicationLifecycle } from '../typings'
import { createAgUiHttpAgent } from './authentication'

type ApplicationLifecyclePayload = {
  schemaVersion: 1
  runId: string
  threadId: string
  status: 'completed' | 'failed'
  action?:
    | 'create'
    | 'get'
    | 'bootstrap_template_generation'
    | 'retry_bootstrap_template_generation'
    | 'workspace_attach'
    | 'release_session_pending'
  lifecycle?: ApplicationLifecycle
  sessionPendingReleased?: boolean
  error?: { type?: string; message?: string }
}

/** 工作区尚未建立生命周期状态：后端以 ApplicationLifecycleMissingError 单独标识，可幂等补建。 */
const MISSING_LIFECYCLE_ERROR_TYPE = 'ApplicationLifecycleMissingError'

/** 携带后端错误类型的生命周期动作失败，供调用方按类型决定是否补建状态。 */
class ApplicationLifecycleActionError extends Error {
  readonly errorType: string | undefined

  constructor(errorType: string | undefined, message: string) {
    super(message)
    this.name = 'ApplicationLifecycleActionError'
    this.errorType = errorType
  }
}

/** 判定失败是否只是"工作区还没有生命周期状态"，区别于损坏或归属冲突等真实故障。 */
function isLifecycleMissingError(error: unknown): boolean {
  return (
    error instanceof ApplicationLifecycleActionError &&
    error.errorType === MISSING_LIFECYCLE_ERROR_TYPE
  )
}

const lifecycleReadRequests = new Map<string, Promise<ApplicationLifecycle>>()
const workspaceAttachRequests = new Map<string, Promise<ApplicationLifecycle>>()

// 校验生命周期快照只属于当前应用及初始化线程，禁止同目录或异步回包造成跨应用串态。
function assertApplicationLifecycleOwnership(
  lifecycle: ApplicationLifecycle,
  applicationId?: string,
  threadId?: string
): ApplicationLifecycle {
  if (applicationId && lifecycle.application.id !== applicationId) {
    throw new Error('当前工作区已属于另一个应用，请为新应用选择独立的项目目录。')
  }
  const lifecycleThreadId = lifecycle.initialization.threadId
  if (threadId && lifecycleThreadId && lifecycleThreadId !== threadId) {
    throw new Error('当前工作区已有另一个应用规划线程，请为每个应用使用独立的项目目录。')
  }
  return lifecycle
}

// 读取独立应用生命周期 AG-UI 地址。
function getApplicationLifecycleUrl(): string {
  const agentBaseUrl = window.devAgentStudio?.agentBaseUrl
  return agentBaseUrl
    ? `${agentBaseUrl.replace(/\/$/, '')}/application-lifecycle/run`
    : '/api/agent/application-lifecycle/run'
}

// 校验生命周期 AG-UI 动作的统一响应信封。
function readApplicationLifecyclePayload(value: unknown): ApplicationLifecyclePayload | undefined {
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
    throw new ApplicationLifecycleActionError(
      payload.error?.type,
      payload.error?.message || '生命周期操作失败。'
    )
  }
  if (!payload.lifecycle) throw new Error('生命周期接口没有返回 lifecycle。')
  return payload.lifecycle
}

// 为新应用显式创建生命周期状态，不读取或推断旧数据。
// inheritedArtifacts 仅在发起新迭代时传入：新迭代保留已有工程代码，上一版本已开发的
// 产物仍然存在，服务端只继承其中的 completed 事实（pending/in_progress 由本轮重新推导）。
export async function createApplicationLifecycle(
  application: Pick<ApplicationConfig, 'workspaceRoot' | 'id' | 'appName'>,
  threadId: string,
  inheritedArtifacts?: ApplicationLifecycle['developmentArtifacts']
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return assertApplicationLifecycleOwnership(
    await runApplicationLifecycleAction(threadId, {
      action: 'create',
      workspaceRoot: application.workspaceRoot,
      application: { id: application.id, appName: application.appName },
      ...(inheritedArtifacts ? { inheritedDevelopmentArtifacts: inheritedArtifacts } : {})
    }),
    application.id,
    threadId
  )
}

// 先接管可能中断的 Workspace，再读取权威生命周期，并合并 StrictMode 等并发请求。
export async function getApplicationLifecycle(
  application: Pick<ApplicationConfig, 'workspaceRoot'> &
    Partial<Pick<ApplicationConfig, 'id' | 'appName'>>,
  threadId = randomUUID()
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot
  if (!workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  const currentRequest = lifecycleReadRequests.get(workspaceRoot)
  if (currentRequest) {
    return assertApplicationLifecycleOwnership(await currentRequest, application.id)
  }

  // 每次冷读取前先 Attach：后端 get 始终只读，孤儿 Bootstrap 的回收只能由 Attach 完成。
  const request = (async (): Promise<ApplicationLifecycle> => {
    try {
      await attachApplicationWorkspace(application, threadId)
      return await runApplicationLifecycleAction(threadId, {
        action: 'get',
        workspaceRoot
      })
    } catch (error) {
      // 发起新迭代会先删掉 lifecycle 文件再由前端重建；这一步中途失败（或文件被外部清理）
      // 会让 Attach 与 Get 双双失败，工作区从此打不开。用本应用已知身份补建一份再读：
      // 后端 create 幂等（已存在则原样返回），损坏或归属冲突仍会照常抛出，不会掩盖真实故障。
      if (!isLifecycleMissingError(error) || !application.id || !application.appName) throw error
      await createApplicationLifecycle(
        { workspaceRoot, id: application.id, appName: application.appName },
        threadId
      )
      return runApplicationLifecycleAction(threadId, { action: 'get', workspaceRoot })
    }
  })()
  lifecycleReadRequests.set(workspaceRoot, request)
  try {
    return assertApplicationLifecycleOwnership(await request, application.id)
  } finally {
    if (lifecycleReadRequests.get(workspaceRoot) === request) {
      lifecycleReadRequests.delete(workspaceRoot)
    }
  }
}

// 通过独立 lifecycle AG-UI 收口指定 Session 拥有的 Pending Build DAG。
export async function releaseSessionPendingPlan(
  workspaceRoot: string,
  sessionId: string
): Promise<ApplicationLifecycle> {
  if (!workspaceRoot.trim()) throw new Error('收口 PendingPlan 前需要工作目录。')
  const normalizedSessionId = sessionId.trim()
  if (!normalizedSessionId) throw new Error('收口 PendingPlan 前需要合法的 sessionId。')
  return runApplicationLifecycleAction(randomUUID(), {
    action: 'release_session_pending',
    workspaceRoot,
    sessionId: normalizedSessionId
  })
}

// 接管指定工作区的中断 Bootstrap；同一工作区的并发恢复请求必须共用一次 AG-UI 调用。
export async function attachApplicationWorkspace(
  application: Pick<ApplicationConfig, 'workspaceRoot'> & Partial<Pick<ApplicationConfig, 'id'>>,
  threadId = randomUUID()
): Promise<ApplicationLifecycle> {
  const workspaceRoot = application.workspaceRoot
  if (!workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  const currentRequest = workspaceAttachRequests.get(workspaceRoot)
  if (currentRequest) {
    return assertApplicationLifecycleOwnership(await currentRequest, application.id)
  }

  const request = runApplicationLifecycleAction(threadId, {
    action: 'workspace_attach',
    workspaceRoot
  })
  workspaceAttachRequests.set(workspaceRoot, request)
  try {
    return assertApplicationLifecycleOwnership(await request, application.id)
  } finally {
    if (workspaceAttachRequests.get(workspaceRoot) === request) {
      workspaceAttachRequests.delete(workspaceRoot)
    }
  }
}

// 由 Backend 持有真实任务，Renderer 只通过 AG-UI 触发并等待最终 lifecycle。
export async function bootstrapApplicationTemplateGeneration(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'bootstrap_template_generation',
    workspaceRoot: application.workspaceRoot
  })
}

// 仅在后端已标记模板生成失败时，显式开启一轮新的 Bootstrap。
export async function retryApplicationTemplateGeneration(
  application: ApplicationConfig,
  threadId: string
): Promise<ApplicationLifecycle> {
  if (!application.workspaceRoot) throw new Error('应用缺少 workspaceRoot。')
  return runApplicationLifecycleAction(threadId, {
    action: 'retry_bootstrap_template_generation',
    workspaceRoot: application.workspaceRoot
  })
}
