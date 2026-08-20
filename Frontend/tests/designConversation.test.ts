import assert from 'node:assert/strict'

import { buildWorkflowForwardedProps } from '../src/renderer/src/service/agUiAgent'
import {
  ensureApplicationPlanningAction,
  planningRequirementsConfirmed,
  planningTechnicalPlanConfirmed,
  planningWorkflowNeedsChatLoading,
  planningWorkflowActivity,
  planningWorkflowRequiresUserInput,
  planningWorkflowSettlesLoading,
  shouldBackfillPlanningWorkflow
} from '../src/renderer/src/components/Welcome/planningWorkflowState'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

const planningInteraction = {
  gateId: 'requirement_spec:revision-1',
  artifact: 'requirement_spec' as const,
  artifactRevision: 'revision-1',
  action: 'design_change' as const,
  request: '新增报表页'
}
const forwardedProps = buildWorkflowForwardedProps({
  applicationPlanningInteraction: planningInteraction,
  editorMode: 'frontend',
  workflowScope: 'application_planning'
})
assert.deepEqual(forwardedProps.applicationPlanningInteraction, planningInteraction)
assert.equal(forwardedProps.workflowScope, 'application_planning')
assert.equal(forwardedProps.resumeState, undefined)

assert.equal(
  planningRequirementsConfirmed({
    state: { requirementsConfirmed: false },
    result: { requirementsConfirmed: true }
  } as WorkflowRunPayload),
  false
)

const regeneratedRequirementWithStaleTerminalConfirmation = {
  runId: 'run-requirement-revision',
  threadId: 'thread-requirement-revision',
  summary: {
    status: 'requires_user_input',
    phase: 'requirements'
  },
  events: [],
  state: {
    lifecycle: { initialization: { stage: 'awaiting_requirement_confirmation' } },
    technical_plan: { confirmation_status: 'pending_user_confirmation' }
  },
  result: {
    application_planning_confirmation: { confirmedAt: 'stale' },
    technical_plan: { confirmation_status: 'confirmed' }
  }
} as WorkflowRunPayload

assert.equal(planningTechnicalPlanConfirmed(regeneratedRequirementWithStaleTerminalConfirmation), false)

const completedTechnicalPlanWorkflow = {
  ...regeneratedRequirementWithStaleTerminalConfirmation,
  summary: {
    status: 'completed',
    phase: 'technical_planning'
  },
  state: {
    lifecycle: { initialization: { stage: 'generating_application_template_files' } },
    technical_plan: { confirmation_status: 'confirmed' }
  }
} as WorkflowRunPayload

assert.equal(planningTechnicalPlanConfirmed(completedTechnicalPlanWorkflow), true)
assert.equal(
  planningRequirementsConfirmed({ state: { requirementsConfirmed: true } } as WorkflowRunPayload),
  true
)
assert.equal(
  planningRequirementsConfirmed({
    state: { lifecycle: { initialization: { stage: 'generating_product_plan' } } }
  } as WorkflowRunPayload),
  false
)
assert.equal(
  planningRequirementsConfirmed(undefined, '.xcodeagent/specs/requirement-spec.md'),
  true
)
assert.equal(
  planningRequirementsConfirmed(undefined, '.xcodeagent/drafts/specs/requirement-spec.md'),
  false
)
assert.equal(
  planningRequirementsConfirmed(
    { state: { requirementsConfirmed: false } } as WorkflowRunPayload,
    '.xcodeagent/specs/requirement-spec.md'
  ),
  false
)

const summaryOnlyQuestionsWorkflow = {
  runId: 'run-requirements',
  threadId: 'thread-requirements',
  summary: {
    status: 'requires_user_input',
    phase: 'requirements',
    clarification: {
      mode: 'ask_user_question',
      questions: [{ id: 'audience', prompt: '主要用户是谁？' }]
    }
  },
  events: [],
  state: {},
  result: {}
} as WorkflowRunPayload

