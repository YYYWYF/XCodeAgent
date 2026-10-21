import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { requestMappingAssist } from '../src/renderer/src/service/endpointDesigns'
import { assistResultIsCurrent, assistInputKey } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistModel'
import MappingAssistPanel from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistPanel'
import MappingAssistReview, { MappingReviewContext } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistReview'
import type { MappingAssistState } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useMappingAssist'
import type { MappingAssistProgress } from '../src/renderer/src/typings/endpointDesignAssist'
import { assistRequest } from './mappingAssistModel.test'

type MockServer = { calls: string[]; failed: boolean; truncate: boolean; delayed: boolean; release?: () => void }

/** 模拟完整 AG-UI 回执，继续由真实客户端消费标准事件。 */
function streamResponse(threadId: string, runId: string, server: MockServer, action: string): Response {
  const value = { schemaVersion: 1, runId, threadId, action, status: server.failed ? 'failed' : 'completed',
    error: server.failed ? { message: '建议字段无效' } : undefined,
    assistance: { baseline: { draftHash: 'a'.repeat(64), metadataHash: 'b'.repeat(64) }, operation: 'read', operationReason: '根据契约查询', mapping: { databaseOperation: 'read', databaseQuery: null, databaseWrites: [], fieldMappings: [], implementationDescription: '' }, questions: [], reviews: [] },
    refinedRule: { description: '保留两位小数', questions: [] },
    applied: { draft: assistRequest().draft, areas: ['return', 'description'], targets: [] } }
  const events: unknown[] = [
    { type: 'RUN_STARTED', threadId, runId },
    { type: 'TEXT_MESSAGE_START', messageId: 'assistant', role: 'assistant' },
    { type: 'CUSTOM', name: 'endpoint-designs', value: { ...value, status: 'in_progress', progress: { stage: 'generating', message: '正在生成', percent: 35 } } },
    { type: 'CUSTOM', name: 'endpoint-designs', value },
    { type: 'STATE_SNAPSHOT', snapshot: { endpointDesigns: value } },
    { type: 'TEXT_MESSAGE_CONTENT', messageId: 'assistant', delta: '完成' },
    { type: 'TEXT_MESSAGE_END', messageId: 'assistant' }
  ]
  if (!server.truncate) events.push({ type: 'RUN_FINISHED', threadId, runId, result: { endpointDesigns: value } })
  return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
}

/** 隔离网络与窗口，支持业务失败、未结束流及取消场景。 */
async function withServer(operation: (server: MockServer) => Promise<void>): Promise<void> {
  const originalWindow = globalThis.window, originalFetch = globalThis.fetch
  const server: MockServer = { calls: [], failed: false, truncate: false, delayed: false }
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://agent.test' } } })
  globalThis.fetch = async (_url, init) => {
    const input = JSON.parse(String(init?.body))
    const action = input.forwardedProps.endpointDesigns.action
    server.calls.push(action)
    if (server.delayed) return new Promise<Response>((resolve, reject) => {
      server.release = () => resolve(streamResponse(input.threadId, input.runId, server, action))
      init?.signal?.addEventListener('abort', () => reject(new DOMException('cancelled', 'AbortError')), { once: true })
    })
    return streamResponse(input.threadId, input.runId, server, action)
  }
  try { await operation(server) } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
}

/** 标准进度不能替代最终结果，规则完善不得触发保存或应用动作。 */
test('mapping assist uses isolated actions and standard progress lifecycle', async () => {
  await withServer(async (server) => {
    const progress: MappingAssistProgress[] = []
    const response = await requestMappingAssist('generate_mapping', assistRequest(), { instruction: '映射售价' }, { onProgress: (value) => progress.push(value) })
    assert.equal(response.status, 'completed')
    assert.ok(progress.some((value) => value.stage === 'generating'))
    assert.ok(response.assistance)
    const rule = await requestMappingAssist('refine_rule', assistRequest(), { description: '售价', target: { area: 'return', key: 'response:response_body:price' }, dependencies: [] })
    assert.equal(rule.refinedRule?.description, '保留两位小数')
    assert.deepEqual(server.calls, ['generate_mapping', 'refine_rule'])
    const replacement = await requestMappingAssist('replace_mapping', assistRequest(), {
      baseline: response.assistance?.baseline, mapping: response.assistance?.mapping
    })
    assert.ok(replacement.applied)
    assert.deepEqual(server.calls, ['generate_mapping', 'refine_rule', 'replace_mapping'])
  })
})

/** 业务失败及缺少结束帧均拒绝交付可应用结果。 */
test('mapping assist rejects business failures and incomplete streams', async () => {
  await withServer(async (server) => {
    server.failed = true
    await assert.rejects(requestMappingAssist('generate_mapping', assistRequest(), { instruction: '售价' }), /建议字段无效/)
    server.failed = false; server.truncate = true
    await assert.rejects(requestMappingAssist('generate_mapping', assistRequest(), { instruction: '售价' }))
  })
})

