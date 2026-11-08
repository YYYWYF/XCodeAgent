import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import PageArtifactTasks from '../src/renderer/src/components/AiChatPanel/components/DevelopmentArtifactsPanel/PageArtifactTasks'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { usePageArtifactOverview } from '../src/renderer/src/components/AiChatPanel/hooks/usePageArtifactOverview'
import {
  readPageArtifactOverview,
  type PageArtifactOverview
} from '../src/renderer/src/service/pageArtifacts'

/** 构造只包含测试身份的概览，不提供任何模拟开发任务。 */
function overview(id: string): PageArtifactOverview {
  return {
    page: {
      id,
      name: id,
      path: `/${id}`,
      description: '',
      confirmationStatus: 'confirmed'
    },
    tasks: [],
    endpoints: [],
    files: [],
    taskPlanAvailable: false,
    issues: {},
    progress: null
  }
}

/** 用标准 AG-UI 事件测试真实客户端，产品代码不自行解析 SSE。 */
function response(threadId: string, runId: string, id: string, failed = false): Response {
  const payload = failed
    ? { status: 'failed', error: { message: '真实来源不可读' } }
    : { status: 'completed', overview: overview(id) }
  const events = [
    { type: 'RUN_STARTED', threadId, runId },
    { type: 'STATE_SNAPSHOT', snapshot: { pageArtifact: payload } },
    { type: 'RUN_FINISHED', threadId, runId, result: { pageArtifact: payload } }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

test('任务来源失败不显示完成比率，已满足和跳过保持不同语义', () => {
  const data = overview('notice')
  data.issues.tasks = '正式开发计划校验失败'
  let html = renderToStaticMarkup(createElement(PageArtifactTasks, { overview: data, onReload: () => undefined }))
  assert.match(html, /正式开发计划校验失败/)
  assert.doesNotMatch(html, /暂无|0 \/ 0|ant-progress/)
  data.issues = {}
  data.tasks = ['already_satisfied', 'skipped'].map((status) => ({
    id: status, title: '任务-' + status, description: '来源原文-' + status,
    owner: 'frontend', unitId: 'notice', scope: 'page', status,
    failureReason: '', updatedAt: '', dependencies: []
  }))
  html = renderToStaticMarkup(createElement(PageArtifactTasks, { overview: data, onReload: () => undefined }))
  assert.match(html, /1 \/ 2/)
  assert.match(html, /已满足/)
  assert.match(html, /已跳过/)
  assert.match(html, /来源原文-skipped/)
  assert.match(html, /任务完成情况与页面开发状态分别记录/)
})

test('页面概览使用独立 AG-UI 身份，失败不伪装成没有任务', async () => {
  const fetch = globalThis.fetch
  const window = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {} })
  const requests: Array<Record<string, any>> = []
  try {
    globalThis.fetch = async (url, init) => {
      assert.match(String(url), /\/page-artifacts\/run$/)
      const request = JSON.parse(String(init?.body))
      requests.push(request)
      return response(
        request.threadId,
        request.runId,
        request.forwardedProps.pageArtifact.pageId,
        requests.length === 2
      )
    }
    assert.equal((await readPageArtifactOverview('/one', 'orders')).page.id, 'orders')
    await assert.rejects(readPageArtifactOverview('/one', 'orders'), /真实来源不可读/)
    assert.notEqual(requests[0].threadId, requests[1].threadId)
    assert.deepEqual(requests[0].forwardedProps, {
      pageArtifact: { action: 'get', workspaceRoot: '/one', pageId: 'orders' }
    })
  } finally {
    globalThis.fetch = fetch
    if (window) Object.defineProperty(globalThis, 'window', window)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})

test('切换工作区或页面后不显示旧事实，迟到回执和卸载不会回写', async () => {
  const fetch = globalThis.fetch
  const window = Object.getOwnPropertyDescriptor(globalThis, 'window')
  Object.defineProperty(globalThis, 'window', {
    configurable: true,
    value: { setTimeout, clearTimeout }
  })
  const requests: Array<{ id: string; release: () => void }> = []
  let mounted: ReactTestRenderer | undefined
  let current!: ReturnType<typeof usePageArtifactOverview>
  /** 挂载生产 Hook，保留真实取消、错误和身份隔离逻辑。 */
  function Probe({
    workspace,
    page,
    refresh = ''
  }: {
    workspace: string
    page: string
    refresh?: string
  }): null {
    current = usePageArtifactOverview(workspace, page, refresh)
    return null
  }
  try {
    globalThis.fetch = async (_url, init) => {
      const request = JSON.parse(String(init?.body))
      const id = request.forwardedProps.pageArtifact.pageId
      return new Promise<Response>((resolve) => {
        requests.push({ id, release: () => resolve(response(request.threadId, request.runId, id)) })
      })
    }
    await act(async () => {
      mounted = create(createElement(Probe, { workspace: '/one', page: 'first' }))
    })
    await act(async () => {
      mounted!.update(createElement(Probe, { workspace: '/two', page: 'second' }))
    })
    assert.equal(current.loading, true)
    assert.equal(current.overview, undefined)
    await act(async () => requests[1].release())
    assert.equal(current.overview?.page.id, 'second')
    await act(async () => requests[0].release())
    assert.equal(current.overview?.page.id, 'second')
    await act(async () => current.reload())
    assert.equal(current.overview, undefined)
    await act(async () => requests[2].release())
    assert.equal(current.overview?.page.id, 'second')
    await act(async () => {
      mounted!.update(
        createElement(Probe, { workspace: '/two', page: 'second', refresh: 'new-task-state' })
      )
    })
    assert.equal(current.loading, true)
    await act(async () => mounted!.unmount())
    await act(async () => requests[3].release())
    assert.equal(requests.length, 4)
  } finally {
    await act(async () => mounted?.unmount())
    globalThis.fetch = fetch
    if (window) Object.defineProperty(globalThis, 'window', window)
    else Reflect.deleteProperty(globalThis, 'window')
  }
})
