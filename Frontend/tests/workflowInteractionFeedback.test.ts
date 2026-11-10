import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkflowRunCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard'
import UiDesignConfirmationPanel from '../src/renderer/src/components/Welcome/UiDesignConfirmationPanel'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import { WORKBENCH_PHASE_AGENTS } from '../src/renderer/src/workbenchPhase'
import {
  globalFallbackState,
  isTechnicalPlanGenerationFailure,
  technicalPlanGenerationFailureMessage
} from '../src/renderer/src/components/AiChatPanel/globalFallbackState'

/** 用当前公开状态构造截图中的空失败审阅卡，不注入模拟用户授权。 */
function planningInteraction(mode: string): WorkflowRunPayload {
  return { runId: 'feedback-run', threadId: 'feedback-thread', events: [], summary: {
    phase: 'technical_planning', status: 'requires_user_input',
    message: '当前应用规划正在等待你的确认。',
    clarification: { mode, status: 'requires_user_input', questions: [] }
  } }
}

test('UI 设计仅在处理期间禁用下一步，未生成、失败或取消不要求补齐', () => {
  for (const status of ['pending', 'queued', 'generating', 'stopping', 'generation_failed', 'cancelled', 'confirmed']) {
    const workflow: WorkflowRunPayload = { runId: 'ui-run', threadId: 'ui-thread', events: [], summary: {
      status: 'requires_user_input', phase: 'ui_confirmation', clarification: {
        mode: 'ui_design_confirmation', pages: [{ pageId: 'home', name: '首页', status }]
      }
    } }
    const markup = renderToStaticMarkup(createElement(UiDesignConfirmationPanel, {
      workflow, onSubmit: () => undefined
    }))
    const next = [...markup.matchAll(/<button\b[^>]*>[\s\S]*?<\/button>/g)]
      .find(([button]) => button.includes('下一步'))?.[0]
    assert.ok(next)
    assert.equal(/\bdisabled/.test(next), ['queued', 'generating', 'stopping'].includes(status), status)
    assert.doesNotMatch(markup, /请先完成全部页面设计稿/)
  }
})

test('旧设计代码不能把当前待生成或失败页面显示成绿色已生成', () => {
  for (const status of ['pending', 'generation_failed', 'cancelled']) {
    const workflow: WorkflowRunPayload = { runId: 'revision', threadId: 'thread', events: [], summary: {
      status: 'requires_user_input', phase: 'ui_confirmation', clarification: {
        mode: 'ui_design_confirmation', pages: [{ pageId: 'notice', name: '公告', status, code: 'export default () => null' }]
      }
    } }
    const markup = renderToStaticMarkup(createElement(UiDesignConfirmationPanel, { workflow, showPreview: false, onSubmit: () => undefined }))
    assert.doesNotMatch(markup, /ui-design-page-row-status is-confirmed/)
    assert.match(markup, status === 'pending' ? /待重新生成/ : status === 'generation_failed' ? /生成失败/ : /已停止/)
  }
})

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
    workflow, interactionAvailability: options.availability || 'active', disabled: Boolean(options.result),
    workflowCardResult: options.result, historicalClarificationAnswers: options.answers,
    // 测试仅提供现有回调入口，不提交产品请求或模拟正式确认。
    onSubmitClarification: () => {}
  })))
}

// 生成错误交给底部区域，历史中也不额外创建自定义回执。
test('已提交的空生成失败快照不显示重复卡片和回执', () => {
  const markup = renderInteraction(planningInteraction('technical_plan_generation_error'), { result: 'submitted' })
  assert.equal(markup, '')
})

// 当前失败只由底部承载，消息区即使直接分派也不能再显示错误卡。
test('当前技术规划失败不在消息区渲染卡片', () => {
  const markup = renderInteraction(planningInteraction('technical_plan_generation_error'))
  assert.equal(markup, '')
})

// 原截图的错误原因保留，内部片段哈希不能进入正文或复制的诊断内容。
test('技术规划错误卡只保留可读原因，不展示片段哈希或堆栈', () => {
  const workflow = planningInteraction('technical_plan_generation_error')
  const hash = '19637f54bf3b183d4ef37c288296b2fa795b300c71bb1ef77ce4f0760065de30'
  workflow.summary.clarification!.errors = [
    `技术规划片段 catalog-part:${hash}:endpoints:${hash} 校验失败：技术规划 Endpoint ID 存在重复值。`,
    'Traceback: /Users/example/project/plan.py'
  ]
  const message = technicalPlanGenerationFailureMessage(workflow)!
  assert.match(message, /接口标识存在重复值/)
  assert.doesNotMatch(message, /catalog-part|19637f54|Traceback|plan\.py/)
})

