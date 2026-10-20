import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import WorkflowRunCard from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import { WORKBENCH_PHASE_AGENTS, type WorkbenchPhase } from '../src/renderer/src/workbenchPhase'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 在实际阶段上下文中渲染产物列表，确认只缩短展示、不修改内部完整路径。 */
function renderArtifacts(
  phase: WorkbenchPhase,
  artifacts: Record<string, string>
): { markup: string; workflow: WorkflowRunPayload } {
  const workflow: WorkflowRunPayload = {
    runId: `artifact-names-${phase}`,
    threadId: 'artifact-names-thread',
    events: [],
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

test('设计阶段已生成产物只展示文件名，隐藏路径与协议字段名', () => {
  const artifacts = {
    requirement_spec_path: '/Users/test/project/.devagentstudio/drafts/specs/requirement-spec.md',
    product_plan_path: '/Users/test/project/.devagentstudio/drafts/plans/product-plan.md'
  }
  const { markup, workflow } = renderArtifacts('design', artifacts)
  assert.match(markup, /已生成产物/)
  assert.match(markup, /requirement-spec\.md/)
  assert.match(markup, /product-plan\.md/)
  assert.doesNotMatch(markup, /\/Users\/|\.devagentstudio|requirement_spec_path|product_plan_path/)
  assert.deepEqual(workflow.summary.artifacts, artifacts)
})

test('计划阶段技术规划同样只展示文件名，并兼容 Windows 路径', () => {
  for (const path of [
    '/Users/test/project/.devagentstudio/plans/technical-plan.md',
    'C:\\Projects\\example\\.devagentstudio\\plans\\technical-plan.md'
  ]) {
    const { markup, workflow } = renderArtifacts('planning', { technical_plan_path: path })
    assert.match(markup, /已生成产物/)
    assert.match(markup, /technical-plan\.md/)
    assert.doesNotMatch(markup, /\/Users\/|C:|\.devagentstudio|technical_plan_path/)
    assert.equal(workflow.summary.artifacts?.technical_plan_path, path)
  }
})