assert.equal(planningWorkflowRequiresUserInput(summaryOnlyQuestionsWorkflow), true)
assert.equal(
  ensureApplicationPlanningAction(summaryOnlyQuestionsWorkflow, { audience: '运营人员' })
    .__applicationPlanningAction,
  'answer'
)
assert.equal(
  ensureApplicationPlanningAction(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        ...summaryOnlyQuestionsWorkflow.summary,
        clarification: {
          mode: 'requirement_spec_confirmation',
          status: 'requires_user_input',
          questions: []
        }
      }
    } as WorkflowRunPayload,
    { requirement_spec_confirmation: '正确，继续规划' }
  ).__applicationPlanningAction,
  'confirm'
)
assert.equal(
  ensureApplicationPlanningAction(
    {
      ...summaryOnlyQuestionsWorkflow,
      summary: {
        ...summaryOnlyQuestionsWorkflow.summary,
        clarification: {
          mode: 'requirement_spec_confirmation',
          status: 'requires_user_input',
          questions: []
        }
      }
    } as WorkflowRunPayload,
    { requirement_spec_confirmation: '增加审批角色' }
  ).__applicationPlanningAction,
  'revise'
)
assert.equal(planningWorkflowSettlesLoading(summaryOnlyQuestionsWorkflow), true)
assert.equal(
  planningWorkflowNeedsChatLoading(
    { ...summaryOnlyQuestionsWorkflow, summary: { status: 'running', phase: 'requirements' } } as WorkflowRunPayload,
    true,
    false,
    false,
    ''
  ),
  true
)
assert.equal(
  planningWorkflowNeedsChatLoading(
    { ...summaryOnlyQuestionsWorkflow, summary: { status: 'running', phase: 'requirements' } } as WorkflowRunPayload,
    true,
    false,
    false,
    '正在分析需求'
  ),
  false
)
assert.equal(
  planningWorkflowNeedsChatLoading(
    { ...summaryOnlyQuestionsWorkflow, summary: { status: 'running', phase: 'requirements' } } as WorkflowRunPayload,
    true,
    false,
    true,
    ''
  ),
  false
)

const clarificationOnlyQuestionsWorkflow = {
  ...summaryOnlyQuestionsWorkflow,
  summary: {
    ...summaryOnlyQuestionsWorkflow.summary,
    status: 'running',
    clarification: {
      ...summaryOnlyQuestionsWorkflow.summary.clarification,
      status: 'requires_user_input'
    }
  }
} as WorkflowRunPayload

assert.equal(planningWorkflowRequiresUserInput(clarificationOnlyQuestionsWorkflow), true)
assert.equal(planningWorkflowSettlesLoading(clarificationOnlyQuestionsWorkflow), true)
assert.equal(shouldBackfillPlanningWorkflow(summaryOnlyQuestionsWorkflow, false), true)
assert.equal(shouldBackfillPlanningWorkflow(summaryOnlyQuestionsWorkflow, true), false)

const analyzingDesignIntentWorkflow = {
  runId: 'run-design-change',
  threadId: 'thread-design-change',
  summary: {
    status: 'running',
    phase: 'design_intent_analysis'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'design_intent_analysis',
      status: 'running'
    }
  ],
  state: {},
  result: {}
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(analyzingDesignIntentWorkflow), {
  status: 'running',
  title: '正在识别设计变更意图',
  detail: '正在判断这次改动应回到需求、产品规划还是 UI 设计阶段。'
})

