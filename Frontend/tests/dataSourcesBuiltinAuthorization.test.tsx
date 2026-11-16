import assert from 'node:assert/strict'
import { test } from 'node:test'
import React, { createElement, type ReactElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { renderToStaticMarkup } from 'react-dom/server'
import DataSourcesDrawer from '../src/renderer/src/components/DataSourcesPage/DataSourcesDrawer'
import BuiltinPatchCard, { useBuiltinPatchCard } from '../src/renderer/src/components/AiChatPanel/components/WorkspaceDebugDock/BuiltinPatchCard'
import { BuiltinAuthorizationTaskCard, BuiltinAuthorizationWorkflowPanel } from '../src/renderer/src/components/BuiltinAuthorization'
import { installRendererDom } from './rendererDomHarness'
import { authorizationTaskWorkflowResult, type BuiltinPatchResult } from '../src/renderer/src/service/builtinAuthorization'

const running: BuiltinPatchResult = { stage: 'seed', status: 'running', schemaReady: true, seedReady: false, message: '等待写入核验', schemaOperationId: '123', seedOperationId: '456' }
const ready: BuiltinPatchResult = { ...running, stage: 'done', status: 'ready', seedReady: true, message: '回读完成', resourceCount: 2, roleGrantCount: 3 }
const failed: BuiltinPatchResult = { ...running, status: 'needs_attention', message: '请核对数据操作', failureCategory: 'seed_invalid_response' }

/** 以真实 Debug Hook 监听空态，同工作区任务启动后应主动出现。 */
function Debug({ workspace }: { workspace: string }): ReactElement | null {
  const { card } = useBuiltinPatchCard(workspace, true)
  return card ? <BuiltinPatchCard card={card} disabled={false} /> : null
}

/** 等待 AG-UI SDK 与 React 完成已到达的响应。 */
function flush(): Promise<void> {
  return new Promise((resolve) => setImmediate(resolve))
}

/** 生成符合现行 AG-UI 生命周期的测试替身。 */
function stream(payload: { runId: string; threadId: string }, name: string, value: object, progress?: object): Response {
  return new Response([
    { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
    ...(progress ? [{ type: 'CUSTOM', name, value: progress }] : []),
    { type: 'CUSTOM', name, value },
    { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId }
  ].map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
}

test('真实抽屉仅在内置数据源与权限启用时调度一次，回读同步 Debug 且重开显示真实完成', async () => {
  const restoreDom = installRendererDom()
  const originalFetch = globalThis.fetch
  const configs: string[] = []
  const requests: { workspaceRoot: string; action: string }[] = []
  const existing = new Map<string, BuiltinPatchResult>()
  let releasePatch: (() => void) | undefined
  let renderer: ReactTestRenderer | undefined
  Object.assign(window, { devAgentStudio: {
    agentBaseUrl: 'http://authorization.test', workspace: {
      /** 权限配置来自工作区正式文件；用不同根目录覆盖关闭和读取失败。 */
      async readApplication({ workspaceRoot }: { workspaceRoot: string }) {
        configs.push(workspaceRoot)
        if (workspaceRoot === '/error') throw new Error('读取失败')
        return { application: { authorization: { enabled: workspaceRoot !== '/disabled' } } }
      }
    }
  } })
  globalThis.fetch = async (url, init) => {
    const payload = JSON.parse(String(init?.body))
    const input = payload.forwardedProps.builtinAuthorization
    if (!input) {
      const workspace = payload.forwardedProps.dataSources.workspaceRoot
      const sources = [{ type: 'database', id: 'builtin-id', name: '数据库',
        mode: workspace === '/direct' ? 'direct' : 'builtin', hasPassword: false, managedTables: [] }]
      return stream(payload, 'data-sources', { schemaVersion: 1, runId: payload.runId,
        threadId: payload.threadId, status: 'completed',
        ...(String(url).endsWith('/list') ? { catalog: { sources } } : { tables: [] }) })
    }
    requests.push(input)
    if (input.action === 'run_patch') {
      await new Promise<void>((resolve) => { releasePatch = resolve })
      existing.set(input.workspaceRoot, running)
      // 即便回执包含 ready，也只允许后续 get 决定卡片显示。
      return stream(payload, 'builtin-authorization', { status: 'completed', patch: ready },
        { status: 'in_progress', progress: { stage: 'schema', message: '正在接受任务' } })
    }
    assert.equal(input.action, 'get')
    return stream(payload, 'builtin-authorization', { status: 'completed', patch: existing.get(input.workspaceRoot) || null })
  }
  /** 隔离页面导航，保留实际抽屉、配置读取和 AG-UI 读取。 */
  function drawer(workspace: string, mode: 'database' | 'external_api' = 'database') {
    return <DataSourcesDrawer key={`${workspace}:${mode}`} workspaceRoot={workspace} mode={mode} theme="dark"
      onClose={() => {}} onNavigationGuard={() => {}} />
  }
  /** 推进渲染、目录加载和配置/任务两个独立请求。 */
  async function show(content: ReactElement): Promise<void> {
    await act(async () => {
      if (renderer) renderer.update(content)
      else renderer = create(content)
      for (let index = 0; index < 4; index += 1) await flush()
    })
  }
  /** 查找真实 DOM 按钮及委托的点击。 */
  function buttons(label: string) {
    return renderer!.root.findAllByType('button').filter((button) =>
      button.findAllByType('span').some((span) => span.children.includes(label)))
  }

  try {
    await show(drawer('/direct'))
    await show(drawer('/enabled', 'external_api'))
    await show(drawer('/disabled'))
    assert.deepEqual(configs, ['/disabled'])
    assert.deepEqual(requests, [])
    assert.equal(buttons('开始初始化').length, 0)
    await show(drawer('/error'))
    assert.equal(buttons('重新读取配置').length, 1)
    assert.deepEqual(requests, [])

    await show(<><Debug workspace="/enabled" /><Debug workspace="/other" />{drawer('/enabled')}</>)
    const start = buttons('开始初始化')
    assert.equal(start.length, 1)
    assert.equal(renderer!.root.findAllByProps({ 'aria-label': '权限初始化' }).length, 1)
    assert.deepEqual(requests.filter((request) => request.workspaceRoot === '/other'), [{ workspaceRoot: '/other', action: 'get' }])
    await act(async () => {
      start[0].props.onClick()
      start[0].props.onClick()
      await flush()
    })
    const submission = { workspaceRoot: '/enabled', action: 'run_patch' }
    assert.deepEqual(requests.filter((request) => request.action !== 'get'), [submission])

    // 离开抽屉不撤销请求，仍在同一工作区的 Debug 接到通知后重新读取。
    await show(<><Debug workspace="/enabled" /><Debug workspace="/other" />{drawer('/direct')}</>)
    await act(async () => { releasePatch!(); await flush() })
    assert.equal(renderer!.root.findByType(BuiltinAuthorizationTaskCard).props.task.status, 'running')
    assert.equal(requests.filter((request) => request.workspaceRoot === '/other').length, 1)
    await show(<><Debug workspace="/enabled" /><Debug workspace="/other" />{drawer('/enabled')}</>)
    const cards = renderer!.root.findAllByType(BuiltinAuthorizationTaskCard)
    assert.equal(cards.length, 2)
    for (const card of cards) assert.equal(card.props.task.status, 'running')
    assert.equal(buttons('开始初始化').length, 0)
    assert.equal(requests.filter((request) => request.workspaceRoot === '/other').length, 1)

    existing.set('/enabled', ready)
    await show(<>{drawer('/direct')}</>)
    await show(<>
      <Debug workspace="/enabled" />
      <BuiltinAuthorizationWorkflowPanel workspace="/enabled" result={authorizationTaskWorkflowResult(running)} />
      {drawer('/enabled')}
    </>)
    const readyCards = renderer!.root.findAllByType(BuiltinAuthorizationTaskCard)
    assert.equal(readyCards.length, 3)
    for (const card of readyCards) {
      assert.equal(card.props.task.seedReady, true)
      assert.equal(card.props.task.resourceCount, ready.resourceCount)
      assert.equal(card.props.task.seedOperationId, ready.seedOperationId)
    }
    assert.equal(buttons('开始初始化').length, 0)
    assert.deepEqual(requests.filter((request) => request.action !== 'get'), [submission])
  } finally {
    await act(async () => { renderer?.unmount(); await flush() })
    globalThis.fetch = originalFetch
    restoreDom()
  }
})

test('共享卡与 Build、Debug 保留阶段、操作号、失败详情和已完成计数', () => {
  for (const task of [failed, ready]) {
    const output = [
      createElement(BuiltinAuthorizationTaskCard, { task, variant: 'compact' }),
      createElement(BuiltinAuthorizationWorkflowPanel, { result: authorizationTaskWorkflowResult(task) }),
      createElement(BuiltinPatchCard, { card: { result: task }, disabled: false })
    ].map(renderToStaticMarkup)
    for (const html of output) {
      for (const expected of ['权限数据表初始化', '权限数据初始化', '初始化结果核验', '123', '456']) assert.ok(html.includes(expected))
      if (task === failed) {
        assert.match(html, /请核对数据操作/)
        assert.match(html, /seed_invalid_response/)
      } else {
        assert.match(html, /已完成/)
      }
    }
    // 当前工作区的 Build 由同一 GET 补齐计数；历史投影继续遵循已有字段白名单。
    for (const html of [output[0], output[2]]) if (task === ready) assert.match(html, /资源点 2 条，角色授权 3 条/)
  }
})
