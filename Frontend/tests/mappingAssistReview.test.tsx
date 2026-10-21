import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import type { WorkflowApiDatabaseCondition, WorkflowApiFieldMapping } from '../src/renderer/src/typings'
import type { MappingAssistance } from '../src/renderer/src/typings/endpointDesignAssist'
import type { MappingAssistState } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useMappingAssist'
import { mappingAssistIssues, mappingAssistSections } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistReviewModel'
import { mappingRequirementsInput, mappingRequirementsKey } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistModel'
import MappingAssistQueryPreview from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistQueryPreview'
import MappingAssistPreview from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistPreview'
import MappingAssistResult from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistResult'

/** 创建真实结构的未配置目标，不按路径猜测业务规则。 */
function unmapped(path: string): WorkflowApiFieldMapping {
  return { mappingType: 'unconfigured', endpointField: { side: 'response', location: 'response_body', path, type: 'string', required: true } }
}

/** 复现截图中的两个字段、四条重复提示和 AND/OR 查询分组。 */
function reviewResult(): MappingAssistance {
  /** 用接口参数构造一条真实查询条件。 */
  const condition = (column: string, path: string): WorkflowApiDatabaseCondition => ({ kind: 'condition', sourceType: 'database', sourceId: 'db', schema: 'shop', table: 'product', column, type: 'varchar', operator: 'eq',
    right: { kind: 'endpoint', endpointField: { side: 'request', location: 'query', path, type: 'string' } } })
  return { baseline: { draftHash: 'a'.repeat(64), metadataHash: 'b'.repeat(64) }, operation: 'read', operationReason: '接口查询商品列表',
    mapping: { databaseOperation: 'read', implementationDescription: '查询商品列表。分类名称尚未配置。', databaseWrites: [],
      databaseQuery: { join: 'and', items: [{ kind: 'group', join: 'or', items: [condition('name', 'keyword'), condition('code', 'keyword')] }, condition('category_id', 'categoryId'), condition('status', 'status')] },
      fieldMappings: [unmapped('total'), unmapped('list[].categoryName')] }, reviews: [], questions: [
      '返回字段 total 的统计口径是什么？', '返回字段 categoryName 在当前表中没有来源。',
      '返回字段 total 尚未配置，请补充其取值规则。', '返回字段 list[].categoryName 尚未配置，请补充其取值规则。'
    ] }
}

/** 归并提示只影响审阅表现，具体问题及原生成结果完整保留。 */
test('四条重复提示按真实返回字段归并为两个问题项', () => {
  const result = reviewResult(), original = structuredClone(result)
  const issues = mappingAssistIssues(result)
  assert.equal(issues.length, 2)
  assert.deepEqual(issues.map((issue) => issue.path), ['total', 'list[].categoryName'])
  assert.deepEqual(issues[0].questions, ['返回字段 total 的统计口径是什么？'])
  assert.deepEqual(issues[1].questions, ['返回字段 categoryName 在当前表中没有来源。'])
  assert.deepEqual(result, original)
})

/** 同名叶字段和子串不混淆；多目标问题关联所有明确目标，未能关联的问题保留。 */
test('问题归并尊重完整路径、大小写和字段词边界', () => {
  const result = reviewResult()
  result.mapping!.fieldMappings = [unmapped('total'), unmapped('subtotal'), unmapped('list[].name'), unmapped('detail.name')]
  result.questions = ['subtotal. 如何统计？', 'name 是什么？', 'list[].name 如何处理？', 'total 和 subtotal 统计口径不同吗？', 'Total 并非当前字段名称']
  const issues = mappingAssistIssues(result)
  assert.deepEqual(issues.find((issue) => issue.path === 'total')?.questions, ['total 和 subtotal 统计口径不同吗？'])
  assert.deepEqual(issues.find((issue) => issue.path === 'subtotal')?.questions, ['subtotal. 如何统计？', 'total 和 subtotal 统计口径不同吗？'])
  assert.deepEqual(issues.find((issue) => issue.path === 'list[].name')?.questions, ['list[].name 如何处理？'])
  assert.ok(issues.some((issue) => !issue.path && issue.questions[0] === 'name 是什么？'))
  assert.ok(issues.some((issue) => !issue.path && issue.questions[0] === 'Total 并非当前字段名称'))
})

