import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { EventType } from '@ag-ui/core'
import { AgUiChatSession, reconcileWorkflowFailurePayload } from '../src/renderer/src/service/agUiAgent'
import { appendTechnicalPlanningOutput, retainTechnicalPlanningOutput } from '../src/renderer/src/service/technicalPlanningOutput'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import TechnicalPlanningOutput from '../src/renderer/src/components/AiChatPanel/components/MessageList/TechnicalPlanningOutput'

/** 构造与服务端 Run 绑定的最小技术规划投影。 */
function workflow(runId = 'output-run'): WorkflowRunPayload {
  return { runId, threadId: 'output-thread', events: [], summary: { phase: 'technical_planning_generate', status: 'running' } }
}

/** 构造当前契约的公开正文或工具事件，不包含模型推理和工具参数。 */
function output(text: string, extra: Record<string, unknown> = {}): Record<string, unknown> {
  return { runId: 'output-run', threadId: 'output-thread', kind: 'text', text, timestamp: '2026-11-24T04:00:00Z', ...extra }
}

test('实时正文累加、工具原位更新，旧运行和错误身份不能混入当前窗口', () => {
  let current = appendTechnicalPlanningOutput(workflow(), output('第一段'))!
  current = appendTechnicalPlanningOutput(current, output('第二段'))!
  assert.equal(current.technicalPlanningOutput?.entries[0].text, '第一段第二段')
  current = appendTechnicalPlanningOutput(current, output('读取中', { kind: 'activity', id: 'read-1', status: 'running' }))!
  current = appendTechnicalPlanningOutput(current, output('读取完成', { kind: 'activity', id: 'read-1', status: 'completed' }))!
  assert.equal(current.technicalPlanningOutput?.entries.length, 2)
  assert.equal(current.technicalPlanningOutput?.entries[1].status, 'completed')
  assert.equal(appendTechnicalPlanningOutput(current, output('迟到', { runId: 'old' })), current)
  assert.equal(appendTechnicalPlanningOutput(current, output('串线程', { threadId: 'other' })), current)
  assert.equal(retainTechnicalPlanningOutput(current, workflow('new')).technicalPlanningOutput, undefined)
  assert.equal(reconcileWorkflowFailurePayload(current, workflow())?.technicalPlanningOutput, current.technicalPlanningOutput)
  current = appendTechnicalPlanningOutput(current, output('x'.repeat(100_000)))!
  assert.equal(current.technicalPlanningOutput?.truncated, true)
  assert.ok(current.technicalPlanningOutput!.entries.reduce((sum, item) => sum + item.text.length, 0) <= 64_000)
})

test('真实 AG-UI SDK 在结束前发布正文，后续快照保留输出且不写入聊天正文', async () => {
  const previousFetch = globalThis.fetch
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {} })
  let released: (() => void) | undefined
  const gate = new Promise<void>((resolve) => { released = resolve })
  let sawLive = false
  let sawContent = false
  try {
    globalThis.fetch = async (_url, init) => {
      const request = JSON.parse(String(init?.body)) as { runId: string; threadId: string }
      const current = { ...workflow(request.runId), threadId: request.threadId }
      const encoder = new TextEncoder()
      return new Response(new ReadableStream<Uint8Array>({
        /** 分两批返回标准事件，第二批必须等待客户端展示首批输出。 */
        async start(controller) {
          /** 使用标准 AG-UI 对象组装测试流，不替换生产 SDK 的事件消费。 */
          const emit = (event: object): void => { controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`)) }
          emit({ type: EventType.RUN_STARTED, ...request })
          emit({ type: EventType.CUSTOM, name: 'workflow-run', value: current })
          emit({ type: EventType.CUSTOM, name: 'technical-planning.output', value: output('真实流式正文', { ...request }) })
          await gate
          emit({ type: EventType.STATE_SNAPSHOT, snapshot: { workflow: { ...current, summary: { ...current.summary, status: 'requires_user_input' } } } })
          emit({ type: EventType.RUN_FINISHED, ...request })
          controller.close()
        }
      }), { headers: { 'Content-Type': 'text/event-stream' } })
    }
    const result = await new AgUiChatSession('output-thread', 'http://test.invalid/workflow/run').sendMessage('生成技术规划', {
      editorMode: 'frontend',
      onContent: () => { sawContent = true },
      onWorkflow: (current) => {
        if (current.technicalPlanningOutput?.entries[0]?.text === '真实流式正文') {
          sawLive = true
          released?.()
        }
      }
    })
    assert.equal(sawLive, true)
    assert.equal(sawContent, false)
    assert.equal(result.workflow?.summary.status, 'requires_user_input')
    assert.equal(result.workflow?.technicalPlanningOutput?.entries[0].text, '真实流式正文')
  } finally {
    released?.()
    globalThis.fetch = previousFetch
    if (previousWindow) Object.defineProperty(globalThis, 'window', previousWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

test('实时窗口提供真实内容、空态和结束态，没有自动跟随控件或结束后的旋转动画', () => {
  const current = appendTechnicalPlanningOutput(workflow(), output('等待修正', { kind: 'activity', id: 'edit', status: 'running' }))!
  const markup = renderToStaticMarkup(createElement(TechnicalPlanningOutput, { runId: current.runId, output: current.technicalPlanningOutput, running: false }))
  assert.match(markup, /实时输出|输出已结束|等待修正/)
  assert.doesNotMatch(markup, /自动跟随|anticon-loading/)
  const empty = renderToStaticMarkup(createElement(TechnicalPlanningOutput, { runId: current.runId, running: true }))
  assert.match(empty, /等待模型返回内容/)
})
