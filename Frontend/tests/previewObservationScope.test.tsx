import assert from 'node:assert/strict'
import { createElement } from 'react'
import { act, create } from 'react-test-renderer'
import type { ReactTestRenderer } from 'react-test-renderer'
import { usePreviewRuntimeConnection } from '../src/renderer/src/components/AiChatPanel/hooks/usePreviewRuntimeConnection'
import type { PreviewRuntimePayload } from '../src/renderer/src/service/previewRuntime'

const nativeFetch = globalThis.fetch
const requests: Array<{ body: { threadId: string; runId: string }; resolve: (response: Response) => void }> = []
globalThis.fetch = async (_input, init) => new Promise<Response>((resolve) => {
  requests.push({ body: JSON.parse(String(init?.body)), resolve })
})
let root: ReactTestRenderer | undefined
let connection: ReturnType<typeof usePreviewRuntimeConnection> | undefined
const received: PreviewRuntimePayload[] = []
/** 收集经过生产连接代次校验后的快照。 */
const receive = (value: PreviewRuntimePayload): void => { received.push(value) }
/** 真实挂载连接 Hook，覆盖切工程后又返回原工程的 ABA 场景。 */
function Probe({ workspace }: { workspace: string }): null {
  connection = usePreviewRuntimeConnection(workspace, undefined, false, receive)
  return null
}
/** 用规范生命周期交付迟到结果，由真实 HttpAgent 完成事件消费。 */
function answer(index: number): void {
  const request = requests[index]
  const value = { status: 'completed' }
  const events = [
    { type: 'RUN_STARTED', ...request.body },
    { type: 'CUSTOM', name: 'preview-runtime', value },
    { type: 'RUN_FINISHED', ...request.body, result: { previewRuntime: value } }
  ]
  request.resolve(new Response(events.map(event => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  }))
}
try {
  await act(async () => { root = create(createElement(Probe, { workspace: '/scope-a' })) })
  let oldRead: Promise<PreviewRuntimePayload> | undefined
  await act(async () => { oldRead = connection!.read() })
  const oldIndex = requests.length - 1
  await act(async () => { root!.update(createElement(Probe, { workspace: '/scope-b' })) })
  await act(async () => { root!.update(createElement(Probe, { workspace: '/scope-a' })) })
  await act(async () => { answer(oldIndex); await oldRead })
  assert.equal(received.length, 0, '返回原工程也不能接收旧请求的迟到快照')
  let currentRead: Promise<PreviewRuntimePayload> | undefined
  await act(async () => { currentRead = connection!.read() })
  await act(async () => { answer(requests.length - 1); await currentRead })
  assert.ok(received.length > 0, '当前代次仍可接收结果')
  console.log('preview observation workspace ABA isolation: passed')
} finally {
  act(() => root?.unmount())
  globalThis.fetch = nativeFetch
}
