import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act } from 'react-test-renderer'
import type { MappingAssistance, MappingAssistRequest } from '../src/renderer/src/typings/endpointDesignAssist'
import { mappingAssistIssues, mappingAssistSections } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistReviewModel'
import MappingAssistPreview from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistPreview'
import { withMappingHarness } from './mappingAssistTestHarness'

/** 外部来源只携带 Operation 身份，不伪装数据库 CRUD。 */
function externalRequest(): MappingAssistRequest {
  const selection = { sourceType: 'external_api' as const, sourceId: 'supplier', directoryId: 'catalog', operationId: 'products' }
  return { workspaceRoot: 'E:/app', apiContractId: 'products', endpointId: 'list', selection, baseRevision: null, technicalPlanHash: 'a'.repeat(64),
    draft: { apiContractId: 'products', endpointId: 'list', sourceBinding: selection, fieldMappings: [], externalApiBindings: [] } }
}

/** 外部请求和应用响应可以同名，但待明确项必须分别归属各自目标。 */
test('外部生成结果分区与待明确项不混入数据库区域或同名返回', () => {
  const result: MappingAssistance = { baseline: { draftHash: 'a'.repeat(64), metadataHash: 'b'.repeat(64) }, operation: null, operationReason: '使用当前外部接口', reviews: [],
    mapping: { externalApiBindings: [{ externalField: { sourceType: 'external_api', sourceId: 'supplier', directoryId: 'catalog', operationId: 'products', section: 'query', path: 'name', type: 'string' } }],
      fieldMappings: [{ mappingType: 'unconfigured', endpointField: { side: 'response', location: 'response_body', path: 'name', type: 'string' } }] },
    questions: ['外部入参 external:query:name 请明确取值。', '外部入参 external:query:name 尚未配置，请补充取值或按需移除可选参数。', '返回字段 name 尚未配置，请补充其取值规则。'] }
  assert.deepEqual(mappingAssistSections(result).map((section) => section.key), ['request', 'return', 'description'])
  const issues = mappingAssistIssues(result)
  assert.equal(issues.length, 2)
  assert.deepEqual(issues.find((issue) => issue.key === 'external:query:name')?.questions, ['外部入参 external:query:name 请明确取值。'])
  assert.equal(issues.find((issue) => issue.key === 'response:response_body:name')?.questions.length, 1)
  const html = renderToStaticMarkup(createElement(MappingAssistPreview, { mapping: result.mapping!, area: 'request', onShowIssues: () => {} }))
  assert.match(html, /外部 API 入参/)
  assert.match(html, /待明确/)
  assert.doesNotMatch(html, /数据库|查询条件|写入字段/)
})

/** 外部结果无需 operation；通过真实 AG-UI 客户端替换一次并可撤销。 */
test('外部辅助生成替换撤销沿用原草稿回填且不带数据库操作', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => h.setRequest(externalRequest()))
    await act(async () => { h.state().setInstruction('调用供应商查询商品'); await h.state().generate() })
    assert.equal(h.state().isExternal, true)
    assert.equal(h.state().canApply, true)
    assert.equal(h.state().result?.operation, null)
    assert.equal(h.edits.length, 0)
    await act(async () => { await Promise.all([h.state().apply(), h.state().apply()]) })
    assert.equal(h.edits.length, 1)
    assert.equal(h.edits[0].databaseOperation, undefined)
    assert.deepEqual(h.edits[0].externalApiBindings, [])
    assert.equal(server.requests.filter((item) => item.input.action === 'replace_mapping').length, 1)
    assert.equal(h.state().canUndo, true)
    await act(async () => h.state().undoApply())
    assert.equal(h.edits.length, 2)
    assert.equal(h.edits[1].implementationDescription, undefined)
  })
})

/** 生成期间切换 Operation，旧响应即使迟到也不能应用。 */
test('外部 Operation 切换丢弃旧生成结果，取消不回填草稿', async () => {
  await withMappingHarness(async (h, server) => {
    const request = externalRequest()
    await act(async () => h.setRequest(request))
    server.held = true
    let pending!: Promise<void>
    await act(async () => { h.state().setInstruction('查询商品'); pending = h.state().generate(); await Promise.resolve() })
    const selection = { sourceType: 'external_api' as const, sourceId: 'supplier', directoryId: 'catalog', operationId: 'other' }
    await act(async () => h.setRequest({ ...request, selection, draft: { ...request.draft, sourceBinding: selection } }))
    await act(async () => { server.requests[0].release(); await pending })
    assert.equal(h.state().result, undefined)
    assert.equal(h.edits.length, 0)
    await act(async () => { pending = h.state().generate(); await Promise.resolve() })
    await act(async () => h.state().cancel())
    await act(async () => { server.requests.at(-1)!.release(); await pending })
    assert.equal(h.state().result, undefined)
    assert.equal(h.state().phase, 'idle')
    assert.equal(h.edits.length, 0)
  })
})