/** 已配置字段的真实问题不会被隐藏，也不能误把父路径问题当成子路径。 */
test('已配置字段问题和无法关联的信息继续可审阅', () => {
  const result = reviewResult()
  result.mapping!.fieldMappings = [{ endpointField: unmapped('price').endpointField, mappingType: 'value_mapping', right: { kind: 'fixed', value: 0 } }]
  result.questions = ['price 的单位是否为元？', 'price.extra 需要说明。', '分页规则需要确认。', '分页规则需要确认。']
  const issues = mappingAssistIssues(result)
  assert.equal(issues.length, 3)
  assert.equal(issues[0].unconfigured, false)
  assert.deepEqual(issues[0].questions, ['price 的单位是否为元？'])
})

/** 页签随本轮操作变化，条件计数包含分组成员而非顶层节点数。 */
test('CRUD 分区和条件数量来自生成结果，不受旧草稿影响', () => {
  const result = reviewResult()
  assert.equal(mappingAssistSections(result).find((item) => item.key === 'query')?.count, 4)
  for (const operation of ['create', 'read', 'update', 'delete'] as const) {
    result.mapping!.databaseOperation = operation
    const keys = mappingAssistSections(result).map((item) => item.key)
    assert.equal(keys.includes('query'), operation !== 'create')
    assert.equal(keys.includes('write'), operation === 'create' || operation === 'update')
  }
  assert.deepEqual(mappingAssistSections({ ...result, mapping: null, operation: null }), [])
})

/** 主需求与回答分开发送，字段标签与稳定排序仍完整参与结果基线。 */
test('主需求与字段补充独立序列化，稳定基线不会混淆两类输入', () => {
  const a = { label: '返回字段 total', value: ' 按筛选条件统计 ' }, b = { label: '返回字段 categoryName', value: '' }
  assert.deepEqual(mappingRequirementsInput(' 列表查询 ', ' 按名称排序 ', { b, a }), {
    instruction: '列表查询', clarification: '按名称排序\n返回字段 total：按筛选条件统计'
  })
  assert.deepEqual(JSON.parse(mappingRequirementsKey('', '', { b, a })), { instruction: '', clarification: '返回字段 total：按筛选条件统计' })
  assert.equal(mappingRequirementsKey('列表查询', '说明', { a, b }), mappingRequirementsKey('列表查询', '说明', { b, a }))
  assert.notEqual(mappingRequirementsKey('列表查询\n补充说明：说明', ''), mappingRequirementsKey('列表查询', '说明'))
})

/** 紧凑预览仍呈现四条条件与 AND/OR 关系，且只保留一组表头。 */
test('紧凑条件树不改变分组语义，也不重复表头', () => {
  const markup = renderToStaticMarkup(createElement(MappingAssistQueryPreview, { query: reviewResult().mapping!.databaseQuery }))
  assert.equal((markup.match(/mapping-review-query-head/g) || []).length, 1)
  assert.equal((markup.match(/class="mapping-review-query-row"/g) || []).length, 4)
  for (const label of ['AND', 'OR', 'name', 'code', 'category_id', 'status', 'keyword']) assert.ok(markup.includes(label))
  assert.ok(!markup.includes('checkbox'))
})

/** 返回字段能跳回待明确区，长说明保留完整原文但默认收起。 */
test('返回字段状态和完整说明在专用预览中可查', () => {
  const mapping = reviewResult().mapping!
  const markup = renderToStaticMarkup(createElement(MappingAssistPreview, { mapping, area: 'return', onShowIssues: () => {} }))
  assert.ok(markup.includes('处理 total 待明确问题'))
  assert.ok(markup.includes('处理 list[].categoryName 待明确问题'))
  const description = renderToStaticMarkup(createElement(MappingAssistPreview, { mapping, area: 'description', onShowIssues: () => {} }))
  assert.ok(description.includes('<details>'))
  assert.ok(description.includes('查询商品列表。分类名称尚未配置。'))
  assert.ok(!description.includes('<details open'))
})

