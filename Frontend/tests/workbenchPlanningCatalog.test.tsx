import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act, create } from 'react-test-renderer'
import { renderToStaticMarkup } from 'react-dom/server'
import { UiReadVisibility } from '../src/renderer/src/hooks/uiReadVisibility'
import { useWorkbenchPlanningCatalog } from '../src/renderer/src/pages/useWorkbenchPlanningCatalog'
import type { inspectWorkspacePlanningArtifacts } from '../src/renderer/src/service/applicationStorage'
import ApplicationOutline from '../src/renderer/src/components/AiChatPanel/components/ApplicationOutline'

type Inspection = Awaited<ReturnType<typeof inspectWorkspacePlanningArtifacts>>
type ReadRequest = { workspace: string; resolve: (value: Inspection) => void; reject: (reason: Error) => void }

/** 构造含页面、接口与实体的 IPC 结果，用来校验异步时序而不替代真实文件读取测试。 */
function inspection(id = 'one'): Inspection {
  return {
    ready: true, planningUnconfirmed: false, catalogIssues: [],
    missing: [], invalid: [], pageTree: [],
    pages: [{ key: id, pageId: id, label: id, path: `/${id}`, purpose: '', designed: false }],
    apiContracts: [{ id, label: id, endpoints: [{ id, path: `/${id}`, method: 'GET', summary: '' }] }],
    entities: [{ id, label: id, purpose: '', dataSourceType: '' }]
  }
}

/** 挂载真实 Hook，以可控 IPC 回执、可见性和定时器覆盖重新进入及失败竞态。 */
async function withCatalog(run: (harness: {
  current: () => ReturnType<typeof useWorkbenchPlanningCatalog>
  requests: ReadRequest[]
  render: (visible: boolean, workspace?: string, key?: string) => Promise<void>
  focus: () => Promise<void>
  advance: (ms: number) => Promise<void>
}) => Promise<void>): Promise<void> {
  const originalWindow = Object.getOwnPropertyDescriptor(globalThis, 'window')
  const originalWarn = console.warn
  const events = new EventTarget()
  let sequence = 0, now = 0
  const timers = new Map<number, { at: number; callback: () => void }>()
  const requests: ReadRequest[] = []
  /** 每个真实调用分配独立的受控回执，允许较旧请求故意迟到。 */
  const read = (workspace: string): Promise<Inspection> => new Promise((resolve, reject) => {
    requests.push({ workspace, resolve, reject })
  })
  let value!: ReturnType<typeof useWorkbenchPlanningCatalog>
  /** 用上下文模拟首页隐藏及同一工作台重新显示，不重新挂载 Hook。 */
  function Harness({ visible, workspace, refreshKey }: { visible: boolean; workspace: string; refreshKey: string }) {
    return <UiReadVisibility.Provider value={visible}><Probe workspace={workspace} refreshKey={refreshKey} /></UiReadVisibility.Provider>
  }
  /** 暴露生产 Hook 的最新返回值。 */
  function Probe({ workspace, refreshKey }: { workspace: string; refreshKey: string }): null {
    value = useWorkbenchPlanningCatalog(workspace, refreshKey, read)
    return null
  }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: {
    setTimeout: (callback: () => void, delay: number): number => {
      const id = ++sequence; timers.set(id, { at: now + delay, callback }); return id
    },
    clearTimeout: (id: number): void => { timers.delete(id) },
    addEventListener: events.addEventListener.bind(events), removeEventListener: events.removeEventListener.bind(events)
  } })
  console.warn = () => undefined
  let renderer: ReturnType<typeof create>
  try {
    await act(async () => { renderer = create(<Harness visible workspace="a" refreshKey="1" />) })
    await run({
      current: () => value, requests,
      render: async (visible, workspace = 'a', key = '1'): Promise<void> => {
        await act(async () => { renderer.update(<Harness visible={visible} workspace={workspace} refreshKey={key} />) })
      },
      focus: async (): Promise<void> => { await act(async () => { events.dispatchEvent(new Event('focus')) }) },
      advance: async (ms): Promise<void> => {
        const target = now + ms
        while (true) {
          const next = [...timers].filter(([, timer]) => timer.at <= target).sort((a, b) => a[1].at - b[1].at)[0]
          if (!next) break
          now = next[1].at; timers.delete(next[0])
          await act(async () => { next[1].callback() })
        }
        now = target
      }
    })
  } finally {
    await act(async () => { renderer!.unmount() })
    console.warn = originalWarn
    if (originalWindow) Object.defineProperty(globalThis, 'window', originalWindow)
    else Reflect.deleteProperty(globalThis, 'window')
  }
}

test('首页返回同一工作台必须重新读取，旧回执不覆盖新数据', async () => {
  await withCatalog(async ({ current, requests, render }) => {
    assert.equal(requests.length, 1)
    assert.equal(current().status.productAvailable, false)
    await act(async () => { requests[0].resolve(inspection('old')) })
    await render(false)
    await render(true)
    assert.equal(requests.length, 2)
    assert.equal(current().status.loading, true)
    await act(async () => { requests[1].resolve(inspection('new')) })
    assert.equal(current().inspection?.pages[0].pageId, 'new')
  })
})

