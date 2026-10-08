import assert from 'node:assert/strict'
import { setImmediate } from 'node:timers/promises'
import { test } from 'node:test'
import { observeBindingSources } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/bindingSources'
import { tableIsSelected } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/model'
import {
  changeSelectedTables,
  createDataSource,
  deleteDataSource,
  updateDataSource,
  type SelectedDataTable
} from '../src/renderer/src/service/dataSources'
import {
  notifyDataSourcesChanged,
  subscribeDataSourcesChanged
} from '../src/renderer/src/service/dataSourceEvents'
import type { DataSourceCatalog } from '../src/renderer/src/typings'

type Snapshot = { catalog: DataSourceCatalog; tables: SelectedDataTable[] }
type TestServer = {
  tables: SelectedDataTable[]
  actions: string[]
  failure?: 'business' | 'transport' | 'missing'
  deferReads: boolean
  pending: Array<() => void>
}

/** 等待真实 AG-UI 异步流落地，超出有限事件循环轮次即报告未完成。 */
async function until(predicate: () => boolean): Promise<void> {
  for (let count = 0; count < 100; count += 1) {
    if (predicate()) return
    await setImmediate()
  }
  assert.ok(predicate(), 'AG-UI 流未在预期事件循环内完成')
}

/** 返回完整 AG-UI 回执，测试复用真实客户端而不替换其解析逻辑。 */
function response(threadId: string, runId: string, value: Record<string, unknown>): Response {
  const events = [
    { type: 'RUN_STARTED', threadId, runId },
    { type: 'CUSTOM', name: 'data-sources', value },
    { type: 'STATE_SNAPSHOT', snapshot: { dataSources: value } },
    { type: 'RUN_FINISHED', threadId, runId, result: { dataSources: value } }
  ]
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
    headers: { 'Content-Type': 'text/event-stream' }
  })
}

/** 隔离窗口事件和网络，模拟已保存表清单并允许控制迟到的读取结果。 */
async function withServer(operation: (server: TestServer) => Promise<void>): Promise<void> {
  const savedWindow = globalThis.window
  const savedFetch = globalThis.fetch
  const server: TestServer = { tables: [], actions: [], deferReads: false, pending: [] }
  const events = Object.assign(new EventTarget(), {
    devAgentStudio: { agentBaseUrl: 'http://agent.test' }
  })
  Object.defineProperty(globalThis, 'window', { configurable: true, value: events })
  globalThis.fetch = async (input, init) => {
    const action = String(input).split('/').at(-1) || ''
    server.actions.push(action)
    const request: {
      threadId: string
      runId: string
      forwardedProps: { dataSources: { tables?: string[] } }
    } = JSON.parse(String(init?.body))
    const isRead = action === 'list' || action === 'selected-tables'
    if (!isRead && server.failure === 'transport') throw new TypeError('Failed to fetch')
    const failed = !isRead && server.failure === 'business'
    if (!isRead && !failed && !server.failure) {
      const names: string[] = request.forwardedProps.dataSources.tables || []
      if (action === 'add-tables')
        server.tables = names.map((table) => ({
          sourceId: 'db',
          schema: 'app',
          table,
          description: ''
        }))
      if (action === 'remove-tables')
        server.tables = server.tables.filter((table) => !names.includes(table.table))
    }
    const value = {
      schemaVersion: 1,
      threadId: request.threadId,
      runId: request.runId,
      status: failed ? 'failed' : 'completed',
      error: failed ? { message: '保存失败' } : undefined,
      catalog: { sources: [] },
      tables: server.failure === 'missing' && !isRead ? undefined : [...server.tables]
    }
    if (isRead && server.deferReads)
      return new Promise<Response>((resolve) => {
        server.pending.push(() => resolve(response(request.threadId, request.runId, value)))
      })
    return response(request.threadId, request.runId, value)
  }
  try {
    await operation(server)
  } finally {
    globalThis.fetch = savedFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: savedWindow })
  }
}

