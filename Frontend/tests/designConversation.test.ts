import assert from 'node:assert/strict'

import { buildWorkflowForwardedProps } from '../src/renderer/src/service/agUiAgent'
import {
  planningWorkflowActivity,
  planningWorkflowRequiresUserInput,
  planningWorkflowSettlesLoading,
  shouldBackfillPlanningWorkflow
} from '../src/renderer/src/components/Welcome/planningWorkflowState'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

const planningWorkflow = {
  state: { workflow_scope: 'application_planning' },
  result: {}
} as WorkflowRunPayload
const forwardedProps = buildWorkflowForwardedProps({
  designChangeSubmission: true,
  editorMode: 'frontend',
  resumeState: planningWorkflow,
  workflowScope: 'application_planning'
})
assert.equal(forwardedProps.designChangeSubmission, true)
assert.equal(forwardedProps.workflowScope, 'application_planning')
assert.equal(forwardedProps.resumeState, planningWorkflow)

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
assert.equal(planningWorkflowSettlesLoading(summaryOnlyQuestionsWorkflow), true)

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
  title: '正在生成需求文档',
  detail: '正在分析产品目标、用户角色、页面与业务流程。',
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
  title: '正在重新生成需求文档',
  detail: '正在合并本次变更，并保留未受影响的需求事实。',
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
  title: '正在重新生成需求文档',
  detail: '正在合并本次变更，并保留未受影响的需求事实。',
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
