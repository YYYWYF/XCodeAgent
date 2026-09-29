import assert from 'node:assert/strict'
import { test } from 'node:test'
import { externalTargetEnabled } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/externalRequestTargets'
import type { WorkflowApiDesignDraft } from '../src/renderer/src/typings/workflow'

const selection = { sourceType: 'external_api' as const, sourceId: 'api', directoryId: 'dir', operationId: 'get' }
const target = { section: 'query' as const, path: 'c', type: 'string', required: false }
const binding = { externalField: { ...selection, ...target }, right: { kind: 'fixed' as const, value: 'value' } }

/** 保存和重新加载后，被删除的可选参数仍不显示，必填参数保持可见。 */
test('optional target visibility survives save and reload', () => {
  const bindings: NonNullable<WorkflowApiDesignDraft['externalApiBindings']> = [binding]
  assert.equal(externalTargetEnabled(target, selection, bindings), true)
  const saved = JSON.parse(JSON.stringify(bindings.filter((item) => item !== binding)))
  assert.equal(externalTargetEnabled(target, selection, saved), false)
  assert.equal(externalTargetEnabled({ ...target, required: true }, selection, saved), true)
})

/** 恢复的空绑定也持久化为可见行，等待用户填写，避免依赖组件内存。 */
test('restored unconfigured target remains visible after draft reload', () => {
  const restored = JSON.parse(JSON.stringify([{ externalField: binding.externalField }]))
  assert.equal(externalTargetEnabled(target, selection, restored), true)
})

/** 不同操作或请求区域的同名参数不能互相恢复。 */
test('target identity includes operation and request section', () => {
  assert.equal(externalTargetEnabled(target, { ...selection, operationId: 'other' }, [binding]), false)
  assert.equal(externalTargetEnabled({ ...target, section: 'header' }, selection, [binding]), false)
})