/** 重新生成入口移到底部后，普通预览和操作歧义都保留同一套输入与运行保护。 */
test('补充重生成集中在底栏，未明确操作时也能继续', () => {
  const result = reviewResult()
  const assist = { phase: 'idle', instruction: '查询商品列表', clarification: '', answers: {}, requirementsLength: 12, canApply: false,
    requirementsChanged: true, apply: async () => {}, generate: async () => {}, setClarification: () => {}, setAnswer: () => {} } as unknown as MappingAssistState
  /** 读取真实组件输出中的底部动作，不复制组件的可用性判断。 */
  const renderActions = (changes: Partial<MappingAssistState> = {}, disabled = false, value = result): { markup: string; footer: string; regenerate: string } => {
    const markup = renderToStaticMarkup(createElement(MappingAssistResult, { assist: { ...assist, ...changes }, result: value, disabled }))
    const footer = markup.slice(markup.indexOf('<footer'), markup.lastIndexOf('</footer>'))
    const regenerate = [...footer.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)].find(([button]) => button.includes('补充并重新生成'))?.[0] || ''
    assert.ok(regenerate)
    assert.equal((markup.match(/补充并重新生成/g) || []).length, 1)
    return { markup, footer, regenerate }
  }
  const ready = renderActions()
  assert.ok(ready.footer.includes('替换草稿'))
  assert.ok(!/\sdisabled[=\s>]/.test(ready.regenerate))
  for (const changes of [{ requirementsChanged: false }, { instruction: '  ' }, { requirementsLength: 4001 }, { phase: 'applying' as const }]) {
    assert.match(renderActions(changes).regenerate, /\sdisabled[=\s>]/)
  }
  assert.match(renderActions({}, true).regenerate, /\sdisabled[=\s>]/)
  const ambiguous = renderActions({}, false, { ...result, mapping: null, operation: null, questions: ['物理删除还是软删除？'] })
  assert.ok(!ambiguous.footer.includes('替换草稿'))
  assert.ok(!/\sdisabled[=\s>]/.test(ambiguous.regenerate))
})

/** 分区切换、替换确认和补充失效使用真实 React 状态，初次点击不直接应用。 */
test('审阅分区与整体替换确认保持输入基线约束', async () => {
  let root!: ReactTestRenderer, applied = 0
  let result = reviewResult()
  // 本用例挂载完整映射，字段输入的生成与失效由真实 hook 用例覆盖。
  result.questions = []
  result.mapping!.fieldMappings = result.mapping!.fieldMappings.map((item) => ({ endpointField: item.endpointField, mappingType: 'value_mapping', right: { kind: 'fixed', value: 0 } }))
  let assist = { phase: 'idle', instruction: '查询商品列表', clarification: '', answers: {}, requirementsLength: 6, canApply: true,
    requirementsChanged: false, apply: async () => { applied++ }, generate: async () => {}, setClarification: () => {}, setAnswer: () => {} } as unknown as MappingAssistState
  /** 挂载生产结果视图，不模拟组件内部的分区或确认状态。 */
  const render = (): ReactElement => createElement(MappingAssistResult, { assist, result, disabled: false })
  /** 根据当前分区导航切换审阅区域。 */
  const select = (index: number): void => root.root.findByType('nav').findAllByType('button')[index].props.onClick()
  /** 查找带有确切可见文本的原生按钮。 */
  const button = (text: string) => root.root.findAllByType('button').find((node) => node.findAll((child) => child.type === 'span' && child.children.join('') === text).length)!
  try {
    await act(async () => { root = create(render()) })
    await act(async () => select(2))
    assert.ok(root.root.findByProps({ 'aria-label': '生成的返回字段' }))
    await act(async () => select(3))
    assert.ok(root.root.findByProps({ 'aria-label': '生成的映射说明' }))
    await act(async () => button('替换草稿').props.onClick())
    assert.equal(applied, 0)
    assert.ok(root.root.findByProps({ role: 'alertdialog' }))
    await act(async () => { result = { ...result, mapping: { ...result.mapping!, implementationDescription: '新一轮生成的说明' } }; root.update(render()) })
    assert.equal(root.root.findAllByProps({ role: 'alertdialog' }).length, 0)
    assert.ok(root.root.findByProps({ 'aria-label': '生成的查询条件' }))
    await act(async () => button('替换草稿').props.onClick())
    await act(async () => { assist = { ...assist, canApply: false, requirementsChanged: true, answers: { total: { label: '返回字段 total', value: '新要求' } } }; root.update(render()) })
    assert.equal(root.root.findAllByProps({ role: 'alertdialog' }).length, 0)
    assert.equal(button('替换草稿').props.disabled, true)
    await act(async () => { assist = { ...assist, canApply: true, requirementsChanged: false }; root.update(render()) })
    await act(async () => button('替换草稿').props.onClick())
    await act(async () => button('确认替换草稿').props.onClick())
    assert.equal(applied, 1)
  } finally { await act(async () => root?.unmount()) }
})
