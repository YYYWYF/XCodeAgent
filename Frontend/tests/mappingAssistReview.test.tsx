import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement, type ReactElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { act, create, type ReactTestRenderer } from 'react-test-renderer'
import type { WorkflowApiDatabaseCondition, WorkflowApiFieldMapping } from '../src/renderer/src/typings'
import type { MappingAssistance } from '../src/renderer/src/typings/endpointDesignAssist'
import type { MappingAssistState } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/useMappingAssist'
import { mappingAssistIssues } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistReviewModel'
import { mappingRequirementsInput, mappingRequirementsKey } from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/mappingAssistModel'
import DatabaseMapping from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/DatabaseMapping'
import MappingAssistResult from '../src/renderer/src/components/AiChatPanel/components/FieldMapping/MappingAssistResult'
import { installMappingDomConstructors } from './mappingAssistTestHarness'

/** 创建真实结构的未配置目标，不按路径猜测业务规则。 */
function unmapped(path: string): WorkflowApiFieldMapping {
  return { mappingType: 'unconfigured', endpointField: { side: 'response', location: 'response_body', path, type: 'string', required: true } }
}

/** 构造两个未配置字段、带目标的问题和 AND/OR 查询分组。 */
function reviewResult(): MappingAssistance {
  /** 用接口参数构造一条真实查询条件。 */
  const condition = (column: string, path: string): WorkflowApiDatabaseCondition => ({ kind: 'condition', sourceType: 'database', sourceId: 'db', schema: 'shop', table: 'product', column, type: 'varchar', operator: 'eq',
    right: { kind: 'endpoint', endpointField: { side: 'request', location: 'query', path, type: 'string' } } })
  return { baseline: { draftHash: 'a'.repeat(64), metadataHash: 'b'.repeat(64) }, operation: 'read', operationReason: '接口查询商品列表',
    mapping: { databaseOperation: 'read', implementationDescription: '查询商品列表。分类名称尚未配置。', databaseWrites: [],
      databaseQuery: { join: 'and', items: [{ kind: 'group', join: 'or', items: [condition('name', 'keyword'), condition('code', 'keyword')] }, condition('category_id', 'categoryId'), condition('status', 'status')] },
      fieldMappings: [unmapped('total'), unmapped('list[].categoryName')] }, reviews: [], questions: [
      { target: 'response:response_body:total', text: '商品总数的统计口径是什么？' },
      { target: 'response:response_body:list[].categoryName', text: '分类名称在当前表中没有来源。' }
    ] }
}

/** 归并提示只影响审阅表现，具体问题及原生成结果完整保留。 */
test('业务问题和未配置状态共用字段卡片，不额外添加占位问题', () => {
  const result = reviewResult(), original = structuredClone(result)
  const issues = mappingAssistIssues(result)
  assert.equal(issues.length, 2)
  assert.deepEqual(issues.map((issue) => issue.path), ['total', 'list[].categoryName'])
  assert.deepEqual(issues[0].questions, ['商品总数的统计口径是什么？'])
  assert.deepEqual(issues[1].questions, ['分类名称在当前表中没有来源。'])
  assert.deepEqual(result, original)
})

/** 先关联完整字段身份，再以契约里的业务名称展示问题。 */
test('问题正文隐藏技术引用前缀并保留字段关联', () => {
  const result = reviewResult(), item = unmapped('code')
  item.endpointField.description = '商品编码'
  result.mapping!.fieldMappings = [item]
  result.questions = [{ target: 'response:response_body:code', text: 'response:response_body:code 应从哪里取值？' }]
  const [issue] = mappingAssistIssues(result)
  assert.equal(issue.key, 'response:response_body:code')
  assert.equal(issue.label, '返回字段 code')
  assert.deepEqual(issue.questions, ['商品编码 应从哪里取值？'])
})

/** 自然语言出现同名字段时，身份仍只由 target 决定。 */
test('问题通过完整目标区分同名字段，通用问题保持独立', () => {
  const result = reviewResult()
  result.mapping!.fieldMappings = [unmapped('list[].name'), unmapped('detail.name')]
  result.questions = [
    { target: 'response:response_body:list[].name', text: '列表名称与详情名称的取值规则一样吗？' },
    { target: null, text: 'list[].name 和 detail.name 是否应同时返回？' }
  ]
  const issues = mappingAssistIssues(result)
  assert.deepEqual(issues.find((issue) => issue.path === 'list[].name')?.questions, ['列表名称与详情名称的取值规则一样吗？'])
  assert.deepEqual(issues.find((issue) => issue.path === 'detail.name')?.questions, ['请补充这个字段的取值或业务规则。'])
  assert.ok(issues.some((issue) => !issue.path && issue.questions[0] === 'list[].name 和 detail.name 是否应同时返回？'))
})

