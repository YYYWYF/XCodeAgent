import assert from 'node:assert/strict'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import DatabaseCheckPanel from '../src/renderer/src/components/BrowserPreviewPanel/DatabaseCheckPanel'
import type { PreviewDatabaseCheck } from '../src/renderer/src/service/previewRuntime'

const report: PreviewDatabaseCheck = {
  status: 'blocked', message: '请处理异常实体后重新启动。', checkedAt: '2026-10-15T02:00:00Z', entities: [
    { entityId: 'Order', label: '订单', tableName: '12ab34cd56ef_order', status: 'SYNCED', differenceCount: 0, differences: [] },
    { entityId: 'OrderItem', label: '订单明细', tableName: '12ab34cd56ef_order_item', status: 'STRUCTURE_MISMATCH', differenceCount: 1,
      differences: [{ category: 'COLUMN', target: 'is_deleted', expected: '存在', actual: '缺失' }] }
  ]
}

/** 提供只读定位回调，SSR 检查导航入口和结构信息，不发建表或启动请求。 */
function openEntity(_entityId: string): void {}

const html = renderToStaticMarkup(createElement(DatabaseCheckPanel, { report, onOpenEntity: openEntity }))
assert.ok(html.includes('数据库检查'))
assert.ok(html.includes('待处理 1'))
assert.ok(html.includes('<summary'))
assert.doesNotMatch(html.match(/<details\b[^>]*>/)?.[0] || '', /\bopen(?:\s|=|>)/)
assert.ok(html.includes('1/2'))
assert.ok(html.indexOf('订单明细') < html.indexOf('title="订单"'))
assert.ok(html.includes('is_deleted'))
assert.ok(html.includes('期望：存在；实际：缺失'))
assert.ok(html.includes('aria-label="查看实体：订单明细"'))
assert.ok(html.includes('最近检查'))
assert.ok(!html.includes('执行 SQL'))

const empty = renderToStaticMarkup(createElement(DatabaseCheckPanel))
assert.ok(empty.includes('尚未检查'))
assert.ok(!empty.includes('已通过'))

const ready = renderToStaticMarkup(createElement(DatabaseCheckPanel, {
  report: { ...report, status: 'ready', entities: [report.entities[0]] }
}))
assert.ok(ready.includes('已通过 1/1'))
assert.doesNotMatch(ready.match(/<details\b[^>]*>/)?.[0] || '', /\bopen(?:\s|=|>)/)
assert.ok(!ready.includes('表结构通过不代表数据库连接或后端服务已启动'))
