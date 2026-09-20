import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { leavePreviewRuntime, runPreviewRuntime } from '../src/renderer/src/service/previewRuntime'
import PreviewRepairControls from '../src/renderer/src/components/BrowserPreviewPanel/PreviewRepairControls'
import {
  previewServiceActionAvailability,
  previewServiceState,
  shouldAutoStartPreviewService
} from '../src/renderer/src/components/BrowserPreviewPanel/serviceStatusPolicy'

const originalFetch = globalThis.fetch
Object.assign(globalThis, { window: { xcodeAgent: { agentBaseUrl: 'http://127.0.0.1:8000' } } })
let forwarded: Record<string, unknown> | undefined

/** 用规范 AG-UI 生命周期验证真实 HttpAgent 的事件消费，不模拟客户端内部解析。 */
function mockStream(failed = false): void {
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body))
    forwarded = body.forwardedProps.previewRuntime
    const value = {
      status: failed ? 'failed' : 'completed',
      error: failed ? { message: '当前应用有任务等待确认' } : undefined,
      runtime: { attemptId: 'attempt-1', status: 'failed', repairAvailable: true },
      repair: { status: 'awaiting_confirmation', planId: 'plan-1', message: '请确认计划' }
    }
    const events = [
      { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
      { type: 'TEXT_MESSAGE_START', messageId: 'm', role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm', delta: '正在诊断' },
      { type: 'CUSTOM', name: 'preview-runtime', value },
      { type: 'STATE_SNAPSHOT', snapshot: { previewRuntime: value } },
      { type: 'TEXT_MESSAGE_END', messageId: 'm' },
      {
        type: 'RUN_FINISHED',
        threadId: body.threadId,
        runId: body.runId,
        result: { previewRuntime: value }
      }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  }
}

try {
  mockStream()
  const updates: string[] = []
  const value = await runPreviewRuntime(
    { workspace: '/workspace', action: 'diagnose', attemptId: 'attempt-1' },
    { threadId: 'repair-session', onUpdate: (event) => updates.push(event.status) }
  )
  assert.equal(value.repair?.planId, 'plan-1')
  assert.ok(updates.includes('completed'))
  assert.deepEqual(forwarded, {
    workspace: '/workspace',
    action: 'diagnose',
    attemptId: 'attempt-1'
  })
  await leavePreviewRuntime('/workspace')
  assert.deepEqual(forwarded, {
    workspace: '/workspace',
    action: 'leave',
    includeLogs: false
  })
  mockStream(true)
  await assert.rejects(
    runPreviewRuntime({ workspace: '/workspace', action: 'restart' }),
    /等待确认/
  )

  assert.deepEqual(
    previewServiceActionAvailability({ busy: false, blockedReason: '' }),
    { canRestart: true, canDiagnose: false },
    '初始运行快照尚未返回时应允许重启，但不能提前开放诊断修复'
  )
  assert.deepEqual(
    previewServiceActionAvailability({
      busy: false,
      blockedReason: '',
      repairAvailable: true
    }),
    { canRestart: true, canDiagnose: true }
  )
  assert.equal(
    previewServiceActionAvailability({ busy: false, blockedReason: '会话执行中' }).canRestart,
    true,
    '应用任务占用期间仍应允许手动重启服务'
  )

  // 服务状态收敛：它同时驱动面板状态标签与"切到预览 tab 是否要自动启动"，
  // 判错会导致要么该启动不启动（白屏），要么把正在跑的服务重复拉起。
  assert.equal(previewServiceState(undefined), 'idle', '无快照时视为未启动')
  assert.equal(previewServiceState({}), 'idle')
  assert.equal(
    previewServiceState({ frontend: { status: 'stopped' }, backend: { status: 'stopped' } }),
    'idle'
  )
  assert.equal(previewServiceState({ frontend: { status: 'running' } }), 'running')
  assert.equal(previewServiceState({ backend: { status: 'running' } }), 'running')
  assert.equal(previewServiceState({ frontend: { status: 'starting' } }), 'starting')
  assert.equal(previewServiceState({ frontend: { status: 'failed' } }), 'failed')
  // failed/starting 优先于 running：一端在跑另一端已失败时不能报"运行中"。
  assert.equal(
    previewServiceState({ frontend: { status: 'running' }, backend: { status: 'failed' } }),
    'failed'
  )
  assert.equal(
    previewServiceState({ frontend: { status: 'running' }, backend: { status: 'starting' } }),
    'starting'
  )

  // 切到预览 tab 的自动启动判断：漏判会让历史版本预览白屏，误判会重复拉起正在跑的服务。
  const autoStart = (over: Partial<Parameters<typeof shouldAutoStartPreviewService>[0]> = {}) =>
    shouldAutoStartPreviewService({
      activeTabIsPreview: true,
      hasSnapshot: true,
      busy: false,
      status: 'idle',
      alreadyRequested: false,
      ...over
    })
  assert.equal(autoStart(), true, '停在预览 tab 且服务未启动时应自动拉起')
  assert.equal(autoStart({ activeTabIsPreview: false }), false, '停在应用文件 tab 不应启动服务')
  assert.equal(autoStart({ hasSnapshot: false }), false, '快照未到达时不得当成待启动')
  assert.equal(autoStart({ status: 'running' }), false, '服务已在运行不应重复拉起')
  assert.equal(autoStart({ status: 'starting' }), false, '正在启动不应重复拉起')
  assert.equal(autoStart({ status: 'failed' }), false, '失败交给用户诊断，不自动重启')
  assert.equal(autoStart({ busy: false, alreadyRequested: true }), false, '同一次进入只启动一次')

  const awaiting = renderToStaticMarkup(
    createElement(PreviewRepairControls, {
      repair: { status: 'awaiting_confirmation', message: '请确认本轮计划' },
      busy: false,
      onAction: async () => {},
      onRevision: () => {}
    })
  )
  assert.match(awaiting, /确认并修复/)
  assert.match(awaiting, /停止修复/)
  assert.doesNotMatch(awaiting, /提交不会自动确认计划/)
  assert.doesNotMatch(awaiting, /重新诊断/)
  assert.doesNotMatch(awaiting, /textarea/)
  const complete = renderToStaticMarkup(
    createElement(PreviewRepairControls, {
      repair: { status: 'completed', message: '预览服务已恢复' },
      busy: false,
      onAction: async () => {},
      onRevision: () => {}
    })
  )
  assert.doesNotMatch(complete, /测试通过/)
  assert.ok((complete.match(/disabled=""/g) || []).length >= 2)
  console.log('preview runtime AG-UI client and repair controls: passed')
} finally {
  globalThis.fetch = originalFetch
}
