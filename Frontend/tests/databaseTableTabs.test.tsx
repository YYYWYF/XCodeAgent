import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import DatabaseTableTabs from '../src/renderer/src/components/DataSourcesPage/DatabaseTableTabs'
import { requestDatabaseTablePage, requestDatabaseTableOperations } from '../src/renderer/src/service/databaseTableData'

const html = renderToStaticMarkup(createElement(DatabaseTableTabs, {
  workspaceRoot: '/workspace', theme: 'dark', table: { sourceId: 'database', schema: '', table: '12ab34cd56ef_order', description: '订单表' }
}))
assert.ok(html.includes('表结构'))
assert.ok(html.includes('表数据'))
assert.ok(html.includes('操作记录'))
assert.ok(html.includes('aria-selected="true"'))
assert.ok(!html.includes('执行 SQL'))

const previousFetch = globalThis.fetch
const previousWindow = globalThis.window
let request: Record<string, unknown> | undefined
let url = ''
let failed = false
Object.assign(globalThis, { window: { devAgentStudio: { agentBaseUrl: 'http://127.0.0.1:8000' } } })

try {
  /** 用标准 AG-UI 生命周期验证真实客户端，不引入自写 SSE 消费器。 */
  globalThis.fetch = async (input, init) => {
    url = String(input)
    const body = JSON.parse(String(init?.body))
    request = body.forwardedProps.dataSources
    const value = { schemaVersion: 1, runId: body.runId, threadId: body.threadId, status: failed ? 'failed' : 'completed',
      error: failed ? { message: '当前表不可用' } : undefined,
      dataPage: { status: 'SUCCEEDED', sourceId: 'database', datasourceKey: '12ab34cd56ef', tableName: '12ab34cd56ef_order',
        columns: [
          { name: 'id', type: 'bigint', nullable: false, primaryKey: true, autoIncrement: true, defaultValue: null, description: '主键' },
          { name: 'reason', type: 'text', nullable: true, primaryKey: false, autoIncrement: false, defaultValue: null, description: '原因' }
        ], rows: [{ id: '18446744073709551615', reason: null }], pageNumber: 2, pageSize: 20, hasMore: false, truncated: false,
        columnFormats: { id: 'TEXT', reason: 'TEXT' }, filteringSupported: false }, operations: [], historyLimit: 100 }
    const events = [
      { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
      { type: 'TEXT_MESSAGE_START', messageId: 'm', role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'm', delta: '读取表数据' },
      { type: 'CUSTOM', name: 'data-sources', value },
      { type: 'STATE_SNAPSHOT', snapshot: { dataSources: value } },
      { type: 'TEXT_MESSAGE_END', messageId: 'm' },
      { type: 'RUN_FINISHED', threadId: body.threadId, runId: body.runId, result: { dataSources: value } }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
  }
  const page = await requestDatabaseTablePage('/workspace', 'database', '12ab34cd56ef_order', 2, 20)
  assert.ok(url.endsWith('/data-sources/database-rows'))
  assert.equal(page.rows[0].id, '18446744073709551615')
  assert.equal(page.rows[0].reason, null)
  assert.equal(page.datasourceKey, '12ab34cd56ef')
  assert.deepEqual(request, { workspaceRoot: '/workspace', sourceId: 'database', table: '12ab34cd56ef_order', pageNumber: 2, pageSize: 20 })
  const history = await requestDatabaseTableOperations('/workspace', 'database', '12ab34cd56ef_order')
  assert.ok(url.endsWith('/data-sources/database-operations'))
  assert.equal(history.historyLimit, 100)
  failed = true
  await assert.rejects(requestDatabaseTablePage('/workspace', 'database', '12ab34cd56ef_order', 1, 20), /当前表不可用/)
  const controller = new AbortController()
  controller.abort()
  await assert.rejects(requestDatabaseTablePage('/workspace', 'database', '12ab34cd56ef_order', 1, 20, controller.signal), /请求已取消/)
} finally {
  globalThis.fetch = previousFetch
  Object.assign(globalThis, { window: previousWindow })
}