test('窗口重新聚焦不触发目录读取，手动刷新才发起新请求', async () => {
  await withCatalog(async ({ current, requests, focus }) => {
    await focus()
    assert.equal(requests.length, 1)
    await act(async () => { requests[0].resolve(inspection()) })
    await focus()
    assert.equal(requests.length, 1)
    await act(async () => { current().reload() })
    assert.equal(requests.length, 2)
    assert.equal(current().status.loading, true)
  })
})

test('手动并发刷新按代次提交，切换工作区不显示前一工作区目录', async () => {
  await withCatalog(async ({ current, requests, render }) => {
    await act(async () => { current().reload() })
    await act(async () => { requests[1].resolve(inspection('new')); requests[0].resolve(inspection('late')) })
    assert.equal(current().inspection?.entities[0].id, 'new')
    await render(true, 'b')
    assert.equal(requests[2].workspace, 'b')
    assert.equal(current().inspection, undefined)
    assert.equal(current().status.technicalAvailable, false)
    await act(async () => { requests[2].resolve(inspection('b')) })
    assert.equal(current().inspection?.entities[0].id, 'b')
  })
})

test('刷新 IPC 连续失败保留已有目录，显示错误并可显式重读', async () => {
  await withCatalog(async ({ current, requests, advance }) => {
    await act(async () => { requests[0].resolve(inspection('saved')) })
    await act(async () => { current().reload() })
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await act(async () => { requests[attempt + 1].reject(new Error('IPC failed')) })
      if (attempt < 2) await advance([300, 900][attempt])
    }
    assert.equal(current().status.loading, false)
    assert.match(current().status.error || '', /读取失败/)
    assert.equal(current().inspection?.apiContracts[0].id, 'saved')
    await act(async () => { current().reload() })
    assert.equal(requests.length, 5)
    await act(async () => { requests[4].resolve(inspection('recovered')) })
    assert.equal(current().status.error, undefined)
    assert.equal(current().inspection?.apiContracts[0].id, 'recovered')
  })
})

test('源文件异常保留该源已有内容，另一个源的合法空数组正常替换', async () => {
  await withCatalog(async ({ current, requests, advance }) => {
    await act(async () => { requests[0].resolve(inspection()) })
    await act(async () => { current().reload() })
    const partial = { ...inspection(), pages: [], apiContracts: [], entities: [], ready: false,
      catalogIssues: [{ source: 'technical' as const, reason: 'invalid' as const }] }
    for (let attempt = 0; attempt < 3; attempt += 1) {
      await act(async () => { requests[attempt + 1].resolve(partial) })
      if (attempt < 2) await advance([300, 900][attempt])
    }
    assert.deepEqual(current().inspection?.pages, [])
    assert.equal(current().inspection?.apiContracts.length, 1)
    assert.equal(current().inspection?.entities.length, 1)
    assert.equal(current().status.issues[0].reason, 'invalid')
    await act(async () => { current().reload() })
    await act(async () => { requests[4].resolve({ ...inspection(), pages: [], apiContracts: [], entities: [] }) })
    assert.deepEqual(current().inspection?.entities, [])
    assert.equal(current().status.technicalAvailable, true)
    assert.deepEqual(current().status.issues, [])
  })
})

test('读取超时可结束等待且迟到回执不能伪装恢复，新迭代重置也隔离旧读取', async () => {
  await withCatalog(async ({ current, requests, advance }) => {
    await advance(46_200)
    assert.equal(requests.length, 3)
    assert.equal(current().settled, true)
    assert.match(current().status.error || '', /读取失败/)
    await act(async () => { requests[0].resolve(inspection('timed-out')) })
    assert.equal(current().inspection, undefined)
    await act(async () => { current().reset() })
    assert.equal(current().settled, false)
    await act(async () => { requests[3].resolve(inspection('iteration')) })
    assert.equal(current().inspection?.pages[0].pageId, 'iteration')
  })
})

test('目录没有成功读取时显示加载或失败，不显示虚假的暂无和零计数', () => {
  const html = renderToStaticMarkup(<ApplicationOutline pages={[]} pageTree={[]} apiContracts={[]} entities={[]}
    outlineLocked={false} selectedPageId="" selectedEntityId="" selectedApiEndpointKey=""
    onPageSelect={() => undefined} onEntitySelect={() => undefined} onApiEndpointSelect={() => undefined}
    planningCatalogStatus={{ loading: false, productAvailable: false, technicalAvailable: false,
      issues: [{ source: 'technical', reason: 'missing' }] }} onPlanningCatalogRetry={() => undefined} />)
  assert.match(html, /接口与实体：文件未读到/)
  assert.match(html, /重新读取/)
  assert.match(html, /aria-label="刷新应用产物"/)
  assert.doesNotMatch(html, /暂无接口|暂无实体|暂无页面/)
})