/** 取消前不发送请求，运行中取消后不接受迟到建议。 */
test('mapping assist aborts pending runs and suppresses stale endpoint results', async () => {
  await withServer(async (server) => {
    const stopped = new AbortController(); stopped.abort()
    await assert.rejects(requestMappingAssist('generate_mapping', assistRequest(), {}, { signal: stopped.signal }))
    assert.deepEqual(server.calls, [])
    server.delayed = true
    const controller = new AbortController()
    const captured = assistInputKey(assistRequest())
    const result = requestMappingAssist('generate_mapping', assistRequest(), { instruction: '映射' }, { signal: controller.signal })
    const rejected = assert.rejects(result)
    controller.abort(); server.release?.()
    await rejected
    assert.equal(assistResultIsCurrent(captured, assistInputKey({ ...assistRequest(), endpointId: 'other' }), controller.signal.aborted), false)
  })
})

/** 完整预览使用必填需求生成，替换操作不展示差异复选框。 */
test('full mapping preview replaces the entire draft and exposes pending fields', () => {
  /** 只读渲染不发出网络请求。 */
  const noop = (): void => {}
  const assist = { open: true, preview: true, instruction: '查询商品列表', clarification: '', answers: {}, requirementsLength: 6, requirementsChanged: false, phase: 'idle', error: '', stale: false, canApply: true,
    result: { baseline: { draftHash: '', metadataHash: '' }, operation: 'read', operationReason: '接口返回商品列表', mapping: { databaseOperation: 'read', databaseQuery: null, databaseWrites: [], implementationDescription: '', fieldMappings: [
      { mappingType: 'unconfigured', endpointField: { side: 'response', location: 'response_body', path: 'categoryName', type: 'string', required: true } }
    ] }, questions: ['分类名称如何返回？'], reviews: [{ area: 'return', key: 'response:response_body:price', reason: '用户要求金额转为分', needsReview: true }] },
    close: noop, cancel: noop, setInstruction: noop, setClarification: noop, setAnswer: noop, setPreview: noop, generate: noop, apply: noop } as unknown as MappingAssistState
  const markup = renderToStaticMarkup(createElement(MappingAssistPanel, { assist, disabled: false }))
  for (const label of ['替换草稿', 'categoryName', '不保留原有取值', '映射结果分区', '补充并重新生成', 'AI 映射已生成']) assert.ok(markup.includes(label))
  assert.ok(!markup.includes('checkbox'))
  const locked = renderToStaticMarkup(createElement(MappingAssistPanel, { assist: { ...assist, phase: 'applying' }, disabled: true }))
  assert.match(locked, /disabled/)
  const hidden = renderToStaticMarkup(createElement(MappingAssistPanel, { assist: { ...assist, preview: false }, disabled: false }))
  assert.ok(!hidden.includes('mapping-review-content'))
  assert.ok(!hidden.includes('替换草稿'))
  const changed = renderToStaticMarkup(createElement(MappingAssistPanel, { assist: { ...assist, requirementsChanged: true, canApply: false }, disabled: false }))
  assert.ok(changed.includes('需求说明或补充内容尚未用于生成'))
  const review = renderToStaticMarkup(createElement(MappingReviewContext.Provider, { value: assist.result!.reviews },
    createElement(MappingAssistReview, { area: 'return', target: 'response:response_body:price' })))
  assert.ok(review.includes('用户要求金额转为分'))
  assert.match(changed, /disabled/)
  const ambiguous = renderToStaticMarkup(createElement(MappingAssistPanel, { assist: { ...assist, canApply: false,
    result: { ...assist.result!, operation: null, operationReason: '删除方式不明', mapping: null, questions: ['物理删除还是软删除？'] } }, disabled: false }))
  assert.ok(ambiguous.includes('操作待明确'))
  assert.ok(ambiguous.includes('物理删除还是软删除'))
  assert.ok(!ambiguous.includes('替换草稿'))
})

/** 初始界面明确区分必填需求与后续补充，空白主需求时无法点击生成。 */
test('生成入口标记需求说明必填，填写后才允许生成', () => {
  const assist = { open: true, phase: 'idle', instruction: '', clarification: '', answers: {}, requirementsLength: 0,
    requirementsChanged: false, close: () => {}, setInstruction: () => {}, generate: async () => {} } as unknown as MappingAssistState
  for (const instruction of ['', ' \n ', '查询商品列表']) {
    const markup = renderToStaticMarkup(createElement(MappingAssistPanel, { assist: { ...assist, instruction }, disabled: false }))
    assert.ok(markup.includes('需求说明'))
    assert.ok(markup.includes('（必填）'))
    assert.ok(!markup.includes('补充要求'))
    const textarea = markup.match(/<textarea\b[^>]*>/)?.[0] || ''
    assert.ok(textarea.includes('aria-label="AI 映射需求说明"'))
    assert.match(textarea, /\srequired=""/)
    assert.ok(textarea.includes('aria-required="true"'))
    const button = [...markup.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].find(([value]) => value.includes('生成映射'))?.[0] || ''
    assert.ok(button)
    assert.equal(/\sdisabled[=\s>]/.test(button), !instruction.trim())
  }
})

