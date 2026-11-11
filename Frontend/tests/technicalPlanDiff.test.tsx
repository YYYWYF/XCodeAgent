import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { technicalPlanDiff } from '../src/renderer/src/components/AiChatPanel/components/DocPanel/technicalPlanDiff'
import TechnicalPlanReviewPanel from '../src/renderer/src/components/AiChatPanel/components/DocPanel/TechnicalPlanReviewPanel'

/** 构造包含共享类型和同名不同位置参数的实际 TechnicalPlan 结构。 */
function plan(): Record<string, any> {
  return {
    architecture: { frontend: 'React', backend: 'Spring', data: 'MySQL' },
    confirmation_status: 'confirmed',
    entities: [
      {
        id: 'task',
        name: '任务',
        fields: [
          { name: 'title', type: 'string', required: false },
          { name: 'status', type: 'string' }
        ]
      }
    ],
    api_contracts: [
      {
        id: 'tasks',
        name: '任务接口',
        schemas: {
          Task: {
            type: 'object',
            properties: { title: { type: 'string' }, nested: { $ref: 'Task' } },
            required: ['title']
          }
        },
        endpoints: [
          {
            id: 'list',
            name: '任务列表',
            method: 'GET',
            path: '/tasks',
            response_schema_ref: 'Task',
            parameters: [
              { name: 'id', in: 'path', required: true },
              { name: 'id', in: 'query', required: false }
            ]
          },
          {
            id: 'detail',
            name: '任务详情',
            method: 'GET',
            path: '/tasks/{id}',
            response_schema_ref: 'Task'
          }
        ]
      }
    ],
    pages: [{ pageId: 'tasks', references: { endpoint_dependencies: [{ endpoint_id: 'list' }] } }],
    authorization_manifest: { fingerprint: 'old', resources: [], bindings: { endpoints: [] } }
  }
}

test('确认事实、摘要、对象键与有身份列表的重排不产生业务差异', () => {
  const before = plan(),
    after = plan()
  after.confirmation_status = 'pending_user_confirmation'
  after.authorization_manifest.fingerprint = 'new'
  after.entities[0].fields.reverse()
  after.api_contracts[0].endpoints.reverse()
  after.architecture = { data: 'MySQL', backend: 'Spring', frontend: 'React' }
  assert.ok(technicalPlanDiff(before, after, {}).every((group) => group.objects.length === 0))
})

test('同一实体的多字段变化按一个对象统计，删除与新增保留各自真实内容', () => {
  const before = plan(),
    after = plan()
  after.entities[0].fields[0].required = true
  after.entities[0].fields[0].type = 'text'
  after.entities[0].fields.pop()
  after.entities.push({ id: 'comment', name: '评论', fields: [] })
  const entities = technicalPlanDiff(before, after, {})[1].objects
  assert.equal(entities.length, 2)
  assert.equal(entities[0].kind, '修改')
  assert.ok(
    entities[0].differences.some(
      (change) => change.path.includes('status') && change.after === undefined
    )
  )
  assert.equal(entities[1].kind, '新增')
  assert.ok(entities[1].differences.some((change) => change.after === '评论'))
})

test('共享响应类型变化标出全部受影响接口，循环引用可终止', () => {
  const before = plan(),
    after = plan()
  after.api_contracts[0].schemas.Task.properties.title.maxLength = 120
  const objects = technicalPlanDiff(before, after, {})[2].objects
  assert.equal(objects.length, 2)
  assert.ok(objects.every((item) => item.id.startsWith('endpoint:')))
  assert.ok(
    objects.every((item) =>
      item.differences[0].path.includes(
        '响应体（responseBody） → Task → 字段（properties） → title → 最长长度（maxLength）'
      )
    )
  )
})

test('类型由未引用变成被引用不伪报类型删除，集合顺序不影响差异', () => {
  const before = plan(),
    after = plan()
  before.api_contracts[0].endpoints = []
  after.api_contracts[0].schemas.Task.required = ['title']
  const objects = technicalPlanDiff(before, after, {})[2].objects
  assert.equal(objects.length, 2)
  assert.ok(objects.every((item) => item.kind === '新增' && item.id.startsWith('endpoint:')))
})