// 校验失败进入原底部兜底，真正运行和正式确认都不借用该失败入口。
test('技术生成校验失败显示原底部兜底，保留原生门禁状态且不污染新运行', () => {
  const workflow = planningInteraction('technical_plan_generation_error')
  const original = JSON.stringify(workflow)
  const error = technicalPlanGenerationFailureMessage(workflow)
  assert.equal(globalFallbackState({ connectionStatus: 'healthy', hasRecoveryIncident: false,
    globalFallbackError: error }).visible, true)
  assert.equal(JSON.stringify(workflow), original)
  assert.equal(isTechnicalPlanGenerationFailure({ ...workflow,
    summary: { ...workflow.summary, status: 'running' } }), false)
  assert.equal(technicalPlanGenerationFailureMessage({ ...workflow,
    summary: { ...workflow.summary, status: 'running' } }), undefined)
  assert.equal(technicalPlanGenerationFailureMessage(planningInteraction('technical_plan_confirmation')), undefined)
})

// 同步未完成必须守住既有交互资格，不能因为换文案而放开动作。
test('未同步或过期的失败不产生消息区按钮和自定义回执', () => {
  const workflow = planningInteraction('technical_plan_generation_error')
  const unavailable = renderInteraction(workflow, { availability: 'unavailable' })
  assert.equal(unavailable, '')
  const stale = renderInteraction(workflow, { availability: 'stale' })
  assert.equal(stale, '')
})

// 恢复原确认卡的布局与文案，保持原有业务动作和资料入口。
test('技术规划审阅沿用原确认卡，不再增加范围区和改写按钮', () => {
  const workflow = planningInteraction('technical_plan_confirmation')
  workflow.state = { technical_plan: {
    pages: Array.from({ length: 8 }, () => ({})), entities: Array.from({ length: 9 }, () => ({})),
    api_contracts: [{ endpoints: Array.from({ length: 40 }, () => ({})) }]
  } }
  const markup = renderInteraction(workflow)
  assert.match(markup, /技术规划已生成，已展示在右侧/)
  assert.match(markup, /请在右侧查看内容/)
  assert.match(markup, /工作流执行/)
  assert.match(markup, /修\s*改/)
  assert.match(markup, /确认保存/)
  assert.doesNotMatch(markup, /technical-plan-review-scope|workflow-run-card-planning-review|<span>提出修改<\/span>|确认技术规划/)
})

// 未加载资料不应把未知范围伪装成真实零项。
test('资料尚未提供时不显示虚假的零范围，正式确认门仍保留', () => {
  const markup = renderInteraction(planningInteraction('technical_plan_confirmation'))
  assert.doesNotMatch(markup, /本次技术规划范围|待确认事项/)
  assert.match(markup, /确认保存/)
})

// 历史确认保持原卡片及只读资格，不能再被替换为新回执样式。
test('历史技术规划和阶段入口保留原卡片，已消费或过期按钮保持只读', () => {
  for (const mode of ['technical_plan_confirmation', 'planning_stage_entry_confirmation']) {
    for (const options of [{ result: 'submitted' as const }, { result: 'confirmed' as const }, { availability: 'stale' as const }]) {
      const markup = renderInteraction(planningInteraction(mode), options)
      assert.match(markup, /workflow-run-card/)
      assert.doesNotMatch(markup, /workflow-interaction-receipt|进入计划阶段已确认|请审阅这份技术规划/)
      assert.doesNotMatch(markup, /<button(?![^>]*disabled)[^>]*>[\s\S]*?<span>确认保存<\/span>/)
      if (mode === 'planning_stage_entry_confirmation') {
        assert.match(markup, /设计阶段已完成/)
        assert.match(markup, /<button[^>]*disabled[^>]*><span>进入计划阶段<\/span>/)
      }
    }
  }
})

// 真实澄清保留原问题及历史答案，不能被空确认修复误删。
test('真实补充问题保留表单和答案，历史回答不出现再次提交操作', () => {
  const workflow = planningInteraction('requirements_clarification')
  workflow.summary.clarification!.questions = [{ id: 'scope', header: '用户范围', question: '哪些人使用？', type: 'text' }]
  const active = renderInteraction(workflow)
  assert.match(active, /待确认事项|哪些人使用/)
  assert.match(active, /确认并继续/)
  const historical = renderInteraction(workflow, { availability: 'stale', answers: { 用户范围: '内部员工' } })
  assert.match(historical, /内部员工/)
  assert.doesNotMatch(historical, /确认并继续/)
})
