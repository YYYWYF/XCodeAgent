import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { leavePreviewRuntime, runPreviewRuntime } from '../src/renderer/src/service/previewRuntime'
import PreviewRepairControls from '../src/renderer/src/components/BrowserPreviewPanel/PreviewRepairControls'
import { previewServiceActionAvailability } from '../src/renderer/src/components/BrowserPreviewPanel/serviceStatusPolicy'

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