/** 已配置字段的真实问题不会被隐藏，也不能误把父路径问题当成子路径。 */
test('已配置字段问题和无法关联的信息继续可审阅', () => {
  const result = reviewResult()
  result.mapping!.fieldMappings = [{ endpointField: unmapped('price').endpointField, mappingType: 'value_mapping', right: { kind: 'fixed', value: 0 } }]
  result.questions = [
    { target: 'response:response_body:price', text: '售价的单位是否为元？' },
    { target: null, text: 'price.extra 需要说明。' },
    { target: null, text: '分页规则需要确认。' },
    { target: null, text: '分页规则需要确认。' }
  ]
  const issues = mappingAssistIssues(result)
  assert.equal(issues.length, 3)
  assert.equal(issues[0].unconfigured, false)
  assert.deepEqual(issues[0].questions, ['售价的单位是否为元？'])
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

/** 原映射卡片在覆盖预览中直接显示完整配置，无编辑入口。 */
test('只读覆盖同时展示原查询、返回和说明卡片', () => {
  const mapping = reviewResult().mapping!
  const markup = renderToStaticMarkup(createElement(DatabaseMapping, {
    selection: { sourceType: 'database', sourceId: 'db', schema: 'shop', table: 'product' },
    draft: { ...mapping, apiContractId: 'products', endpointId: 'list', databaseQuery: mapping.databaseQuery || undefined },
    operation: 'read', fields: mapping.fieldMappings.map((item) => item.endpointField), readOnly: true,
    editable: false, busy: false, errors: {}, onChange: () => {}, onOperationChange: () => {}
  }))
  assert.equal((markup.match(/class="database-query-row is-readonly"/g) || []).length, 4)
  for (const label of ['AND', 'OR', 'name', 'code', 'category_id', 'status', 'keyword', 'total', 'list[].categoryName']) assert.ok(markup.includes(label))
  assert.equal((markup.match(/查询商品列表。分类名称尚未配置。/g) || []).length, 1)
  assert.doesNotMatch(markup, /添加条件|写入字段|<textarea/)
})

/** 重新生成入口移到底部后，普通预览和操作歧义都保留同一套输入与运行保护。 */
test('补充重生成集中在底栏，未明确操作时也能继续', () => {
  const result = reviewResult()
  const assist = { phase: 'idle', instruction: '查询商品列表', clarification: '', answers: {}, requirementsLength: 12, canApply: false,
    requirementsChanged: true, apply: async () => {}, close: () => {}, generate: async () => {}, setClarification: () => {}, setAnswer: () => {} } as unknown as MappingAssistState
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
  assert.ok(ready.footer.includes('aria-label="采纳 AI 映射"'))
  assert.ok(ready.footer.includes('不采纳'))
  assert.ok(!/\sdisabled[=\s>]/.test(ready.regenerate))
  assert.doesNotMatch(renderActions({ instruction: '  ' }).regenerate, /\sdisabled[=\s>]/)
  for (const changes of [{ requirementsChanged: false }, { requirementsLength: 4001 }, { phase: 'applying' as const }]) {
    assert.match(renderActions(changes).regenerate, /\sdisabled[=\s>]/)
  }
  assert.match(renderActions({}, true).regenerate, /\sdisabled[=\s>]/)
  const ambiguous = renderActions({}, false, { ...result, mapping: null, operation: null, questions: [{ target: null, text: '物理删除还是软删除？' }] })
  assert.ok(!ambiguous.footer.includes('aria-label="采纳 AI 映射"'))
  assert.ok(!/\sdisabled[=\s>]/.test(ambiguous.regenerate))
})

/** 问题按类型聚合，用户可以明确采纳或放弃当前候选。 */
test('同类问题共用卡片，采纳和放弃是两个明确操作', async () => {
  const restoreDomConstructors = installMappingDomConstructors()
  let root!: ReactTestRenderer, applied = 0, dismissed = 0
  const result = reviewResult()
  result.questions.push({ target: 'response:response_body:total', text: '结果为空时需要返回零吗？' })
  let assist = { phase: 'idle', instruction: '查询商品列表', clarification: '', answers: {}, requirementsLength: 6, canApply: true,
    requirementsChanged: false, apply: async () => { applied++ }, close: () => { dismissed++ },
    generate: async () => {}, setClarification: () => {}, setAnswer: () => {} } as unknown as MappingAssistState
  /** 挂载生产结果视图，检查实际问题和决策按钮。 */
  const render = (): ReactElement => createElement(MappingAssistResult, { assist, result, disabled: false })
  /** 查找带有确切可见文本的原生按钮。 */
  const button = (text: string) => root.root.findAllByType('button').find((node) => node.props['aria-label'] === text
    || node.findAll((child) => child.type === 'span' && child.children.join('') === text).length)!
  try {
    await act(async () => { root = create(render()) })
    assert.equal(root.root.findAllByType('nav').length, 0)
    assert.equal(root.root.findAllByProps({ 'aria-label': '返回字段' }).length, 1)
    assert.equal(root.root.findByType('ol').findAllByType('li').length, 2)
    await act(async () => button('不采纳').props.onClick())
    assert.equal(dismissed, 1)
    assert.equal(applied, 0)
    await act(async () => { assist = { ...assist, canApply: false, requirementsChanged: true, answers: { total: { label: '返回字段 total', value: '新要求' } } }; root.update(render()) })
    assert.equal(button('采纳 AI 映射').props.disabled, true)
    await act(async () => { assist = { ...assist, canApply: true, requirementsChanged: false }; root.update(render()) })
    await act(async () => button('采纳 AI 映射').props.onClick())
    assert.equal(applied, 1)
  } finally {
    try { await act(async () => root?.unmount()) }
    finally { restoreDomConstructors() }
  }
})