/** 左侧增删后的通知应刷新右侧候选，只读取清单、不重载草稿或提交确认。 */
test('表清单增删自动刷新绑定候选，并隔离其他工作区和卸载后的监听', async () => {
  await withServer(async (server) => {
    const snapshots: Snapshot[] = []
    const errors: unknown[] = []
    const reader = observeBindingSources(
      '/workspace',
      (snapshot) => snapshots.push(snapshot),
      (reason) => errors.push(reason)
    )
    try {
      await until(() => snapshots.length === 1)
      assert.deepEqual(snapshots[0].tables, [])
      await changeSelectedTables('/workspace', 'db', ['age_record', 'person'])
      await until(() => snapshots.length === 2)
      assert.deepEqual(
        snapshots[1].tables.map((table) => table.table),
        ['age_record', 'person']
      )
      const selection = {
        sourceType: 'database' as const,
        sourceId: 'db',
        schema: 'app',
        table: 'person'
      }
      assert.equal(tableIsSelected(selection, snapshots[1].tables), true)
      await changeSelectedTables('/workspace', 'db', ['person'], true)
      await until(() => snapshots.length === 3)
      assert.equal(tableIsSelected(selection, snapshots[2].tables), false)
      assert.equal(selection.table, 'person') // 目录刷新只提示失效，不能自动清除接口选择。
      const reads = server.actions.length
      notifyDataSourcesChanged('/another-workspace')
      await setImmediate()
      assert.equal(server.actions.length, reads)
      reader.dispose()
      reader.refresh()
      notifyDataSourcesChanged('/workspace')
      await setImmediate()
      assert.equal(server.actions.length, reads)
      assert.deepEqual(errors, [])
      assert.ok(
        server.actions.every((action) =>
          ['list', 'selected-tables', 'add-tables', 'remove-tables'].includes(action)
        )
      )
    } finally {
      reader.dispose()
    }
  })
})

/** 旧空清单在新表已展示后返回，不得再次清空下拉候选；卸载后也不接收回执。 */
test('来源候选忽略迟到旧请求及卸载后请求结果', async () => {
  await withServer(async (server) => {
    server.deferReads = true
    const snapshots: Snapshot[] = []
    const errors: unknown[] = []
    const reader = observeBindingSources(
      '/workspace',
      (snapshot) => snapshots.push(snapshot),
      (reason) => errors.push(reason)
    )
    try {
      await until(() => server.pending.length === 2)
      server.deferReads = false
      await changeSelectedTables('/workspace', 'db', ['person'])
      await until(() => snapshots.length === 1)
      server.pending.splice(0).forEach((release) => release())
      await setImmediate()
      assert.equal(snapshots.length, 1)
      assert.equal(snapshots[0].tables[0].table, 'person')
      server.deferReads = true
      reader.refresh()
      await until(() => server.pending.length === 2)
      reader.dispose()
      server.pending.splice(0).forEach((release) => release())
      await setImmediate()
      assert.equal(snapshots.length, 1)
      assert.deepEqual(errors, [])
    } finally {
      reader.dispose()
    }
  })
})

/** 失败或无效回执不发布保存通知，读取失败保留最后成功的候选。 */
test('失败的表清单变更不触发刷新，读取失败可重试且保留现有候选', async () => {
  await withServer(async (server) => {
    let notifications = 0
    const unsubscribe = subscribeDataSourcesChanged('/workspace', () => {
      notifications += 1
    })
    const snapshots: Snapshot[] = []
    const errors: unknown[] = []
    const reader = observeBindingSources(
      '/workspace',
      (snapshot) => snapshots.push(snapshot),
      (reason) => errors.push(reason)
    )
    try {
      await until(() => snapshots.length === 1)
      for (const failure of ['business', 'transport', 'missing'] as const) {
        server.failure = failure
        await assert.rejects(changeSelectedTables('/workspace', 'db', ['person']))
        assert.equal(notifications, 0)
      }
      server.failure = undefined
      const savedFetch = globalThis.fetch
      globalThis.fetch = async () => {
        throw new TypeError('Failed to fetch')
      }
      reader.refresh()
      await until(() => errors.length === 1)
      assert.equal(snapshots.length, 1)
      globalThis.fetch = savedFetch
      reader.refresh()
      await until(() => snapshots.length === 2)
    } finally {
      reader.dispose()
      unsubscribe()
    }
  })
})

/** 连接及外部来源增删改复用同一失效通知，成功前及业务失败时不发送。 */
test('数据源增删改在成功回执后发布工作区通知', async () => {
  await withServer(async (server) => {
    let notifications = 0
    const unsubscribe = subscribeDataSourcesChanged('/workspace', () => {
      notifications += 1
    })
    const source = {
      type: 'external_api' as const,
      name: '测试接口域',
      baseUrl: 'https://api.test',
      timeoutMs: 10000,
      headers: [],
      directories: []
    }
    try {
      await createDataSource('/workspace', source)
      await updateDataSource('/workspace', { ...source, id: 'api' })
      await deleteDataSource('/workspace', 'api')
      assert.equal(notifications, 3)
      server.failure = 'business'
      await assert.rejects(updateDataSource('/workspace', { ...source, id: 'api' }))
      assert.equal(notifications, 3)
    } finally {
      unsubscribe()
    }
  })
})