test('同名参数按位置匹配；仅名称变化且唯一的实体字段合并成一处名称变更', () => {
  const before = plan(),
    after = plan()
  after.api_contracts[0].endpoints[0].parameters[1].required = true
  after.entities[0].fields[0].name = 'subject'
  const groups = technicalPlanDiff(before, after, {})
  assert.match(groups[2].objects[0].differences[0].path, /query.id/)
  const changes = groups[1].objects[0].differences
  assert.equal(changes.length, 1)
  assert.equal(changes[0].kind, 'rename')
  assert.equal(changes[0].before, 'title')
  assert.equal(changes[0].after, 'subject')
  const markup = renderToStaticMarkup(
    createElement(TechnicalPlanReviewPanel, {
      plan: after,
      productPlan: {},
      baseline: before
    })
  )
  assert.match(markup, /名称变更/)
  assert.match(markup, /<code>title<\/code>/)
  assert.match(markup, /<code>subject<\/code>/)
  assert.match(markup, /其余属性未变/)
  assert.doesNotMatch(markup, /&quot;name&quot;:|&quot;type&quot;:/)
})

test('嵌套 JSON Schema 的字段 key 改名合并展示，未变属性不重复展开', () => {
  const before = plan(),
    after = plan()
  after.api_contracts[0].schemas.Task.properties.subject =
    after.api_contracts[0].schemas.Task.properties.title
  delete after.api_contracts[0].schemas.Task.properties.title
  const objects = technicalPlanDiff(before, after, {})[2].objects
  assert.equal(objects.length, 2)
  for (const object of objects) {
    assert.equal(object.differences.length, 1)
    assert.equal(object.differences[0].kind, 'rename')
    assert.equal(object.differences[0].before, 'title')
    assert.equal(object.differences[0].after, 'subject')
    assert.match(object.differences[0].path, /响应体.*字段（properties）/)
  }
})

test('API 字段及同名实体引用同步改名时合并名称，仍展示引用变化', () => {
  const before = plan(),
    after = plan()
  before.api_contracts[0].schemas.Task.properties.title.entity_field_ref = 'task.title'
  after.api_contracts[0].schemas.Task.properties.subject = {
    type: 'string',
    entity_field_ref: 'task.subject'
  }
  delete after.api_contracts[0].schemas.Task.properties.title
  let changes = technicalPlanDiff(before, after, {})[2].objects[0].differences
  assert.equal(changes.length, 2)
  assert.equal(changes[0].kind, 'rename')
  assert.equal(changes[0].unchanged, false)
  assert.equal(changes[1].field, 'entity_field_ref')
  assert.equal(changes[1].before, 'task.title')
  assert.equal(changes[1].after, 'task.subject')
  after.api_contracts[0].schemas.Task.properties.subject.entity_field_ref = 'other.subject'
  changes = technicalPlanDiff(before, after, {})[2].objects[0].differences
  assert.ok(changes.every((change) => change.kind !== 'rename'))
})

test('改名同时变更属性或匹配不唯一时保留增删，不隐藏实际变化', () => {
  const before = plan(),
    after = plan()
  after.entities[0].fields[0] = { name: 'subject', type: 'text', required: false }
  let changes = technicalPlanDiff(before, after, {})[1].objects[0].differences
  assert.ok(changes.every((change) => change.kind !== 'rename'))
  assert.ok(changes.some((change) => change.after === 'text'))
  before.entities[0].fields = [
    { name: 'a', type: 'string' },
    { name: 'b', type: 'string' }
  ]
  after.entities[0].fields = [
    { name: 'c', type: 'string' },
    { name: 'd', type: 'string' }
  ]
  changes = technicalPlanDiff(before, after, {})[1].objects[0].differences
  assert.ok(changes.every((change) => change.kind !== 'rename'))
  assert.ok(changes.some((change) => change.before === 'a' && change.after === undefined))
  assert.ok(changes.some((change) => change.after === 'd' && change.before === undefined))
})

