import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { message } from 'antd'
import { useApplicationTemplateGeneration } from '../src/renderer/src/hooks/useApplicationTemplateGeneration'
import { reduceApplicationPlanningCurrentState, type ApplicationPlanningCurrentState } from '../src/renderer/src/service/activeApplicationPlanning'
import { failConnectionRequest, initialConnectionState } from '../src/renderer/src/service/connectionState'

/** 构造已确认正式规划后的模板失败现场，不制造新的 Graph 运行。 */
function currentTemplate(): ApplicationPlanningCurrentState {
  return {
    application: { id: 'template-test', appName: 'test', workspaceRoot: '/tmp/template-recovery-test' } as ApplicationPlanningCurrentState['application'],
    threadId: 'template-thread', transportState: 'idle', connection: initialConnectionState(true),
    lifecycle: {
      application: { id: 'template-test', name: 'test' }, revision: 2, updatedAt: '2026-10-06T00:00:00Z',
      initialization: { stage: 'application_template_generation_failed', status: 'failed', threadId: 'template-thread' },
      activeExecutions: {}, extensions: {}
    }
  }
}

/** 返回标准 AG-UI 生命周期事件流，测试经过真实客户端解析。 */
function lifecycleResponse(request: Record<string, any>, lifecycle: ApplicationPlanningCurrentState['lifecycle'], failed = false): Response {
  const payload = { schemaVersion: 1, runId: request.runId, threadId: request.threadId, status: failed ? 'failed' : 'completed', lifecycle,
    ...(failed ? { error: { type: 'BootstrapFailure', message: '模板物化失败' } } : {}) }
  const frames = [
    { type: 'RUN_STARTED', runId: request.runId, threadId: request.threadId },
    { type: 'CUSTOM', name: 'application-lifecycle', value: payload },
    { type: 'STATE_SNAPSHOT', snapshot: { applicationLifecycle: payload } },
    { type: 'RUN_FINISHED', runId: request.runId, threadId: request.threadId }
  ]
  return new Response(frames.map(frame => `data: ${JSON.stringify(frame)}\n\n`).join(''), { headers: { 'content-type': 'text/event-stream' } })
}

test('模板任务失败事件保留断线状态，不改变规划及恢复事实', () => {
  const current = currentTemplate()
  current.connection = failConnectionRequest(current.connection, 1, 'Backend 不可达')
  const next = reduceApplicationPlanningCurrentState(current, { type: 'template_generation_failed',
    applicationId: current.application.id, threadId: current.threadId, error: '模板初始化失败' })
  assert.equal(next.connection.status, 'unavailable')
  assert.equal(next.lifecycle, current.lifecycle)
  assert.equal(next.workflow, current.workflow)
  assert.equal(next.recovery, current.recovery)
  const wrongThread = reduceApplicationPlanningCurrentState(current, { type: 'template_generation_failed',
    applicationId: current.application.id, threadId: 'old-thread', error: '旧错误', connectionError: '旧断线' })
  assert.equal(wrongThread, current)
})

test('模板专用重试先校准；业务失败与通信失败分别处理，运行中不重发', async () => {
  const savedFetch = globalThis.fetch
  const savedWindow = globalThis.window
  const savedMessageError = message.error
  const savedConsoleError = console.error
  const savedConsoleWarn = console.warn
  message.error = (() => undefined) as typeof message.error
  console.error = () => undefined
  console.warn = () => undefined
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } } })
  try {
    for (const scenario of ['business', 'transport', 'running']) {
      let current = currentTemplate()
      const actions: string[] = []
      let bootstrapRequested = false
      globalThis.fetch = async (url, init) => {
        assert.ok(String(url).endsWith('/application-lifecycle/run'))
        const request = JSON.parse(String(init?.body))
        const action = request.forwardedProps.applicationLifecycle.action
        actions.push(action)
        if (action === 'retry_bootstrap_template_generation') {
          bootstrapRequested = true
          if (scenario === 'transport') throw new TypeError('Backend 不可达')
          return lifecycleResponse(request, current.lifecycle, true)
        }
        if (bootstrapRequested && scenario === 'transport') throw new TypeError('Backend 不可达')
        const lifecycle = scenario === 'running' ? { ...current.lifecycle, initialization: {
          ...current.lifecycle.initialization, stage: 'generating_application_template_files' as const, status: 'running' as const
        }} : current.lifecycle
        return lifecycleResponse(request, lifecycle)
      }
      let controller!: ReturnType<typeof useApplicationTemplateGeneration>
      /** 捕获生产模板编排，保留真实 AG-UI 请求和 Canonical reducer。 */
      function Probe() {
        controller = useApplicationTemplateGeneration({
          dispatchPlanningEvent: event => { current = reduceApplicationPlanningCurrentState(current, event) },
          hidePlanning: () => { throw new Error('失败流程不应导航') },
          getVisiblePlanningId: () => current.application.id,
          onOpenWorkbench: () => { throw new Error('失败流程不应进入工作台') }
        })
        return createElement('div')
      }
      renderToStaticMarkup(createElement(Probe))
      assert.equal(await controller.retryApplicationTemplateFiles(current), false)
      assert.deepEqual(actions.slice(0, 2), ['workspace_attach', 'get'])
      assert.equal(current.connection.status, scenario === 'transport' ? 'unavailable' : 'healthy')
      assert.equal(current.workflow, undefined)
      assert.equal(current.recovery, undefined)
      assert.equal(actions.filter(action => action === 'retry_bootstrap_template_generation').length, scenario === 'running' ? 0 : 1)
    }
  } finally {
    globalThis.fetch = savedFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: savedWindow })
    message.error = savedMessageError
    console.error = savedConsoleError
    console.warn = savedConsoleWarn
  }
})
