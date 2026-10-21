import assert from 'node:assert/strict'
import { test } from 'node:test'
import { act } from 'react-test-renderer'
import { withMappingHarness } from './mappingAssistTestHarness'
import { assistRequest } from './mappingAssistModel.test'

/** 首次需求不能由字段回答代替，空白校验在真实 hook 发送请求前执行。 */
test('需求说明必填，后续补充与主需求分开发送', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => {
      h.state().setClarification('查询按名称排序')
      h.state().setAnswer('response:response_body:total', '返回字段 total', '按筛选条件统计')
    })
    for (const value of ['', ' \n\t ']) {
      await act(async () => { h.state().setInstruction(value); await h.state().generate() })
      assert.equal(server.requests.length, 0)
      assert.match(h.state().error, /请先填写需求说明/)
      assert.equal(h.state().phase, 'idle')
    }
    await act(async () => { h.state().setInstruction(' 查询商品列表 '); await h.state().generate() })
    assert.equal(server.requests[0].input.instruction, '查询商品列表')
    assert.equal(server.requests[0].input.clarification, '查询按名称排序\n返回字段 total：按筛选条件统计')
  })
})

/** 修改回答的同一事件内也不能通过旧回调应用旧结果。 */
test('真实 hook 保留旧预览但阻止未重新生成的补充内容被应用', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => h.state().setInstruction('查询商品售价'))
    await act(async () => { await h.state().generate() })
    assert.equal(h.state().canApply, true)
    const oldApply = h.state().apply
    await act(async () => { h.state().setClarification('售价单位为元'); await oldApply() })
    assert.equal(server.requests.length, 1)
    assert.ok(h.state().previewDraft)
    assert.equal(h.state().requirementsChanged, true)
    assert.equal(h.state().canApply, false)
    await act(async () => { await h.state().apply() })
    assert.equal(h.edits.length, 0)
    await act(async () => { await h.state().generate() })
    assert.equal(server.requests[1].input.instruction, '查询商品售价')
    assert.match(server.requests[1].input.clarification || '', /售价单位为元/)
    assert.equal(h.state().requirementsChanged, false)
    assert.equal(h.state().canApply, true)
  })
})

/** 字段回答按目标身份参与请求，重生成、失败和取消都不静默改写草稿。 */
test('字段补充必须重新生成且保留回答，切换接口后清除', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => h.state().setInstruction('查询商品列表'))
    await act(async () => { await h.state().generate() })
    const oldApply = h.state().apply
    await act(async () => { h.state().setAnswer('response:response_body:total', '返回字段 total', '按筛选条件统计分页前总数'); await oldApply() })
    assert.equal(server.requests.length, 1)
    assert.equal(h.state().canApply, false)
    assert.equal(h.edits.length, 0)
    await act(async () => { await h.state().generate() })
    assert.equal(server.requests.at(-1)?.input.instruction, '查询商品列表')
    assert.match(server.requests.at(-1)?.input.clarification || '', /返回字段 total：按筛选条件统计分页前总数/)
    assert.equal(h.state().requirementsChanged, false)
    assert.equal(h.state().answers['response:response_body:total'].value, '按筛选条件统计分页前总数')
    server.failStage = true
    await act(async () => { await h.state().generate() })
    assert.ok(h.state().error)
    await act(async () => h.state().cancel())
    assert.ok(h.state().answers['response:response_body:total'])
    assert.equal(h.edits.length, 0)
    await act(async () => h.setRequest({ ...assistRequest(), endpointId: 'other' }))
    assert.deepEqual(h.state().answers, {})
    assert.equal(h.state().instruction, '')
  })
})

/** 多个字段补充受同一后端长度上限约束，超限不能发送无效请求。 */
test('字段回答与全局要求合计超限时保留输入并阻止生成', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => {
      h.state().setInstruction('说明'.repeat(1500))
      h.state().setAnswer('a', '返回字段 a', '规则'.repeat(450))
      h.state().setAnswer('b', '返回字段 b', '规则'.repeat(450))
      await h.state().generate()
    })
    assert.equal(server.requests.length, 0)
    assert.match(h.state().error, /4000/)
    assert.equal(h.state().answers.b.value.length, 900)
    assert.equal(h.state().phase, 'idle')
  })
})

