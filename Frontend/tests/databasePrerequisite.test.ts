import assert from 'node:assert/strict'
import { test } from 'node:test'
import { setImmediate } from 'node:timers/promises'
import { createElement } from 'react'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import { resolveDatabaseGuideEntities, resolveDatabasePrerequisite } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/databasePrerequisite'
import { useBindingWorkspace } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useBindingWorkspace'
import { notifyDataSourcesChanged } from '../src/renderer/src/service/dataSourceEvents'
import type { DatabaseDataSource, DevelopmentPlanningEntityOption } from '../src/renderer/src/typings'

const base = { kind: 'database', catalog: { sources: [] }, tables: [], loading: false, error: '' }
const table = { sourceId: 'db', schema: '', table: 'products', description: '' }

/** 多关联不能默认首个，缺失/过期关联不能挪用其他实体。 */
test('实体导航只接受当前 Contract 的有效关联与明确选择', () => {
  const entities = [{ id: 'one', label: '商品' }, { id: 'two', label: '订单' }] as DevelopmentPlanningEntityOption[]
  assert.equal(resolveDatabaseGuideEntities(['one'], entities).targetEntityId, 'one')
  assert.equal(resolveDatabaseGuideEntities(['one', 'two'], entities).targetEntityId, undefined)
  assert.equal(resolveDatabaseGuideEntities(['one', 'two'], entities, 'two').targetEntityId, 'two')
  assert.deepEqual(resolveDatabaseGuideEntities(['deleted'], entities, 'one').relatedEntities, [])
  assert.equal(resolveDatabaseGuideEntities(undefined, entities, 'one').targetEntityId, undefined)
})

/** 构造脱敏数据库目录，模式与表清单分别参与判断。 */
function source(mode: DatabaseDataSource['mode']): DatabaseDataSource {
  return { id: 'db', name: '数据库', type: 'database', mode, hasPassword: false, managedTables: [] }
}

/** 加载、失败与模式冲突优先于空状态，不能以表数量推断内置或外部。 */
test('数据库前置判断隔离加载、异常、未知模式及其他来源类型', () => {
  for (const kind of ['external_api', 'static', undefined]) {
    assert.equal(resolveDatabasePrerequisite({ ...base, kind, loading: true, error: '失败' }), 'hidden')
  }
  assert.equal(resolveDatabasePrerequisite({ ...base, mode: 'direct', loading: true }), 'loading')
  assert.equal(resolveDatabasePrerequisite({ ...base, mode: 'builtin', error: '查询失败' }), 'load_error')
  assert.equal(resolveDatabasePrerequisite(base), 'load_error')
  assert.equal(resolveDatabasePrerequisite({ ...base, mode: 'builtin', catalog: { sources: [source('direct')] } }), 'load_error')
  assert.equal(resolveDatabasePrerequisite({ ...base, mode: 'external', catalog: { sources: [source('builtin')] } }), 'load_error')
  assert.equal(resolveDatabasePrerequisite({ ...base, catalog: { sources: [source('dbid'), { ...source('direct'), id: 'other' }] } }), 'load_error')
})

/** 三类导航互斥，只有当前数据库的真实已添加表才允许映射。 */
test('内置仅引导实体建表，外部按连接和添加表依次引导', () => {
  assert.equal(resolveDatabasePrerequisite({ ...base, mode: 'builtin' }), 'builtin_create_table')
  for (const mode of ['builtin', 'direct', 'dbid'] as const) {
    const catalog = { sources: [source(mode)] }
    assert.equal(resolveDatabasePrerequisite({ ...base, mode, catalog }), mode === 'builtin' ? 'builtin_create_table' : 'external_add_table')
    assert.equal(resolveDatabasePrerequisite({ ...base, mode, catalog, tables: [table] }), 'ready')
    assert.notEqual(resolveDatabasePrerequisite({ ...base, mode, catalog, tables: [{ ...table, sourceId: 'other' }] }), 'ready')
    if (mode !== 'builtin') assert.equal(resolveDatabasePrerequisite({ ...base, mode }), 'external_config_connection')
  }
  assert.equal(resolveDatabasePrerequisite({ ...base, mode: 'external' }), 'external_config_connection')
})