const initialRequirementWorkflow = {
  ...analyzingDesignIntentWorkflow,
  summary: {
    status: 'running',
    phase: 'requirements'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'requirements',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(initialRequirementWorkflow), {
  status: 'running',
  title: '正在分析需求',
  detail: '正在识别产品目标、用户角色、页面与业务流程中的信息缺口。',
  intentLabel: undefined
})

const generatingRequirementDocumentWorkflow = {
  ...initialRequirementWorkflow,
  state: {
    lifecycle: {
      initialization: { stage: 'generating_requirement_spec' }
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(generatingRequirementDocumentWorkflow), {
  status: 'running',
  title: '正在生成需求文档',
  detail: '正在把已确认的需求草稿写入正式 Markdown 文档。',
  intentLabel: undefined
})

const initialProductPlanningWorkflow = {
  ...initialRequirementWorkflow,
  summary: {
    status: 'running',
    phase: 'product_planning'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'product_planning',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(initialProductPlanningWorkflow), {
  status: 'running',
  title: '正在生成产品规划',
  detail: '正在梳理页面目标、核心操作、状态与产品验收标准。',
  intentLabel: undefined
})

const firstProductPlanWithPendingArtifact = {
  ...initialProductPlanningWorkflow,
  state: {
    product_plan: { confirmation_status: 'pending_user_confirmation' },
    design_change_submission: true,
    design_change_target: 'product_planning'
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(firstProductPlanWithPendingArtifact), {
  status: 'running',
  title: '正在生成产品规划',
  detail: '正在梳理页面目标、核心操作、状态与产品验收标准。',
  intentLabel: undefined
})

const initialUiDesignWorkflow = {
  ...initialRequirementWorkflow,
  summary: {
    status: 'running',
    phase: 'ui_confirmation'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'ui_confirmation',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.equal(planningWorkflowActivity(initialUiDesignWorkflow), undefined)

const revisingRequirementWorkflow = {
  ...initialRequirementWorkflow,
  state: {
    design_change_submission: true,
    design_change_existing_artifacts: {
      requirements: true,
      product_planning: true,
      ui_confirmation: true,
      technical_planning: false
    }
  },
  events: [
    {
      type: 'workflow.node.completed',
      nodeName: 'design_intent_analysis',
      status: 'completed',
      data: { detail: { target: 'requirements' } }
    },
    {
      type: 'workflow.node.started',
      nodeName: 'requirements',
      status: 'running'
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(revisingRequirementWorkflow), {
  status: 'running',
  title: '正在重新分析需求',
  detail: '正在合并本次补充，并保留未受影响的需求事实。',
  intentLabel: '需求层变更'
})

const classifiedRequirementWorkflow = {
  ...revisingRequirementWorkflow,
  summary: {
    status: 'completed',
    phase: 'design_intent_analysis'
  },
  state: {
    design_change_submission: true,
    design_change_request: '新增报表页',
    design_change_target: 'requirements',
    design_change_existing_artifacts: {
      requirements: true
    }
  },
  events: [
    {
      type: 'workflow.node.completed',
      nodeName: 'design_intent_analysis',
      status: 'completed',
      data: { detail: { target: 'requirements' } }
    }
  ]
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(classifiedRequirementWorkflow), {
  status: 'running',
  title: '正在重新分析需求',
  detail: '正在合并本次补充，并保留未受影响的需求事实。',
  intentLabel: '需求层变更'
})

const regeneratingProductPlanWorkflow = {
  ...analyzingDesignIntentWorkflow,
  summary: {
    status: 'running',
    phase: 'product_planning'
  },
  events: [
    {
      type: 'workflow.node.completed',
      nodeName: 'design_intent_analysis',
      status: 'completed',
      data: {
        detail: {
          target: 'product_planning',
          reason: '页面操作和可见结果发生变化'
        }
      }
    },
    {
      type: 'workflow.node.started',
      nodeName: 'product_planning',
      status: 'running'
    }
  ],
  state: {
    design_change_submission: true,
    design_change_existing_artifacts: {
      product_planning: true
    }
  },
  result: {}
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(regeneratingProductPlanWorkflow), {
  status: 'running',
  title: '正在重新生成产品规划',
  detail: '页面操作和可见结果发生变化',
  intentLabel: '产品规划层变更'
})

const regeneratingUiDesignWorkflow = {
  ...regeneratingProductPlanWorkflow,
  summary: {
    status: 'running',
    phase: 'ui_confirmation'
  },
  events: [
    {
      type: 'workflow.node.started',
      nodeName: 'ui_confirmation',
      status: 'running'
    }
  ],
  state: {
    design_change_submission: true,
    design_change_target: 'ui_confirmation',
    design_change_existing_artifacts: {
      ui_confirmation: true
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(regeneratingUiDesignWorkflow), {
  status: 'running',
  title: '正在重新生成 UI 设计稿',
  detail: '正在更新受影响页面的布局、视觉与交互呈现。',
  intentLabel: 'UI 设计层变更'
})

const firstTechnicalPlanAfterDesignRevision = {
  ...regeneratingProductPlanWorkflow,
  summary: {
    status: 'running',
    phase: 'technical_planning'
  },
  events: [
    ...regeneratingProductPlanWorkflow.events,
    {
      type: 'workflow.node.started',
      nodeName: 'technical_planning',
      status: 'running'
    }
  ],
  state: {
    design_change_submission: true,
    design_change_target: 'product_planning',
    design_change_existing_artifacts: {
      product_planning: true,
      ui_confirmation: true,
      technical_planning: false
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(firstTechnicalPlanAfterDesignRevision), {
  status: 'running',
  title: '正在生成技术规划',
  detail: '正在根据已确认的上游设计生成技术实现方案。',
  intentLabel: undefined
})

const regeneratingTechnicalPlan = {
  ...firstTechnicalPlanAfterDesignRevision,
  state: {
    ...firstTechnicalPlanAfterDesignRevision.state,
    design_change_existing_artifacts: {
      technical_planning: true
    }
  }
} as WorkflowRunPayload

assert.deepEqual(planningWorkflowActivity(regeneratingTechnicalPlan), {
  status: 'running',
  title: '正在重新生成技术规划',
  detail: '正在根据本次设计变更更新技术实现方案。',
  intentLabel: '产品规划层变更'
})

const uiCardActionWithHistoricalIntent = {
  ...regeneratingProductPlanWorkflow,
  summary: {
    status: 'running',
    phase: 'ui_confirmation'
  },
  state: {
    design_change_submission: false,
    design_change_target: 'ui_confirmation'
  }
} as WorkflowRunPayload

assert.equal(planningWorkflowActivity(uiCardActionWithHistoricalIntent), undefined)
