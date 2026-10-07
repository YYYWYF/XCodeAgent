import assert from 'node:assert/strict'
import { test } from 'node:test'
import { readBindingRecoverySnapshot, reconcileBindingAfterRecovery } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/bindingRecovery'
import type { BindingEntry } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useBindingWorkspace'
import { endpointFailureIsTransport, endpointRecoveryScope } from '../src/renderer/src/components/AiChatPanel/hooks/useEndpointDesignRecovery'
import { requestEndpointDesignPreparation } from '../src/renderer/src/service/endpointDesigns'
import { requestDataSources } from '../src/renderer/src/service/dataSources'
import { AgUiBusinessError } from '../src/renderer/src/service/agUiBusinessError'

/** 以真实 HttpAgent 生命周期验证断线校准的动作边界和业务错误归属。 */
test('字段映射恢复只读、保留输入和确认状态，业务拒绝不冒充停服', async () => {
  const savedFetch = globalThis.fetch
  const savedWindow = globalThis.window
  Object.assign(globalThis, { window: { devAgentStudio: { agentBaseUrl: 'http://127.0.0.1:8000' } } })
  const actions: string[] = []
  let failed = false
  const fresh = { apiContractId: 'api', endpointId: 'one', artifactRevision: 'r2', technicalPlanHash: 'h2', payload: {} }
  globalThis.fetch = async (input, init) => {
    const body = JSON.parse(String(init?.body))
    const endpoint = body.forwardedProps.endpointDesigns
    const action = endpoint?.action || String(input).split('/').at(-1)
    actions.push(action)
    const name = endpoint ? 'endpoint-designs' : 'data-sources'
    const stateKey = endpoint ? 'endpointDesigns' : 'dataSources'
    const value = { schemaVersion: 1, runId: body.runId, threadId: body.threadId,
      status: failed ? 'failed' : 'completed', error: failed ? { message: '版本冲突' } : undefined,
      preparation: fresh, catalog: { sources: [] }, tables: [], metadata: { tables: [], columns: [] } }
    const events = [
      { type: 'RUN_STARTED', runId: body.runId, threadId: body.threadId },
      { type: 'CUSTOM', name, value },
      { type: 'STATE_SNAPSHOT', snapshot: { [stateKey]: value } },
      { type: 'RUN_FINISHED', runId: body.runId, threadId: body.threadId }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
  }
  try {
    const snapshot = await readBindingRecoverySnapshot('/workspace', { apiContractId: 'api', endpointId: 'one' },
      { sourceType: 'database', sourceId: 'db', schema: 'public', table: 'cats' })
    assert.deepEqual(actions.sort(), ['database-columns', 'list', 'prepare', 'selected-tables'])
    const entry = { preparation: fresh, value: { draft: { fieldMappings: [], implementationDescription: '未保存输入' }, selection: null,
      baseRevision: 'r1', technicalPlanHash: 'h1' }, readOnly: false, dirty: true, complex: false, conflict: false } as BindingEntry
    const next = reconcileBindingAfterRecovery(entry, snapshot[2]!)
    assert.equal(next.value, entry.value)
    assert.equal(next.preparation, entry.preparation)
    assert.equal(next.dirty, true)
    assert.equal(next.readOnly, false)
    assert.equal(next.conflict, true)
    assert.equal(reconcileBindingAfterRecovery({ ...entry, readOnly: true, dirty: false }, snapshot[2]!).readOnly, true)
    failed = true
    for (const call of [() => requestEndpointDesignPreparation('/workspace', 'api', 'one'), () => requestDataSources('/workspace')]) {
      await assert.rejects(call(), (reason) => reason instanceof AgUiBusinessError && !endpointFailureIsTransport(reason))
    }
    globalThis.fetch = async () => { throw new TypeError('Failed to fetch') }
    await assert.rejects(requestEndpointDesignPreparation('/workspace', 'api', 'one'), endpointFailureIsTransport)
    assert.notEqual(endpointRecoveryScope('/workspace', { apiContractId: 'api', endpointId: 'one' }), endpointRecoveryScope('/workspace', { apiContractId: 'api', endpointId: 'two' }))
  } finally { globalThis.fetch = savedFetch; Object.assign(globalThis, { window: savedWindow }) }
})
