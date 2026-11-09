import assert from 'node:assert/strict'
import http from 'node:http'
import { test } from 'node:test'
import type { BackendConnectionState } from '../src/shared/backendConnection'
import { initializeBackendConnection, currentBackendBaseUrl, resolveBackendUrl } from '../src/renderer/src/service/backendConnection'
import { createAgUiHttpAgent } from '../src/renderer/src/service/authentication'
import { recoveryStore } from '../src/renderer/src/service/recoveryStore'
import { ApplicationPlanningRuntime } from '../src/renderer/src/service/applicationPlanningRuntime'
import { reduceApplicationPlanningCurrentState, type ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { initialConnectionState } from '../src/renderer/src/service/connectionState'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'

test('旧窗口和已缓存会话换地址后使用新 AG-UI，补读和旧请求不能覆盖新结束状态，也不重跑业务', async () => {
  let oldCalls = 0, newCalls = 0
  const oldServer = http.createServer((_request, response) => { oldCalls++; response.end() })
  const nextServer = http.createServer(async (request, response) => {
    let body = ''
    for await (const part of request) body += part
    const payload = JSON.parse(body)
    newCalls++
    response.writeHead(200, { 'Content-Type': 'text/event-stream' })
    for (const event of [
      { type: 'RUN_STARTED', threadId: payload.threadId, runId: payload.runId },
      { type: 'CUSTOM', name: 'transport-test', value: { ok: true } },
      { type: 'RUN_FINISHED', threadId: payload.threadId, runId: payload.runId }
    ]) response.write(`data: ${JSON.stringify(event)}\n\n`)
    response.end()
  })
  await Promise.all([oldServer, nextServer].map((server) => new Promise<void>((resolve) => server.listen(0, '127.0.0.1', resolve))))
  const oldAddress = oldServer.address(), nextAddress = nextServer.address()
  assert.ok(oldAddress && typeof oldAddress === 'object' && nextAddress && typeof nextAddress === 'object')
  const oldUrl = `http://127.0.0.1:${oldAddress.port}`, newUrl = `http://127.0.0.1:${nextAddress.port}`
  let changed!: (state: BackendConnectionState) => void
  let initial!: (state: BackendConnectionState) => void
  const previousWindow = globalThis.window
  Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: { devAgentStudio: {
    agentBaseUrl: oldUrl, backendConnection: {
      getState: () => new Promise<BackendConnectionState>((resolve) => { initial = resolve }),
      onStateChanged: (listener: typeof changed) => { changed = listener; return () => {} }
    }
  } } })
  let runtime: ApplicationPlanningRuntime | undefined
  try {
    const initializing = initializeBackendConnection()
    changed({ revision: 2, status: 'ready', baseUrl: newUrl, backendInstanceId: 'new' })
    initial({ revision: 1, status: 'ready', baseUrl: oldUrl, backendInstanceId: 'old' })
    await initializing
    assert.equal(currentBackendBaseUrl(), newUrl)
    assert.equal(resolveBackendUrl(`${oldUrl}/workflow/run?test=1`), `${newUrl}/workflow/run?test=1`)
    assert.equal(resolveBackendUrl('https://external.example/run'), 'https://external.example/run')
    await createAgUiHttpAgent({ url: `${oldUrl}/transport-test`, threadId: 'transport' }).runAgent()
    assert.equal(oldCalls, 0)
    assert.equal(newCalls, 1)

    const lifecycle = { application: { id: 'transport-app', name: 'transport' }, revision: 1, updatedAt: '',
      initialization: { threadId: 'transport-thread', stage: 'awaiting_ui_design_confirmation', status: 'awaiting_user' },
      activeExecutions: {}, extensions: {} } as ApplicationLifecycle
    let state = { application: { id: 'transport-app', appName: 'transport', workspaceRoot: '/transport' },
      threadId: 'transport-thread', transportState: 'idle', connection: initialConnectionState(true), lifecycle } as ApplicationPlanningCurrentState
    const ended: WorkflowRunPayload = { runId: 'ended', threadId: state.threadId, events: [], state: {},
      summary: { status: 'completed', phase: 'plan_control', planControl: { action: 'end', status: 'ended', targetRunId: 'ended' } } }
    const latest = { workflow: ended, lifecycle: { ...lifecycle, revision: 2 }, recovery: {
      schemaVersion: 'application-planning-recovery.v1' as const, classification: 'completed' as const,
      threadId: state.threadId, canContinue: false, userActionRequired: false, inputCommitted: false,
      reasonCode: 'APPLICATION_PLANNING_ENDED', message: '本轮任务已结束，已有文件已保留。'
    } }
    let oldRead!: (value: typeof latest) => void
    let reads = 0, writes = 0, detaches = 0
    runtime = new ApplicationPlanningRuntime({ applicationId: state.application.id, threadId: state.threadId,
      getCurrentState: () => state, dispatchCurrentEvent: (event) => { state = reduceApplicationPlanningCurrentState(state, event) },
      publishWorkflow: () => {}, publishContent: () => {}, publishReply: () => {}, onTechnicalPlanConfirmed: async () => true,
      onRevisionContinuation: async () => {},
      session: { sendMessage: async () => { writes++; throw new Error('不得重跑') }, stop: async () => { writes++ },
        hasActiveRun: () => false, detach: () => { detaches++ } },
      readAuthoritativeSnapshot: async () => { reads++; if (reads === 1) return new Promise((resolve) => { oldRead = resolve }); return latest }
    })
    const key = recoveryStore.key('/transport', 'transport-app', state.threadId)
    recoveryStore.adopt(key, lifecycle)
    recoveryStore.recordFailure(key, new Error('temporary'))
    const oldRequest = runtime.reconcileCurrentState()
    changed({ revision: 3, status: 'reconnecting', baseUrl: oldUrl, backendInstanceId: 'old' })
    changed({ revision: 4, status: 'ready', baseUrl: newUrl, backendInstanceId: 'new' })
    await new Promise((resolve) => setImmediate(resolve))
    oldRead({ ...latest, lifecycle, workflow: { ...ended, summary: { status: 'requires_user_input', phase: 'ui_confirmation' } } })
    await oldRequest
    assert.equal(reads, 2)
    assert.equal(writes, 0)
    assert.equal(detaches, 2)
    assert.equal(state.workflow?.summary.planControl?.status, 'ended')
    assert.equal(state.lifecycle.revision, 2)
    assert.equal(state.connection.status, 'healthy')
    assert.equal(recoveryStore.canRead(key), true)
  } finally {
    runtime?.dispose()
    Object.defineProperty(globalThis, 'window', { configurable: true, writable: true, value: previousWindow })
    await Promise.all([oldServer, nextServer].map((server) => new Promise<void>((resolve) => server.close(() => resolve()))))
  }
})
