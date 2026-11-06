import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import { useWorkflowDocuments } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard/useWorkflowDocuments'

/** 用官方客户端可消费的协议回执完成一次受控资料读取。 */
function reply(content: unknown, failed = false): Response {
  const payload = failed
    ? { status: 'failed', error: { message: '资料内容校验失败。' } }
    : { status: 'completed', content }
  const events = [
    { type: 'RUN_STARTED', threadId: 'documents', runId: 'read' },
    { type: 'STATE_SNAPSHOT', snapshot: { workflowArtifact: payload } },
    {
      type: 'RUN_FINISHED',
      threadId: 'documents',
      runId: 'read',
      result: { workflowArtifact: payload }
    }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

/** 等待 SDK 消费帧和 React 状态更新，不使用真实网络或计时等待。 */
async function flush(): Promise<void> {
  for (let count = 0; count < 4; count += 1)
    await new Promise<void>((resolve) => setImmediate(resolve))
}

test('文档面板按引用加载并隔离工作区、迟到回执与失败，禁用后不预取', async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalFetch = globalThis.fetch
  const pending: Array<{ workspace: string; id: string; resolve: (value: Response) => void }> = []
  const reference = { artifactId: 'a'.repeat(64), byteLength: 10, mediaType: 'application/json' }
  const source = {
    runId: 'current',
    threadId: 'thread',
    summary: { status: 'requires_user_input', phase: 'ui_confirmation' },
    state: { artifactRefs: { requirement_spec: reference, product_plan: reference } }
  } as WorkflowRunPayload
  const keys = ['requirement_spec', 'product_plan']
  let view: ReturnType<typeof useWorkflowDocuments>
  let renderer: ReactTestRenderer | undefined
  /** 只渲染生产 Hook，允许模拟同一快照切换工作区。 */
  function Harness({ workspace, enabled = true }: { workspace: string; enabled?: boolean }): null {
    view = useWorkflowDocuments(source, workspace, enabled, keys)
    return null
  }
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { devAgentStudio: { agentBaseUrl: 'http://fixture.invalid' } }
  })
  globalThis.fetch = async (input, init) => {
    assert.equal(String(input), 'http://fixture.invalid/workflow-artifacts/run')
    const body = JSON.parse(String(init?.body))
    return new Promise<Response>((resolve) =>
      pending.push({
        workspace: body.forwardedProps.artifact.workspaceRoot,
        id: body.forwardedProps.artifact.reference.artifactId,
        resolve
      })
    )
  }
  try {
    await act(async () => {
      renderer = create(createElement(Harness, { workspace: '/first' }))
      await flush()
    })
    assert.equal(view!.loading, true)
    assert.equal(pending.length, 1)
    // 请求尚未返回就切换工作区，旧正文不得显示到新工作区。
    await act(async () => {
      renderer!.update(createElement(Harness, { workspace: '/second' }))
      await flush()
    })
    assert.equal(pending.length, 2)
    await act(async () => {
      pending[0].resolve(reply({ name: '旧正文' }))
      await flush()
    })
    assert.equal(view!.workflow?.state?.requirement_spec, undefined)
    assert.equal(pending.length, 2)
    await act(async () => {
      pending[1].resolve(reply({ name: '当前需求' }))
      await flush()
    })
    assert.equal(pending[2].workspace, '/second')
    await act(async () => {
      pending[2].resolve(reply({ pages: ['current'] }))
      await flush()
    })
    assert.equal(view!.loading, false)
    assert.deepEqual(view!.workflow?.state?.requirement_spec, { name: '当前需求' })
    assert.deepEqual(view!.workflow?.state?.product_plan, { pages: ['current'] })
    assert.equal(source.state?.requirement_spec, undefined)
    await act(async () => {
      renderer!.update(createElement(Harness, { workspace: '/failed' }))
      await flush()
    })
    assert.equal(view!.workflow?.state?.requirement_spec, undefined)
    await act(async () => {
      pending[3].resolve(reply(null, true))
      await flush()
    })
    assert.equal(view!.error, '资料内容校验失败。')
    await act(async () => {
      renderer!.update(createElement(Harness, { workspace: '/disabled', enabled: false }))
      await flush()
    })
    assert.equal(pending.length, 4)
    assert.equal(view!.loading, false)
    assert.equal(view!.workflow, source)
  } finally {
    act(() => renderer?.unmount())
    globalThis.fetch = originalFetch
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})
