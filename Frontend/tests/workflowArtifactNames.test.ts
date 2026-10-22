import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkflowRunCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import { WORKBENCH_PHASE_AGENTS, type WorkbenchPhase } from '../src/renderer/src/workbenchPhase'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 在实际阶段上下文中渲染确认卡，确保隐藏内部产物清单而不修改完整路径。 */
function renderArtifacts(
  phase: WorkbenchPhase,
  artifacts: Record<string, string>,
  state?: WorkflowRunPayload['state']
): { markup: string; workflow: WorkflowRunPayload } {
  const workflow: WorkflowRunPayload = {
    runId: `artifact-names-${phase}`,
    threadId: 'artifact-names-thread',
    events: [],
    state,
    summary: {
      phase: phase === 'design' ? 'product_planning' : 'technical_planning',
      status: 'requires_user_input',
      artifacts,
      clarification: {
        mode:
          phase === 'design' ? 'requirement_document_confirmation' : 'technical_plan_confirmation',
        status: 'requires_user_input',
        questions: []
      }
    }
  }
  const markup = renderToStaticMarkup(
    createElement(
      WorkbenchPhaseContext.Provider,
      {
        value: {
          phase,
          derivedPhase: phase,
          reachedPhase: phase,
          manualOverride: null,
          agent: WORKBENCH_PHASE_AGENTS[phase],
          recordReachedPhase: () => undefined,
          switchPhase: () => undefined,
          canEdit: () => true,
          locked: false
        }
      },
      createElement(WorkflowRunCard, { workflow, interactionAvailability: 'active' })
    )
  )
  return { markup, workflow }
}

test('设计阶段直接展示文档确认操作，隐藏内部产物清单', () => {
  const artifacts = {
    requirement_spec_path: '/Users/test/project/.devagentstudio/drafts/specs/requirement-spec.md',
    product_plan_path: '/Users/test/project/.devagentstudio/drafts/plans/product-plan.md'
  }
  const { markup, workflow } = renderArtifacts('design', artifacts)
  assert.match(markup, /需求草稿待确认/)
  assert.match(markup, /需求文档已生成，已展示在右侧/)
  assert.match(markup, /请在右侧查看内容/)
  assert.doesNotMatch(markup, /已生成产物|待确认事项|requirement-spec\.md|product-plan\.md/)
  assert.doesNotMatch(markup, /\/Users\/|\.devagentstudio|requirement_spec_path|product_plan_path/)
  assert.deepEqual(workflow.summary.artifacts, artifacts)
})

test('需求修订确认门不使用旧版本已确认标记隐藏查看提示', () => {
  const { markup } = renderArtifacts('design', {}, { requirements_confirmed: true })
  assert.match(markup, /需求文档已生成，已展示在右侧/)
  assert.match(markup, /需求草稿待确认/)
  assert.match(markup, /确认并继续规划/)
  assert.doesNotMatch(markup, /需求文档已确认/)
})

test('技术规划提示右侧查看并保留确认操作，隐藏各平台内部路径', () => {
  for (const path of [
    '/Users/test/project/.devagentstudio/plans/technical-plan.md',
    'C:\\Projects\\example\\.devagentstudio\\plans\\technical-plan.md'
  ]) {
    const { markup, workflow } = renderArtifacts('planning', { technical_plan_path: path })
    assert.match(markup, /技术规划已生成，已展示在右侧/)
    assert.match(markup, /请在右侧查看内容/)
    assert.match(markup, /确认保存/)
    assert.doesNotMatch(markup, /已生成产物|待确认事项|technical-plan\.md/)
    assert.doesNotMatch(markup, /\/Users\/|C:|\.devagentstudio|technical_plan_path/)
    assert.equal(workflow.summary.artifacts?.technical_plan_path, path)
  }
})
