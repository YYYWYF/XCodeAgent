import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkflowRunCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import { WORKBENCH_PHASE_AGENTS } from '../src/renderer/src/workbenchPhase'

/** 用当前公开状态构造截图中的空失败审阅卡，不注入模拟用户授权。 */
function planningInteraction(mode: string): WorkflowRunPayload {
  return { runId: 'feedback-run', threadId: 'feedback-thread', events: [], summary: {
    phase: 'technical_planning', status: 'requires_user_input',
    message: '当前应用规划正在等待你的确认。',
    clarification: { mode, status: 'requires_user_input', questions: [] }
  } }
}

/** 渲染真实卡片分派和上下文，检查历史消费状态不能变成当前确认。 */
function renderInteraction(workflow: WorkflowRunPayload, options: {
  availability?: 'active' | 'stale' | 'unavailable'
  result?: 'submitted' | 'confirmed' | 'cancelled'
  answers?: Record<string, string>
} = {}): string {
  return renderToStaticMarkup(createElement(WorkbenchPhaseContext.Provider, { value: {
    phase: 'planning', derivedPhase: 'planning', reachedPhase: 'planning', manualOverride: null,
    agent: WORKBENCH_PHASE_AGENTS.planning, recordReachedPhase: () => {},
    switchPhase: () => {}, canEdit: () => false
  } }, createElement(WorkflowRunCard, {
    workflow, interactionAvailability: options.availability || 'active',
    workflowCardResult: options.result, historicalClarificationAnswers: options.answers
  })))
}

// 截图回归：重新生成已提交，但原始快照依然声称需要输入且没有问题。
test('已提交的空生成失败卡只显示回执，不显示矛盾的等待说明和按钮', () => {
  const markup = renderInteraction(planningInteraction('technical_plan_generation_error'), { result: 'submitted' })
  assert.match(markup, /重新生成请求已提交/)
  assert.doesNotMatch(markup, /<button|待确认事项|确认并继续|等待你的确认|workflow-run-card/)
})

// 当前失败是重新生成决策，不能冒充技术规划正式产物确认。
test('当前技术规划失败明确说明没有可确认内容，唯一操作是重新生成', () => {
  const markup = renderInteraction(planningInteraction('technical_plan_generation_error'))
  assert.match(markup, /技术规划生成未完成/)
  assert.match(markup, /还没有可供确认的内容/)
  assert.match(markup, /重新生成技术规划/)
  assert.equal((markup.match(/<button/g) || []).length, 1)
  assert.doesNotMatch(markup, /待确认事项|确认并继续|确认技术规划|等待你的确认/)
})

// 同步未完成必须守住既有交互资格，不能因为换文案而放开动作。
test('未同步的失败卡禁用重新生成，过期失败卡仅展示历史记录', () => {
  const workflow = planningInteraction('technical_plan_generation_error')
  const unavailable = renderInteraction(workflow, { availability: 'unavailable' })
  assert.match(unavailable, /<button[^>]*disabled/)
  const stale = renderInteraction(workflow, { availability: 'stale' })
  assert.match(stale, /此前的技术规划记录/)
  assert.doesNotMatch(stale, /<button|等待你的确认/)
})

// 新产物仍有原确认操作，只增加可理解的范围和结果说明。
test('技术规划审阅展示当前范围、核对对象与明确的确认后果', () => {
  const workflow = planningInteraction('technical_plan_confirmation')
  workflow.state = { technical_plan: {
    pages: Array.from({ length: 8 }, () => ({})), entities: Array.from({ length: 9 }, () => ({})),
    api_contracts: [{ endpoints: Array.from({ length: 40 }, () => ({})) }]
  } }
  const markup = renderInteraction(workflow)
  assert.match(markup, /作为后续开发依据/)
  assert.match(markup, /接口定义及页面操作绑定/)
  assert.match(markup, /确认后进入开发准备/)
  assert.match(markup, /<strong>8<\/strong>个页面/)
  assert.match(markup, /<strong>9<\/strong>个数据实体/)
  assert.match(markup, /<strong>40<\/strong>个接口/)
  assert.match(markup, /提出修改/)
  assert.match(markup, /确认技术规划/)
  assert.doesNotMatch(markup, /待确认事项|工作流执行|确认保存/)
})

// 未加载资料不应把未知范围伪装成真实零项。
test('资料尚未提供时不显示虚假的零范围，正式确认门仍保留', () => {
  const markup = renderInteraction(planningInteraction('technical_plan_confirmation'))
  assert.doesNotMatch(markup, /本次技术规划范围|待确认事项/)
  assert.match(markup, /确认技术规划/)
})

// 用户提交或新版本替换后，旧技术规划不再要求重复确认。
test('已提交和失效的技术规划不保留旧审阅说明与禁用确认按钮', () => {
  for (const options of [{ result: 'submitted' as const }, { availability: 'stale' as const }]) {
    const markup = renderInteraction(planningInteraction('technical_plan_confirmation'), options)
    assert.doesNotMatch(markup, /<button|请审阅这份技术规划|等待你的确认/)
  }
})

// 真实澄清保留原问题及历史答案，不能被空确认修复误删。
test('真实补充问题保留表单和答案，历史回答不出现再次提交操作', () => {
  const workflow = planningInteraction('requirements_clarification')
  workflow.summary.clarification!.questions = [{ id: 'scope', header: '用户范围', question: '哪些人使用？', type: 'text' }]
  const active = renderInteraction(workflow)
  assert.match(active, /待补充信息|哪些人使用/)
  assert.match(active, /提交补充信息/)
  const historical = renderInteraction(workflow, { availability: 'stale', answers: { 用户范围: '内部员工' } })
  assert.match(historical, /内部员工/)
  assert.doesNotMatch(historical, /提交补充信息/)
})
