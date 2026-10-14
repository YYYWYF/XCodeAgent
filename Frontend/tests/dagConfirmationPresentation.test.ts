import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import MessageList from '../src/renderer/src/components/AiChatPanel/components/MessageList'
import { WorkbenchPhaseContext } from '../src/renderer/src/context/workbenchPhaseState'
import { WORKBENCH_PHASE_AGENTS } from '../src/renderer/src/workbenchPhase'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'

/** 使用真实消息列表检查历史表单与过程记录的展示边界。 */
function renderMessages(messages: AgentChatMessage[]): string {
  return renderToStaticMarkup(
    createElement(
      WorkbenchPhaseContext.Provider,
      {
        value: {
          phase: 'development',
          derivedPhase: 'development',
          reachedPhase: 'development',
          manualOverride: null,
          locked: false,
          agent: WORKBENCH_PHASE_AGENTS.development,
          recordReachedPhase: () => undefined,
          switchPhase: () => undefined,
          canEdit: () => true
        }
      },
      createElement(MessageList, {
        applicationTemplatePreparationEligible: false,
        codeChangeActionsDisabled: false,
        conversationRunning: false,
        loading: false,
        messages,
        revertingCodeChangeIds: new Set<string>(),
        onRevertCodeChanges: () => undefined,
        onOpenCodeChangeFile: () => undefined,
        onSubmitClarification: async () => undefined
      })
    )
  )
}

/** 构造 Pending 确认时保留在历史消息中的 Workflow 快照。 */
function dagMessage(): AgentChatMessage {
  const workflow: WorkflowRunPayload = {
    runId: 'dag-run',
    threadId: 'dag-thread',
    events: [],
    summary: {
      status: 'requires_user_input',
      phase: 'prepare_build_tasks',
      clarification: {
        mode: 'build_task_plan_confirmation',
        status: 'requires_user_input',
        taskPlan: { status: 'ready', confirmationStatus: 'pending', reviewTasks: [] }
      }
    }
  }
  return {
    id: 1,
    role: 'assistant',
    content: '',
    createdAt: 1,
    workflow,
    processSteps: [
      {
        id: 'workflow:prepare_build_tasks',
        kind: 'workflow',
        status: 'completed',
        nodeName: 'prepare_build_tasks',
        title: '已完成 生成开发任务计划',
        detail: '本轮任务生成记录',
        sequence: 1
      }
    ]
  }
}

test('待确认 DAG 的完整表单不在左侧渲染，过程记录仍保留', () => {
  const markup = renderMessages([dagMessage()])
  assert.doesNotMatch(markup, /workflow-dag-confirmation|任务计划待确认|确认并执行任务/)
  assert.match(markup, /已完成 生成开发任务计划/)
  assert.match(markup, /本轮任务生成记录/)
})

test('确认进入 Build 后不恢复历史 DAG 表单，确认记录和后续确认仍保留', () => {
  const markup = renderMessages([
    dagMessage(),
    { id: 2, role: 'user', content: '已确认任务计划，请开始执行。', createdAt: 2 },
    {
      id: 3,
      role: 'assistant',
      content: '',
      createdAt: 3,
      workflow: {
        runId: 'build-run',
        threadId: 'dag-thread',
        events: [],
        summary: {
          status: 'requires_user_input',
          phase: 'test_phase_confirmation',
          clarification: { mode: 'test_phase_confirmation', status: 'requires_user_input' }
        }
      }
    }
  ])
  assert.doesNotMatch(markup, /workflow-dag-confirmation|任务计划待确认|确认并执行任务/)
  assert.match(markup, /已确认任务计划，请开始执行。/)
  assert.match(markup, /本轮任务生成记录/)
  assert.match(markup, /workflow-test-phase-confirmation/)
})