test('多个可唯一对应的改名分别合并，其他字段属性变化照常保留', () => {
  const before = plan(),
    after = plan()
  before.entities[0].fields.push({ name: 'enabled', type: 'boolean' })
  after.entities[0].fields.push({ name: 'active', type: 'boolean' })
  after.entities[0].fields[0].name = 'subject'
  after.entities[0].fields[1].required = true
  const changes = technicalPlanDiff(before, after, {})[1].objects[0].differences
  assert.equal(changes.filter((change) => change.kind === 'rename').length, 2)
  assert.equal(changes.length, 3)
  assert.ok(changes.some((change) => change.path.includes('status') && change.after === true))
})

test('修改审阅保留上方中英文说明，下方用代码展示属性名和实际值', () => {
  const before = plan(),
    after = plan()
  before.api_contracts[0].endpoints[0].parameters[1].schema = {
    description: '按编号搜索',
    type: 'string'
  }
  after.api_contracts[0].endpoints[0].parameters[1].schema = { minLength: 1, type: 'string' }
  const markup = renderToStaticMarkup(
    createElement(TechnicalPlanReviewPanel, {
      plan: after,
      productPlan: {},
      baseline: before
    })
  )
  assert.match(
    markup,
    /请求参数（parameters） → query.id → 类型定义（schema） → 说明（description）/
  )
  assert.match(markup, /最短长度（minLength）/)
  assert.match(markup, /删除/)
  assert.match(markup, /<code>&quot;description&quot;: &quot;按编号搜索&quot;<\/code>/)
  assert.match(markup, /新增/)
  assert.match(markup, /<code>&quot;minLength&quot;: 1<\/code>/)
  assert.doesNotMatch(markup, /片段行号|代码差异|<table/)
})

test('页面绑定显示产品页面名称，连续两轮比较不会累积上一轮改动', () => {
  const first = plan(),
    second = plan(),
    third = plan()
  second.entities[0].fields[0].required = true
  third.entities[0].fields[0].required = true
  third.pages[0].references.endpoint_dependencies = []
  const groups = technicalPlanDiff(second, third, {
    pages: [{ pageId: 'tasks', name: '任务管理' }]
  })
  assert.equal(groups[1].objects.length, 0)
  assert.equal(groups[3].objects[0].name, '任务管理')
  assert.equal(
    technicalPlanDiff(first, third, {}).filter((group) => group.objects.length).length,
    2
  )
})

test('首次生成只展示完整规划，已有基线默认展示差异与两个阅读入口', () => {
  const before = plan(),
    after = plan()
  after.entities[0].fields[0].required = true
  const first = renderToStaticMarkup(
    createElement(TechnicalPlanReviewPanel, { plan: after, productPlan: {} })
  )
  assert.doesNotMatch(first, /技术规划本次变更/)
  const changed = renderToStaticMarkup(
    createElement(TechnicalPlanReviewPanel, { plan: after, productPlan: {}, baseline: before })
  )
  assert.match(changed, /技术规划本次变更/)
  assert.match(changed, /本次变更/)
  assert.match(changed, /完整规划/)
  assert.match(changed, /修改前/)
  assert.match(changed, /<code>&quot;required&quot;: true<\/code>/)
})

test('无变化时展示说明，生成中保留旧版且不显示提示卡和未完成的差异', () => {
  const before = plan()
  const empty = renderToStaticMarkup(
    createElement(TechnicalPlanReviewPanel, { plan: before, productPlan: {}, baseline: before })
  )
  assert.match(empty, /本次未产生内容变化/)
  const running = renderToStaticMarkup(
    createElement(TechnicalPlanReviewPanel, {
      plan: before,
      productPlan: {},
      baseline: before,
      updating: true
    })
  )
  assert.doesNotMatch(running, /正在修改技术规划|下方保留修改前的内容/)
  assert.match(running, /API 契约/)
  assert.doesNotMatch(running, /技术规划本次变更/)
})
