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

test('同名参数按位置匹配；字段改名不臆测为同一字段', () => {
  const before = plan(),
    after = plan()
  after.api_contracts[0].endpoints[0].parameters[1].required = true
  after.entities[0].fields[0].name = 'subject'
  const groups = technicalPlanDiff(before, after, {})
  assert.match(groups[2].objects[0].differences[0].path, /query.id/)
  const changes = groups[1].objects[0].differences
  assert.ok(changes.some((change) => change.path.includes('title') && change.after === undefined))
  assert.ok(
    changes.some((change) => change.path.includes('subject') && change.before === undefined)
  )
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

test('无变化与生成中状态明确，生成中保留旧版且不显示尚未完成的差异', () => {
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
  assert.match(running, /正在修改技术规划/)
  assert.match(running, /API 契约/)
  assert.doesNotMatch(running, /技术规划本次变更/)
})