/** 驱动真实 Hook 和 AG-UI 客户端，验证返回刷新不会替换 Endpoint 的未保存草稿。 */
test('映射候选加载失败可恢复，事件与返回刷新保留草稿和接口定位', async () => {
  const savedFetch = globalThis.fetch
  const savedWindow = globalThis.window
  Object.assign(globalThis, { window: Object.assign(new EventTarget(), { devAgentStudio: { agentBaseUrl: 'http://agent.test' } }) })
  let root: ReactTestRenderer | undefined
  let state!: ReturnType<typeof useBindingWorkspace>
  let selected: typeof table[] = []
  let failed = false
  const actions: string[] = []
  globalThis.fetch = async (input, init) => {
    const body = JSON.parse(String(init?.body))
    const endpoint = body.forwardedProps.endpointDesigns
    const action = endpoint?.action || String(input).split('/').at(-1)
    actions.push(action)
    if (failed && action === 'selected-tables') throw new TypeError('表目录读取失败')
    const value = { schemaVersion: 1, threadId: body.threadId, runId: body.runId, status: 'completed',
      catalog: { sources: [source('builtin')] }, tables: selected,
      preparation: { apiContractId: 'api', endpointId: 'one', technicalPlanHash: 'h1', payload: { endpoint: { id: 'one', method: 'GET' }, endpointFields: [] } } }
    const name = endpoint ? 'endpoint-designs' : 'data-sources'
    const key = endpoint ? 'endpointDesigns' : 'dataSources'
    const events = [
      { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
      { type: 'CUSTOM', name, value },
      { type: 'STATE_SNAPSHOT', snapshot: { [key]: value } },
      { type: 'RUN_FINISHED', threadId: body.threadId, runId: body.runId }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
  }
  /** 始终保留同一个 Hook 实例，模拟右侧映射面板隐藏而不卸载。 */
  function Probe({ workspaceRoot = '/workspace' }: { workspaceRoot?: string }): null {
    state = useBindingWorkspace(workspaceRoot, { apiContractId: 'api', endpointId: 'one' }, () => undefined)
    return null
  }
  /** 等待标准 AG-UI 流完成，避免用固定延时掩盖状态竞争。 */
  async function settle(predicate: () => boolean): Promise<void> {
    for (let count = 0; count < 100 && !predicate(); count++) await act(async () => { await setImmediate() })
    assert.ok(predicate())
  }
  try {
    act(() => { root = create(createElement(Probe)) })
    assert.equal(state.sourcesLoading, true)
    assert.equal(state.sourcesRefreshing, true)
    await settle(() => Boolean(state.entry) && !state.sourcesRefreshing)
    await act(async () => { state.edit({ ...state.entry!.value.draft, implementationDescription: '未保存的映射规则' }) })
    const draft = state.entry!.value
    const key = state.key
    failed = true
    act(() => { state.refreshSourceCandidates() })
    assert.equal(state.sourcesRefreshing, true)
    assert.equal(state.sourcesLoading, false)
    await settle(() => !state.sourcesRefreshing)
    assert.match(state.sourcesError, /表目录读取失败/)
    assert.equal(state.entry!.value, draft)
    failed = false
    selected = [table]
    act(() => { notifyDataSourcesChanged('/workspace') })
    // 旧错误必须保留至新快照成功，不能点击重试就把失败候选重新当作可信。
    assert.match(state.sourcesError, /表目录读取失败/)
    await settle(() => !state.sourcesRefreshing && state.tables.length === 1)
    assert.equal(state.sourcesError, '')
    act(() => { state.refreshSourceCandidates() })
    assert.equal(state.sourcesRefreshing, true)
    assert.equal(state.sourcesLoading, false)
    // 这正是 Select 展开时使用的门禁：刷新前后保持 ready，不能触发 disabled 关闭下拉。
    assert.equal(resolveDatabasePrerequisite({ kind: 'database', mode: 'builtin', catalog: state.catalog, tables: state.tables, loading: state.sourcesLoading, error: state.sourcesError }), 'ready')
    assert.deepEqual(state.tables, [table])
    await settle(() => !state.sourcesRefreshing)
    assert.equal(state.entry!.value, draft)
    assert.equal(state.entry!.dirty, true)
    assert.equal(state.key, key)
    assert.ok(actions.every((action) => ['prepare', 'list', 'selected-tables'].includes(action)))
    assert.equal(actions.filter((action) => action === 'prepare').length, 1)
    const reads = actions.length
    notifyDataSourcesChanged('/other-workspace')
    await act(async () => { await setImmediate() })
    assert.equal(actions.length, reads)
    act(() => { root!.update(createElement(Probe, { workspaceRoot: '/other-workspace' })) })
    assert.equal(state.sourcesLoading, true)
    assert.equal(state.sourcesRefreshing, true)
    assert.deepEqual(state.tables, [])
    await settle(() => !state.sourcesRefreshing)
  } finally {
    await act(async () => { root?.unmount() })
    globalThis.fetch = savedFetch
    Object.assign(globalThis, { window: savedWindow })
  }
})
