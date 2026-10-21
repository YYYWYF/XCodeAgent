import assert from 'node:assert/strict'
import { test } from 'node:test'
import type { MappingAssistRequest } from '../src/renderer/src/typings/endpointDesignAssist'
import { assistInputKey, assistResultIsCurrent, canUndoMapping } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistModel'

/** 提供数据库辅助请求基线，不接触实际工作区。 */
export function assistRequest(): MappingAssistRequest {
  const selection = { sourceType: 'database' as const, sourceId: 'db', schema: 'shop', table: 'product' }
  return { workspaceRoot: 'E:/shop', apiContractId: 'product', endpointId: 'detail', selection,
    baseRevision: null, technicalPlanHash: 'a'.repeat(64), draft: { apiContractId: 'product', endpointId: 'detail', sourceBinding: selection, databaseOperation: 'read', fieldMappings: [] } }
}


/** 基线包含目标、来源、操作、版本及整个草稿，不能复用过期建议。 */
test('mapping assistant invalidates changes to all identity inputs', () => {
  const request = assistRequest()
  const captured = assistInputKey(request, { columns: ['price'] })
  for (const next of [
    { ...request, endpointId: 'other' }, { ...request, technicalPlanHash: 'b'.repeat(64) },
    { ...request, selection: { ...request.selection, table: 'other' } },
    { ...request, draft: { ...request.draft, implementationDescription: '手动编辑' } }
  ]) assert.equal(assistResultIsCurrent(captured, assistInputKey(next, { columns: ['price'] })), false)
  assert.equal(assistResultIsCurrent(captured, assistInputKey(request, { columns: ['stock'] })), false)
  assert.equal(assistResultIsCurrent(captured, captured, true), false)
  assert.equal(assistResultIsCurrent(captured, captured), true)
})

/** 只允许撤销原应用结果，手动编辑后的快照不能回滚。 */
test('mapping assistant undo requires unchanged applied draft', () => {
  const request = assistRequest()
  const applied = { ...request, draft: { ...request.draft, implementationDescription: 'AI 建议' } }
  const key = assistInputKey(applied)
  assert.equal(canUndoMapping(key, assistInputKey(applied)), true)
  assert.equal(canUndoMapping(key, assistInputKey(request)), false)
  assert.equal(canUndoMapping(undefined, key), false)
})
