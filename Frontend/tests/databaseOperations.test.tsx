import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import DatabaseOperationTag from '../src/renderer/src/components/DatabaseOperationTag'
import { requestEntityTable } from '../src/renderer/src/service/entityTables'
import type { DatabaseTableOperation } from '../src/renderer/src/typings/managedDatabase'

const table = '12ab34cd56ef_order'

/** 构造当前服务端安全操作投影，资源回收通过目标清单关联实体。 */
function operation(mode: DatabaseTableOperation['mode']): DatabaseTableOperation {
  return {
    operationId: '123', mode, tableName: mode === 'RELEASE' ? '' : table, tableNames: [table],
    status: 'SUCCEEDED', createdAt: '2026-10-15T02:00:00Z',
    executionResult: { errorCode: null, errorSummary: null, recoveryState: 'NOT_REQUESTED' }
  }
}

// 实际渲染共用标签，验证四种操作不会被混称为建表成功。
for (const [mode, label] of [['CREATE', '创建'], ['REBUILD', '重建'], ['DROP', '删除'], ['RELEASE', '资源回收']] as const) {
  const html = renderToStaticMarkup(createElement(DatabaseOperationTag, { operation: operation(mode), status: 'SUCCEEDED' }))
  assert.ok(html.includes(`最近操作：${label} · 成功`))
  assert.ok(!html.includes('当前结构一致'))
}
assert.ok(renderToStaticMarkup(createElement(DatabaseOperationTag, { status: 'RUNNING' })).includes('最近操作：执行中'))
assert.equal(renderToStaticMarkup(createElement(DatabaseOperationTag, {})), '')

const previousFetch = globalThis.fetch
const previousWindow = globalThis.window
Object.assign(globalThis, { window: { devAgentStudio: { agentBaseUrl: 'http://127.0.0.1:8000' } } })
let mode: DatabaseTableOperation['mode'] = 'DROP'
let forwarded: Record<string, unknown> | undefined

try {
  /** 使用完整标准事件验证真实 AG-UI 客户端，刷新不携带删除或回收请求意图。 */
  globalThis.fetch = async (_input, init) => {
    const body = JSON.parse(String(init?.body))
    forwarded = body.forwardedProps.entityTables
    const value = {
      schemaVersion: 1, runId: body.runId, threadId: body.threadId, status: 'completed',
      preview: { entityId: 'Order', entityName: '订单', datasourceKey: '12ab34cd56ef', datasourceId: 2,
        tableName: table, sql: 'internal-sql', previewHash: 'a'.repeat(64), columns: [] },
      operation: operation(mode),
      readiness: { ready: false, tables: [{ tableName: table, status: 'NOT_CREATED', differences: [] }] }
    }
    const events = [
      { type: 'RUN_STARTED', threadId: body.threadId, runId: body.runId },
      { type: 'TEXT_MESSAGE_START', messageId: 'operation-message', role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId: 'operation-message', delta: '已读取操作记录' },
      { type: 'CUSTOM', name: 'entity-tables', value },
      { type: 'STATE_SNAPSHOT', snapshot: { entityTables: value } },
      { type: 'TEXT_MESSAGE_END', messageId: 'operation-message' },
      { type: 'RUN_FINISHED', threadId: body.threadId, runId: body.runId, result: { entityTables: value } }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  }
  for (const current of ['DROP', 'RELEASE'] as const) {
    mode = current
    const result = await requestEntityTable('/workspace', 'Order', 'refresh')
    assert.equal(result.operation?.mode, current)
    assert.deepEqual(result.operation?.tableNames, [table])
    assert.equal(result.operation?.tableName, current === 'RELEASE' ? '' : table)
    assert.equal(result.readiness?.ready, false)
    assert.deepEqual(forwarded, { workspaceRoot: '/workspace', entityId: 'Order', action: 'refresh' })
  }
} finally {
  globalThis.fetch = previousFetch
  Object.assign(globalThis, { window: previousWindow })
}
