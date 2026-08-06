// 假 AG-UI HttpAgent：仅 lifecycle 动作返回统一信封，其余返回空 result。
// 由 service/authentication.ts 在浏览器 mock 环境直接使用（不经 Vite 插件重定向）。
import { appDataByWorkspace, mockApplications } from './fixtures'
import type { ApplicationLifecycle } from '../typings'

// 模拟后端按工作区持久化的生命周期（appId 与阶段随动作演进）。
// create → 进行中（收集需求）；complete_template_generation → 就绪；get → 返回已存状态。
const lifecycleStore = new Map<string, { appId: string; appName: string; stage: string; status: string; threadId?: string }>()

// 预置镜像应用的初始生命周期：pms-design → 设计(collecting_requirement)、pms-dev → 开发(ready_for_workbench)。
// 这样 mockApplicationInPlanning 能按 workspace 区分设计期/开发期，sessions 与阶段自动分流。
for (const app of mockApplications) {
  const scenario = appDataByWorkspace(app.workspaceRoot)
  const init = scenario.lifecycle.initialization
  lifecycleStore.set(app.workspaceRoot || '', {
    appId: app.id,
    appName: app.name,
    stage: init.stage,
    status: init.status
  })
}

// 工作台剧本注册的当前 lifecycle（含 activeExecutions），供 get 时返回，
// 避免 getApplicationLifecycle 用旧状态覆盖掉工作台执行快照（导致交互校验失效）。
let registeredWorkbenchLifecycle: ApplicationLifecycle | undefined
export function registerWorkbenchLifecycle(lifecycle: ApplicationLifecycle): void {
  registeredWorkbenchLifecycle = lifecycle
}

const PLANNING_STAGES = new Set([
  'collecting_requirement',
  'analyzing_requirement',
  'awaiting_requirement_clarification',
  'generating_requirement_spec',
  'awaiting_requirement_confirmation',
  'generating_project_plan',
  'awaiting_project_plan_confirmation',
  'generating_build_task_plan'
])

/** 工作区应用是否仍处于规划(设计)阶段——规划期的应用不返回已设计页会话。 */
export function mockApplicationInPlanning(workspaceRoot: string): boolean {
  const stored = lifecycleStore.get(workspaceRoot)
  return Boolean(stored && PLANNING_STAGES.has(stored.stage))
}

// 构造 lifecycle 动作的统一响应信封。
function lifecyclePayload(threadId: string, action: Record<string, unknown>): Record<string, unknown> {
  const workspaceRoot = String(action.workspaceRoot || '')
  const actionApplication = action.application as { id?: string; appName?: string } | undefined

  if (action.action === 'create' && actionApplication?.id) {
    lifecycleStore.set(workspaceRoot, {
      appId: actionApplication.id,
      appName: actionApplication.appName || '应用',
      stage: 'collecting_requirement',
      status: 'running',
      threadId
    })
  } else if (action.action === 'complete_template_generation') {
    const current = lifecycleStore.get(workspaceRoot)
    if (current) {
      lifecycleStore.set(workspaceRoot, { ...current, stage: 'ready_for_workbench', status: 'completed' })
    }
  }

  const stored = lifecycleStore.get(workspaceRoot)
  const scenario = appDataByWorkspace(workspaceRoot)
  const app = stored
    ? { id: stored.appId, name: stored.appName }
    : { id: scenario.app.id, name: scenario.app.name }

  // 工作台已注册的 lifecycle（含 activeExecutions）与目标应用匹配时，直接返回它。
  if (registeredWorkbenchLifecycle && registeredWorkbenchLifecycle.application.id === app.id) {
    return {
      schemaVersion: 1,
      runId: `mock-lc-${Date.now()}`,
      threadId,
      status: 'completed',
      action: action.action,
      lifecycle: { ...registeredWorkbenchLifecycle, revision: (registeredWorkbenchLifecycle.revision || 0) + 1 }
    }
  }

  // 恢复进行中计划时，前端用随机 threadId 调 get 校验归属；
  // 若持久化没有 threadId（预置的待交互阶段），则回显请求 threadId，
  // 让 loadActiveApplicationPlannings 拿到非空初始化线程标识而不报错。
  const initialization = stored
    ? { stage: stored.stage, status: stored.status, threadId: stored.threadId ?? threadId }
    : { stage: scenario.lifecycle.initialization.stage, status: scenario.lifecycle.initialization.status }

  return {
    schemaVersion: 1,
    runId: `mock-lc-${Date.now()}`,
    threadId,
    status: 'completed',
    action: action.action,
    lifecycle: {
      ...scenario.lifecycle,
      application: app,
      revision: (scenario.lifecycle.revision || 0) + 1,
      initialization
    }
  }
}

// 构造假 agent：lifecycle 动作触发订阅回调并返回信封；其它返回空 result（组件多已 catch）。
export function createMockHttpAgent(config: { url?: string; threadId?: string }) {
  const threadId = config.threadId || 'mock-lc-thread'
  const base = {
    addMessage: () => undefined,
    async runAgent(
      options: { forwardedProps?: Record<string, unknown> },
      subscriber?: {
        onCustomEvent?: (payload: { event: { name?: string; value?: unknown } }) => void
        onStateSnapshotEvent?: (payload: { event: { snapshot?: unknown } }) => void
      }
    ) {
      const props = options?.forwardedProps || {}
      const action = props.applicationLifecycle
      if (action && typeof action === 'object') {
        const payload = lifecyclePayload(threadId, action as Record<string, unknown>)
        subscriber?.onCustomEvent?.({ event: { name: 'application-lifecycle', value: payload } })
        subscriber?.onStateSnapshotEvent?.({ event: { snapshot: { applicationLifecycle: payload } } })
        return { result: { applicationLifecycle: payload }, status: 'completed' }
      }
      // 需求文档草稿保存：原样回传 spec + 一个需求文档 artifact。
      const draftAction = props.requirementSpecDraft
      if (draftAction && typeof draftAction === 'object') {
        const spec = ((draftAction as Record<string, unknown>).spec as Record<string, unknown>) || {}
        const draftPayload = {
          schemaVersion: 1,
          runId: `mock-spec-${Date.now()}`,
          threadId,
          status: 'completed',
          action: 'save',
          requirementSpec: spec,
          artifact: {
            id: 'requirement_spec',
            name: '需求文档',
            path: 'specs/requirement.md',
            format: 'markdown',
            content: '# 需求文档（已编辑保存）\n\n已按你的编辑同步到 Markdown。'
          }
        }
        subscriber?.onCustomEvent?.({ event: { name: 'requirement-spec-draft', value: draftPayload } })
        subscriber?.onStateSnapshotEvent?.({ event: { snapshot: { requirementSpecDraft: draftPayload } } })
        return { result: { requirementSpecDraft: draftPayload }, status: 'completed' }
      }
      return { result: {}, status: 'completed' }
    },
    async invoke() {
      return { result: {}, status: 'completed' }
    },
    async stream() {
      return { result: {}, status: 'completed' }
    },
    async runConversation() {
      return { result: {}, status: 'completed' }
    },
    async stop() {}
  }
  // 未实现的方法兜底为 benign 空结果。
  return new Proxy(base, {
    get(target, prop, receiver) {
      if (typeof prop === 'string' && prop in target) return Reflect.get(target, prop, receiver)
      return async () => ({ result: {}, status: 'completed' })
    }
  })
}
