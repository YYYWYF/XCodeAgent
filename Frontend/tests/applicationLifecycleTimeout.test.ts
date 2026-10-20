import assert from 'node:assert/strict'
import { test } from 'node:test'
import { getApplicationLifecycle } from '../src/renderer/src/service/applicationLifecycle'
import { refreshWorkbenchRun } from '../src/renderer/src/components/AiChatPanel/hooks/useWorkbenchRunRefresh'
import { failConnectionRequest, initialConnectionState } from '../src/renderer/src/service/connectionState'
import type { ApplicationLifecycle } from '../src/renderer/src/typings'

/** 用标准事件信封覆盖真实 AG-UI 客户端，不绕过生产请求与共享查询缓存。 */
function response(request: Record<string, any>, lifecycle: ApplicationLifecycle): Response {
  const payload = { schemaVersion: 1, runId: request.runId, threadId: request.threadId,
    status: 'completed', lifecycle }
  const frames = [
    { type: 'RUN_STARTED', runId: request.runId, threadId: request.threadId },
    { type: 'CUSTOM', name: 'application-lifecycle', value: payload },
    { type: 'STATE_SNAPSHOT', snapshot: { applicationLifecycle: payload } },
    { type: 'RUN_FINISHED', runId: request.runId, threadId: request.threadId }
  ]
  return new Response(frames.map(frame => `data: ${JSON.stringify(frame)}\n\n`).join(''),
    { headers: { 'content-type': 'text/event-stream' } })
}

test('Attach/Get 永不返回时按时退出、释放共享查询，重新同步不执行生成或恢复', async () => {
  const savedFetch = globalThis.fetch
  const savedWindow = globalThis.window
  const savedTimeout = globalThis.setTimeout
  Object.defineProperty(globalThis, 'window', { configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } } })
  // 只加速生产的 20 秒查询定时器，保留 AG-UI 自身调度行为。
  globalThis.setTimeout = ((handler, delay, ...args) =>
    savedTimeout(handler, delay === 20_000 ? 0 : delay, ...args)) as typeof globalThis.setTimeout
  try {
    for (const blockedAction of ['workspace_attach', 'get']) {
      const application = { id: `timeout-${blockedAction}`, workspaceRoot: `/tmp/timeout-${blockedAction}` }
      const lifecycle = { application: { id: application.id }, revision: 1,
        initialization: { stage: 'ready_for_workbench', status: 'completed' },
        activeExecutions: {} } as ApplicationLifecycle
      const actions: string[] = []
      let shouldHang = true
      let lateResolve!: (value: Response) => void
      let blockedRequest!: Record<string, any>
      let blockedSignal: AbortSignal | undefined
      globalThis.fetch = async (_url, init) => {
        const request = JSON.parse(String(init?.body))
        const action = request.forwardedProps.applicationLifecycle.action
        actions.push(action)
        if (shouldHang && action === blockedAction) {
          shouldHang = false
          blockedRequest = request
          blockedSignal = init?.signal || undefined
          // 故意不响应 abort：生产逻辑必须自行退出等待，不能依赖网络完成取消。
          return new Promise<Response>(resolve => { lateResolve = resolve })
        }
        return response(request, lifecycle)
      }
      let state = initialConnectionState(true)
      let merges = 0
      const first = getApplicationLifecycle(application)
      const shared = getApplicationLifecycle(application)
      const read = refreshWorkbenchRun(() => first, () => { merges++ }, {
        start: () => 1,
        isCurrentRequest: () => true,
        settle: (generation, error) => {
          if (error) state = failConnectionRequest(state, generation, error)
        }
      }, () => true)
      const outcomes = await Promise.allSettled([first, shared, read])
      assert.equal(outcomes[0].status, 'rejected')
      assert.equal(outcomes[1].status, 'rejected')
      assert.match(state.lastError || '', /应用状态同步超时/)
      assert.equal(state.status, 'unavailable')
      assert.equal(blockedSignal?.aborted, true)
      assert.equal(merges, 0)
      // 下一次必须发出全新的 Attach/Get，而不是继续等待缓存中的旧 Promise。
      const before = actions.length
      assert.deepEqual(await getApplicationLifecycle(application), lifecycle)
      assert.deepEqual(actions.slice(before), ['workspace_attach', 'get'])
      lateResolve(response(blockedRequest, { ...lifecycle, revision: 99 }))
      await new Promise(resolve => savedTimeout(resolve, 0))
      assert.equal(merges, 0, '超时旧响应不得再回写生命周期')
      assert.ok(actions.every(action => ['workspace_attach', 'get'].includes(action)))
    }
  } finally {
    globalThis.fetch = savedFetch
    globalThis.setTimeout = savedTimeout
    Object.defineProperty(globalThis, 'window', { configurable: true, value: savedWindow })
  }
})
