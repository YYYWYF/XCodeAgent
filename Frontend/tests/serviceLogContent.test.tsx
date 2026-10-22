import assert from 'node:assert/strict'
import { test } from 'node:test'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import ServiceLogContent from '../src/renderer/src/components/BrowserPreviewPanel/ServiceLogContent'
import { splitServiceLogMatches } from '../src/renderer/src/components/BrowserPreviewPanel/serviceLogSearch'
import { useServiceLogClipboard } from '../src/renderer/src/components/BrowserPreviewPanel/useServiceLogClipboard'

test('搜索全部字面量匹配，忽略大小写且保留原始日志及换行', () => {
  const content = 'Error [a.*]\nerror [A.*]\n中文日志 <script>alert(1)</script>'
  for (const [query, expected] of [
    ['error', ['Error', 'error']],
    ['[a.*]', ['[a.*]', '[A.*]']],
    ['中文', ['中文']],
    ['missing', []],
    ['', []]
  ] as const) {
    const segments = splitServiceLogMatches(content, query)
    assert.deepEqual(
      segments.filter((segment) => segment.matched).map((segment) => segment.text),
      expected
    )
    assert.equal(segments.map((segment) => segment.text).join(''), content)
  }
  assert.equal(splitServiceLogMatches('aaaa', 'aa').filter((segment) => segment.matched).length, 2)
  assert.deepEqual(splitServiceLogMatches('', 'error'), [])
})

test('日志框提供搜索输入和放大镜，原始日志作为安全文本显示', () => {
  const html = renderToStaticMarkup(
    <ServiceLogContent
      content={'Error\n<script>alert(1)</script>'}
      label="前端启动日志"
      following
      expanded={false}
      onPauseFollowing={() => {}}
      onCopy={async () => {}}
    />
  )
  assert.match(html, /aria-label="搜索启动日志"/)
  assert.match(html, /anticon-search/)
  assert.match(html, /&lt;script&gt;alert\(1\)&lt;\/script&gt;/)
  assert.ok(!html.includes('<mark'))
  const empty = renderToStaticMarkup(
    <ServiceLogContent
      content=""
      label="后端启动日志"
      following
      expanded={false}
      onPauseFollowing={() => {}}
      onCopy={async () => {}}
    />
  )
  assert.match(empty, /本次启动暂无日志/)
  assert.match(empty, /disabled/)
})

test('复制使用原生桥接，成功、权限错误和缺失桥接均有真实状态反馈', async () => {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const writes: string[] = []
  let fail = false
  const nativeClipboard = {
    /** 记录传给原生剪贴板的完整文本，并模拟系统写入失败。 */
    writeText: async (text: string): Promise<void> => {
      if (fail) throw new Error('Clipboard unavailable')
      writes.push(text)
    }
  }
  const fakeWindow = {
    devAgentStudio: { clipboard: nativeClipboard as typeof nativeClipboard | undefined },
    setTimeout,
    clearTimeout
  }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: fakeWindow })
  let state: ReturnType<typeof useServiceLogClipboard> | undefined
  let renderer: ReactTestRenderer | undefined
  /** 在真实 React effect 中捕获复制状态，不替换生产 Hook。 */
  function Probe(): null {
    state = useServiceLogClipboard()
    return null
  }
  try {
    await act(async () => {
      renderer = create(<Probe />)
    })
    const logs = 'Error\n完整日志与 <mark>原文</mark>'
    await act(async () => {
      await state!.copyLogs(logs)
    })
    assert.deepEqual(writes, [logs])
    assert.equal(state!.copied, true)
    assert.equal(state!.copyError, '')
    fail = true
    await act(async () => {
      await state!.copyLogs('selected text')
    })
    assert.equal(state!.copied, false)
    assert.match(state!.copyError, /复制失败/)
    fail = false
    await act(async () => {
      await state!.copyLogs('selected text')
    })
    assert.deepEqual(writes, [logs, 'selected text'])
    assert.equal(state!.copyError, '')
    fakeWindow.devAgentStudio.clipboard = undefined
    await act(async () => {
      await state!.copyLogs(logs)
    })
    assert.equal(state!.copied, false)
    assert.match(state!.copyError, /复制失败/)
  } finally {
    await act(async () => renderer?.unmount())
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})