/** 在请求尚未返回时切换 Endpoint，旧请求成功也不能显示或回填。 */
test('真实 hook 丢弃 Endpoint 切换及取消后的迟到回执', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => h.state().setInstruction('查询商品售价'))
    server.held = true
    let pending!: Promise<void>
    await act(async () => { pending = h.state().generate() })
    assert.equal(h.state().phase, 'generating')
    await act(async () => h.setRequest({ ...assistRequest(), endpointId: 'other' }))
    await act(async () => { server.requests[0].release(); await pending })
    assert.equal(h.state().result, undefined)
    assert.equal(h.state().phase, 'idle')
    await act(async () => { h.state().setInstruction('查询另一个商品'); pending = h.state().generate() })
    await act(async () => { h.state().cancel(); server.requests[1].release(); await pending })
    assert.equal(h.state().result, undefined)
    assert.equal(h.edits.length, 0)
    server.held = false
    await act(async () => { await h.state().generate() })
    assert.ok(h.state().result)
    assert.equal(server.requests[2].input.endpointId, 'other')
  })
})

/** 一次回填、一次撤销与后续手工修改均由真实 React 更新验证。 */
test('真实 hook 阻止重复替换，应用锁持续到回执，撤销不覆盖后续编辑', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => h.state().setInstruction('查询商品售价'))
    await act(async () => { await h.state().generate() })
    server.held = true
    let applying!: Promise<void>
    await act(async () => { applying = h.state().apply(); await h.state().apply() })
    assert.equal(h.state().phase, 'applying')
    assert.equal(server.requests.filter((row) => row.input.action === 'replace_mapping').length, 1)
    assert.equal(h.edits.length, 0)
    await act(async () => { server.requests[1].release(); await applying })
    assert.equal(h.edits.length, 1)
    assert.equal(h.state().canUndo, true)
    await act(async () => h.state().undoApply())
    assert.equal(h.edits.length, 2)
    assert.equal(h.state().canUndo, false)
    server.held = false
    await act(async () => { await h.state().generate() })
    await act(async () => { await h.state().apply() })
    const afterApply = h.edits.length
    await act(async () => {
      h.state().invalidate()
      h.setRequest({ ...assistRequest(), draft: { ...assistRequest().draft, implementationDescription: '手动修改' } })
    })
    await act(async () => h.state().undoApply())
    assert.equal(h.edits.length, afterApply)
    assert.equal(h.state().canUndo, false)
  })
})

/** 恢复凭据只有输入未变且用户明确点击继续时才发送。 */
test('真实 hook 显式从失败步骤继续，补充变化使阶段重试失效', async () => {
  await withMappingHarness(async (h, server) => {
    await act(async () => h.state().setInstruction('查询商品售价'))
    server.failStage = true
    await act(async () => { await h.state().generate() })
    assert.equal(h.state().failure?.failedStage, 'return')
    assert.equal(server.requests.length, 1)
    server.failStage = false
    await act(async () => { await h.state().generate(true) })
    assert.equal(server.requests[1].input.retryToken, 'c'.repeat(32))
    assert.ok(h.state().result)
    server.failStage = true
    await act(async () => { await h.state().generate() })
    await act(async () => h.state().setInstruction('新要求'))
    assert.equal(h.state().failure, undefined)
    const calls = server.requests.length
    await act(async () => { await h.state().generate(true) })
    assert.equal(server.requests.length, calls)
    server.failStage = false
    await act(async () => { await h.state().generate() })
    assert.equal(server.requests.at(-1)?.input.retryToken, undefined)
  })
})

/** 操作属于生成结果，歧义时没有可应用映射；明确后与字段配置一次回填。 */
test('真实 hook 支持未选操作生成、歧义阻断和操作整体替换撤销', async () => {
  await withMappingHarness(async (h, server) => {
    const original = assistRequest()
    await act(async () => h.setRequest({ ...original, draft: { ...original.draft, databaseOperation: undefined } }))
    await act(async () => h.state().setInstruction('处理商品'))
    server.operation = null
    await act(async () => { await h.state().generate() })
    assert.equal(h.state().result?.operation, null)
    assert.equal(h.state().previewDraft, undefined)
    assert.equal(h.state().canApply, false)
    await act(async () => { await h.state().apply() })
    assert.equal(h.edits.length, 0)
    server.operation = 'create'
    await act(async () => { h.state().setClarification('新增商品'); await h.state().generate() })
    assert.equal(h.state().previewDraft?.databaseOperation, 'create')
    assert.equal(h.state().canApply, true)
    await act(async () => { await h.state().apply() })
    assert.equal(h.edits[0].databaseOperation, 'create')
    assert.equal(h.edits[0].databaseQuery, undefined)
    await act(async () => h.state().undoApply())
    assert.equal(h.edits[1].databaseOperation, undefined)
  })
})
