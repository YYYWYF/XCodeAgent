import assert from 'node:assert/strict'
import { test } from 'node:test'
import { createElement } from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  beginOptimisticSkillSend,
  normalizeChatSkills,
  rollbackSkillSelection,
  selectedSkillNames,
  skillsAfterEmptyBackspace
} from '../src/renderer/src/components/AiChatPanel/skillSelection'
import {
  AgUiChatSession,
  buildWorkflowForwardedProps,
  readDagGenerationSnapshot,
  readProjectPlanUpdate,
  readWorkspaceInspectionSnapshot
} from '../src/renderer/src/service/agUiAgent'
import { revisionContinuationFromWorkflow } from '../src/renderer/src/service/applicationPagePlanning'
import ProcessSteps from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps'
import ApplicationPlanningQuestionPanel from '../src/renderer/src/components/Welcome/ApplicationPlanningQuestionPanel'
import { ToolCallChain } from '../src/renderer/src/components/AiChatPanel/components/ToolCallCard'
import {
  isConversationWaitingForInput,
  shouldUseConversation
} from '../src/renderer/src/components/AiChatPanel/conversationMode'
import { workflowDebugBuildScope } from '../src/renderer/src/components/AiChatPanel/debugExecutionScope'
import WorkflowRunCard, {
  buildToolActivityPlacement,
  workflowOriginalRequest
} from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard'
import { workflowInteractionAvailability } from '../src/renderer/src/components/AiChatPanel/planExecutionMode'
import {
  isStructuredPlanningWorkflow,
  processStepsForDisplay,
  processStepsForMessageDisplay,
  workflowMessageContentForDisplay
} from '../src/renderer/src/service/processStepHistory'
import { normalizePersistentSessionMessage } from '../src/main/sessionMessageNormalization'
import {
  normalizeMessageSkills,
  normalizeRevisionSessionContext
} from '../src/renderer/src/service/chatSessions'
import {
  chatCopy,
  DEFAULT_DIFF_PANEL_WIDTH
} from '../src/renderer/src/components/AiChatPanel/constants'
import {
  splitWorkspacePath,
  workspaceCodeChangeDisplayPath,
  workflowCodeChangesBeforeConfirmation,
  workflowShouldShowCodeChanges
} from '../src/renderer/src/components/AiChatPanel/utils'
import {
  selectedSessionIdForPhase,
  withSelectedSessionForPhase
} from '../src/renderer/src/components/AiChatPanel/hooks/phaseSessionSelection'
import {
  DEFAULT_SKILL_CATEGORY,
  enabledUserSkills,
  filterCatalogSkills,
  reconcileEnabledChatSkills
} from '../src/renderer/src/components/SkillsPage/skillCatalog'
import type { UserSkillCatalog, WorkflowRunPayload } from '../src/renderer/src/typings'

test('prepare_build_tasks 调试默认继承当前页面范围', () => {
  const scope = workflowDebugBuildScope({
    runId: 'run-page',
    threadId: 'thread-page',
    summary: {},
    events: [],
    state: {
      buildExecutionScope: {
        type: 'page',
        targetId: 'pet_list_page'
      }
    }
  })

  assert.deepEqual(scope, { type: 'page', targetId: 'pet_list_page' })
})

test('design TechnicalPlan 完成后只读取完整的一次性 continuation 合同', () => {
  const continuation = revisionContinuationFromWorkflow({
    runId: 'planning-run',
    threadId: 'planning-thread',
    summary: {
      revisionContinuation: {
        changeId: 'chg-1',
        formalBranch: 'design_stage_revision',
        action: 'continue_revision_build',
        token: 't'.repeat(48),
        technicalPlanSha256: 'a'.repeat(64)
      }
    },
    events: []
  })
  const invalid = revisionContinuationFromWorkflow({
    runId: 'planning-run',
    threadId: 'planning-thread',
    summary: {
      revisionContinuation: {
        changeId: 'chg-1',
        formalBranch: 'design_stage_revision',
        action: 'continue_revision_build',
        token: 'inspect_workspace',
        technicalPlanSha256: 'a'.repeat(64)
      }
    },
    events: []
  })

  assert.equal(continuation?.changeId, 'chg-1')
  assert.equal(invalid, undefined)
})

const skillCatalog: UserSkillCatalog = {
  root: '~/.xcodeagent_dev/skills',
  builtinRoot: '/.xcodeagent/builtin-skills',
  skills: [
    {
      name: 'alpha',
      description: 'First user skill',
      directoryName: 'alpha',
      relativePath: 'alpha/SKILL.md',
      updatedAt: '2026-07-19T00:00:00Z',
      enabled: true
    },
    {
      name: 'beta',
      description: 'Disabled user skill',
      directoryName: 'beta',
      relativePath: 'beta/SKILL.md',
      updatedAt: '2026-07-19T00:00:00Z',
      enabled: false
    }
  ],
  builtinSkills: [
    {
      name: 'builtin-react',
      description: 'Built-in React skill',
      directoryName: 'builtin-react',
      relativePath: 'builtin-react/SKILL.md'
    }
  ],
  skippedCount: 0,
  issues: []
}

test('简单模式输入框引导用户进行页面或 API 微调', () => {
  const expected = '描述你想微调的页面或 API，例如修改文案、样式或接口逻辑…'

  assert.equal(chatCopy.frontend.placeholder, expected)
  assert.equal(chatCopy.backend.placeholder, expected)
})

test('简单模式等待补充时直接展示问题并隐藏步骤标题和图标', () => {
  const workflow = {
    runId: 'direct-wait-run',
    threadId: 'direct-thread',
    summary: {
      status: 'requires_user_input',
      phase: 'conversation',
      intent: 'clarification',
      owner: 'unknown',
      message: '请说明要修改哪个页面或接口，以及期望结果。'
    },
    events: []
  }
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      waitingForInput: isConversationWaitingForInput(workflow),
      waitingPrompt: workflow.summary.message,
      steps: [
        {
          id: 'direct:classify_intent',
          kind: 'workflow',
          status: 'requires_user_input',
          title: '等待输入 识别修改意图',
          detail: '',
          sequence: 10
        }
      ]
    })
  )

  assert.equal(isConversationWaitingForInput(workflow), true)
  assert.equal((markup.match(/ open=""/g) || []).length, 1)
  assert.match(markup, /Agent 等待补充/)
  assert.match(markup, /请根据下方提示补充修改需求/)
  assert.match(markup, /请补充输入/)
  assert.match(markup, /请说明要修改哪个页面或接口，以及期望结果。/)
  assert.doesNotMatch(markup, /等待输入 识别修改意图/)
  assert.doesNotMatch(markup, /process-step-icon/)
})

test('简单模式 formal revision 影响确认显示为等待输入', () => {
  const workflow = {
    runId: 'direct-planning-run',
    threadId: 'direct-thread',
    summary: {
      status: 'requires_user_input',
      phase: 'conversation',
      intent: 'formal_revision',
      owner: 'unknown'
    },
    events: []
  }
  const waitingForInput = isConversationWaitingForInput(workflow)
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      waitingForInput,
      steps: [
        {
          id: 'direct:classify_intent',
          kind: 'workflow',
          status: 'requires_user_input',
          title: '等待输入 识别修改意图',
          detail: '该需求需要正式设计工作流。',
          sequence: 10
        }
      ]
    })
  )

  assert.equal(waitingForInput, true)
  assert.match(markup, /Agent 等待补充/)
  assert.match(markup, /请补充输入/)
})

test('简单模式等待补充后仍复用独立端点', () => {
  const workflow = {
    runId: 'direct-wait-run',
    threadId: 'direct-thread',
    summary: {
      status: 'requires_user_input',
      phase: 'conversation',
      intent: 'clarification',
      owner: 'unknown'
    },
    events: []
  }

  assert.equal(shouldUseConversation(false, workflow, undefined), true)
  assert.equal(
    shouldUseConversation(
      true,
      {
        ...workflow,
        summary: { status: 'requires_user_input', phase: 'requirement_clarification' }
      },
      undefined
    ),
    false
  )
  assert.equal(shouldUseConversation(true, workflow, { enabled: true }), false)
})

test('无目标自由对话默认使用独立快速修改端点', () => {
  assert.equal(shouldUseConversation(true, undefined, undefined), true)
})

test('页面或 API 设计模式显式控制新消息的 Graph 路由', () => {
  assert.equal(shouldUseConversation(true, undefined, undefined, 'design'), false)
  assert.equal(shouldUseConversation(true, undefined, undefined, 'conversation'), true)
  assert.equal(
    shouldUseConversation(
      true,
      {
        runId: 'conversation-pending',
        threadId: 'conversation-thread',
        summary: { status: 'requires_user_input', phase: 'conversation' },
        events: []
      },
      undefined,
      'design'
    ),
    true
  )
})

test('技能选择按名称去空白去重并保留首次顺序', () => {
  assert.deepEqual(
    normalizeChatSkills([
      { name: ' alpha ', description: ' first ' },
      { name: 'alpha', description: 'duplicate' },
      { name: 'beta', description: 'second' }
    ]),
    [
      { name: 'alpha', description: 'first' },
      { name: 'beta', description: 'second' }
    ]
  )
})

test('AG-UI forwardedProps 在约定字段发送技能名称', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    selectedSkillNames: ['alpha', 'beta']
  })

  assert.deepEqual(forwardedProps.selectedSkillNames, ['alpha', 'beta'])
})

test('快速修改只在独立字段发送工作区和技能且不包含 target', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    workspaceRoot: '/workspace',
    selectedSkillNames: ['alpha'],
    conversation: true
  })

  assert.deepEqual(forwardedProps.conversation, {
    workspaceRoot: '/workspace',
    selectedSkillNames: ['alpha']
  })
  assert.equal(
    Object.hasOwn(forwardedProps.conversation as Record<string, unknown>, 'target'),
    false
  )
})

test('SmallTask handoff 只在确认续跑时携带原请求、路径和决定', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    conversation: true,
    originalRequest: '修复订单页筛选按钮',
    conversationApprovedPaths: ['Frontend/src/pages/Orders.tsx'],
    conversationHandoffDecision: 'approved'
  })

  assert.deepEqual(forwardedProps.conversation, {
    workspaceRoot: undefined,
    selectedSkillNames: undefined,
    originalRequest: '修复订单页筛选按钮',
    approvedPaths: ['Frontend/src/pages/Orders.tsx'],
    handoffDecision: 'approved'
  })
})

test('快速修改自定义事件复用 Workflow 展示和流程步骤回调', async () => {
  const originalFetch = globalThis.fetch
  let workflowStatus = ''
  let processStepCount = 0
  const processStepStatuses: string[] = []
  globalThis.fetch = async (_input, init) => {
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>
    const threadId = String(request.threadId)
    const runId = String(request.runId)
    const messageId = 'assistant-direct'
    const value = {
      runId,
      threadId,
      status: 'completed',
      summary: {
        status: 'completed',
        phase: 'conversation',
        intent: 'implementation_fix',
        message: '快速修改完成',
        owner: 'frontend'
      },
      events: [],
      state: { status: 'completed' },
      result: { status: 'completed' },
      processStep: {
        id: 'direct:execute_frontend',
        kind: 'workflow',
        status: 'completed',
        title: '已完成 执行前端修改',
        detail: '完成页面修改',
        sequence: 60
      }
    }
    const runningValue = {
      ...value,
      status: 'in_progress',
      summary: { ...value.summary, status: 'in_progress' },
      state: { status: 'in_progress' },
      processStep: {
        ...value.processStep,
        status: 'running',
        title: '正在执行 执行前端修改',
        detail: '正在执行：执行前端修改'
      }
    }
    const events = [
      { type: 'RUN_STARTED', threadId, runId },
      { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
      { type: 'CUSTOM', name: 'conversation', value: runningValue },
      { type: 'CUSTOM', name: 'conversation', value },
      { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: '快速修改完成' },
      { type: 'TEXT_MESSAGE_END', messageId },
      { type: 'RUN_FINISHED', threadId, runId, result: { conversation: value } }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'content-type': 'text/event-stream' },
      status: 200
    })
  }

  try {
    const session = new AgUiChatSession('thread-direct', 'http://agent.test/conversation/run')
    const result = await session.sendMessage('修改页面样式', {
      editorMode: 'frontend',
      workspaceRoot: '/workspace',
      conversation: true,
      onWorkflow: (workflow) => {
        workflowStatus = String(workflow.summary.status)
      },
      onProcessSteps: (steps) => {
        processStepCount = steps.length
        processStepStatuses.push(String(steps[0]?.status))
      }
    })
    assert.equal(result.workflow?.summary.phase, 'conversation')
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(workflowStatus, 'completed')
  assert.equal(processStepCount, 1)
  assert.deepEqual(processStepStatuses, ['running', 'completed'])
})

test('自由对话运行时展示实时步骤与工具活动', () => {
  const workflow = {
    runId: 'direct-run',
    threadId: 'direct-thread',
    summary: { status: 'in_progress', phase: 'conversation', intent: 'implementation_fix' },
    events: []
  }
  const steps = [
    {
      id: 'direct:execute_frontend',
      kind: 'workflow' as const,
      status: 'running' as const,
      title: '正在执行 执行前端修改',
      detail: '正在执行：执行前端修改',
      sequence: 60
    },
    {
      id: 'direct-tool:read-old',
      kind: 'tool' as const,
      status: 'completed' as const,
      title: 'grep',
      detail: '已完成搜索代码：旧调用',
      sequence: 100
    },
    {
      id: 'direct-tool:read-current',
      kind: 'tool' as const,
      status: 'running' as const,
      title: 'read_file',
      detail: '正在读取文件：/src/App.tsx',
      sequence: 101
    }
  ]

  const runningSteps = processStepsForMessageDisplay(steps, workflow)
  const completedSteps = processStepsForMessageDisplay(steps, workflow)
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      conversation: true,
      loading: true,
      steps: runningSteps || []
    })
  )

  assert.deepEqual(
    runningSteps?.map((step) => step.id),
    ['direct:execute_frontend', 'direct-tool:read-old', 'direct-tool:read-current']
  )
  assert.deepEqual(
    completedSteps?.map((step) => step.id),
    ['direct:execute_frontend', 'direct-tool:read-old', 'direct-tool:read-current']
  )
  assert.match(markup, /正在处理请求/)
  assert.match(markup, /正在调用 read_file 工具/)
  assert.match(markup, /展开调用链/)
  assert.match(markup, /已记录 2 次调用/)
  assert.doesNotMatch(markup, /已调用 grep 工具/)
})

test('标准 AG-UI 工具事件默认只展示最新调用', () => {
  const markup = renderToStaticMarkup(
    createElement(ToolCallChain, {
      toolCalls: [
        { id: 'tool-1', name: 'read_file', args: '{}', status: 'completed' },
        { id: 'tool-2', name: 'write_file', args: '{}', status: 'running' }
      ]
    })
  )

  assert.match(markup, /调用 write_file 工具中/)
  assert.match(markup, /展开调用链/)
  assert.match(markup, /已记录 2 次工具调用/)
  assert.doesNotMatch(markup, /已调用 read_file 工具/)
})

test('自由对话保留全部过程步骤，正式 Workflow 仍保留稳定步骤', () => {
  const steps = [
    {
      id: 'direct:launch_project',
      kind: 'workflow' as const,
      status: 'completed' as const,
      title: '已完成 启动本地预览',
      detail: '',
      sequence: 95,
      nodeName: 'launch_project'
    },
    {
      id: 'direct:finalize_direct_modification',
      kind: 'workflow' as const,
      status: 'completed' as const,
      title: '已完成 整理修改结果',
      detail: '',
      sequence: 100,
      nodeName: 'finalize_direct_modification'
    }
  ]
  const directWorkflow = {
    runId: 'direct-run',
    threadId: 'direct-thread',
    summary: { status: 'completed', phase: 'conversation', intent: 'implementation_fix' },
    events: []
  }
  const mainWorkflow = {
    runId: 'main-run',
    threadId: 'main-thread',
    summary: { status: 'completed' },
    events: []
  }

  assert.deepEqual(
    processStepsForMessageDisplay(steps, directWorkflow)?.map((step) => step.id),
    ['direct:launch_project', 'direct:finalize_direct_modification']
  )
  assert.deepEqual(
    processStepsForMessageDisplay(steps, directWorkflow)?.map((step) => step.id),
    ['direct:launch_project', 'direct:finalize_direct_modification']
  )
  assert.deepEqual(
    processStepsForMessageDisplay(steps, mainWorkflow)?.map((step) => step.id),
    ['direct:launch_project', 'direct:finalize_direct_modification']
  )
})

test('简单模式正式升级使用统一影响确认并保留原始问题', () => {
  const workflow = {
    runId: 'direct-handoff-run',
    threadId: 'direct-thread',
    summary: {
      status: 'requires_user_input',
      phase: 'conversation',
      intent: 'formal_revision',
      request: '创建一个订单管理系统',
      clarification: {
        mode: 'revision_impact_confirmation',
        status: 'requires_user_input'
      }
    },
    events: []
  }

  assert.equal(workflowInteractionAvailability(workflow, undefined), 'active')
  assert.equal(workflowOriginalRequest(workflow), '创建一个订单管理系统')
})

test('AG-UI 继续执行只发送旧 runId 作为资源锁转移令牌', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    resumeExecutionRunId: 'run-stopped'
  })

  assert.equal(forwardedProps.resumeExecutionRunId, 'run-stopped')
})

test('AG-UI 重试失败任务发送显式工作流动作', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    workflowAction: 'retry_failed_tasks'
  })

  assert.equal(forwardedProps.workflowAction, 'retry_failed_tasks')
})

test('AG-UI 审查模型重试发送独立工作流动作', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    workflowAction: 'retry_code_review'
  })

  assert.equal(forwardedProps.workflowAction, 'retry_code_review')
})

test('AG-UI 连续请求只发送当前用户消息', async () => {
  const originalFetch = globalThis.fetch
  const requestBodies: Array<Record<string, unknown>> = []
  globalThis.fetch = async (_input, init) => {
    requestBodies.push(JSON.parse(String(init?.body)) as Record<string, unknown>)
    const request = requestBodies.at(-1)!
    const threadId = String(request.threadId)
    const runId = String(request.runId)
    const messageId = `assistant-${requestBodies.length}`
    const events = [
      { type: 'RUN_STARTED', threadId, runId },
      { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
      { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: 'ok' },
      { type: 'TEXT_MESSAGE_END', messageId },
      { type: 'RUN_FINISHED', threadId, runId, result: {} }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'content-type': 'text/event-stream' },
      status: 200
    })
  }

  try {
    const session = new AgUiChatSession('thread-1', 'http://agent.test/workflow/run')
    await session.sendMessage('第一条', { editorMode: 'frontend' })
    await session.sendMessage('第二条', { editorMode: 'frontend' })
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(requestBodies.length, 2)
  assert.deepEqual(
    requestBodies.map((body) => body.threadId),
    ['thread-1', 'thread-1']
  )
  assert.notEqual(requestBodies[0].runId, requestBodies[1].runId)
  assert.deepEqual(
    requestBodies.map((body) =>
      (body.messages as Array<{ role: string; content: string }>).map(({ role, content }) => ({
        role,
        content
      }))
    ),
    [[{ role: 'user', content: '第一条' }], [{ role: 'user', content: '第二条' }]]
  )
})

test('AG-UI 暂停先等待后端取消接管，不会立即中止活动流', async () => {
  const originalFetch = globalThis.fetch
  let activeSignal: AbortSignal | undefined
  let rejectActiveRequest: ((reason?: unknown) => void) | undefined
  let cancellationRequested = false
  globalThis.fetch = async (_input, init) => {
    const request = JSON.parse(String(init?.body)) as {
      forwardedProps?: { cancelRunId?: string }
      runId?: string
      threadId?: string
    }
    if (request.forwardedProps?.cancelRunId) {
      cancellationRequested = true
      const events = [
        {
          type: 'RUN_STARTED',
          threadId: String(request.threadId),
          runId: String(request.runId)
        },
        {
          type: 'RUN_FINISHED',
          threadId: String(request.threadId),
          runId: String(request.runId),
          result: {
            workflowRunControl: {
              status: 'cancel_requested',
              targetRunId: request.forwardedProps.cancelRunId
            }
          }
        }
      ]
      return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
        headers: { 'content-type': 'text/event-stream' },
        status: 200
      })
    }
    activeSignal = init?.signal || undefined
    return new Promise<Response>((_resolve, reject) => {
      rejectActiveRequest = reject
    })
  }

  try {
    const session = new AgUiChatSession('thread-stop', 'http://agent.test/workflow/run')
    const activeRequest = session.sendMessage('执行计划', { editorMode: 'frontend' })
    await Promise.resolve()
    session.stop()
    await new Promise((resolve) => setTimeout(resolve, 0))

    assert.equal(cancellationRequested, true)
    assert.equal(activeSignal?.aborted, false)

    rejectActiveRequest?.(new Error('server cancelled'))
    await assert.rejects(activeRequest, /server cancelled/)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('AG-UI 集成测试步骤会合并并保留实时检查清单', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_input, init) => {
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>
    const threadId = String(request.threadId)
    const runId = String(request.runId)
    const messageId = 'assistant-integration-checks'
    const events = [
      { type: 'RUN_STARTED', threadId, runId },
      { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
      {
        type: 'CUSTOM',
        name: 'agent-process',
        value: {
          id: 'workflow:integration_test',
          kind: 'workflow',
          status: 'running',
          title: '正在执行 集成测试与质量门禁',
          detail: '正在执行检查。',
          sequence: 1,
          checks: [
            {
              id: 'frontend_build',
              name: '前端构建检查',
              status: 'running',
              required: true
            }
          ]
        }
      },
      {
        type: 'CUSTOM',
        name: 'agent-process',
        value: {
          id: 'workflow:integration_test',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 集成测试与质量门禁',
          detail: '通过=True，检查=1/1',
          sequence: 1
        }
      },
      { type: 'TEXT_MESSAGE_END', messageId },
      { type: 'RUN_FINISHED', threadId, runId, result: {} }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'content-type': 'text/event-stream' },
      status: 200
    })
  }

  try {
    const session = new AgUiChatSession('thread-integration', 'http://agent.test/workflow/run')
    const result = await session.sendMessage('执行集成测试', { editorMode: 'frontend' })
    assert.deepEqual(result.processSteps[0]?.checks, [
      {
        id: 'frontend_build',
        name: '前端构建检查',
        status: 'running',
        required: true
      }
    ])
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('AG-UI DAG 生成步骤按稳定 ID 合并最新完整快照', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_input, init) => {
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>
    const threadId = String(request.threadId)
    const runId = String(request.runId)
    const messageId = 'assistant-dag-generation'
    const baseStep = {
      id: 'workflow:prepare_build_tasks',
      kind: 'workflow',
      status: 'running',
      title: '正在执行 构建任务 DAG 生成',
      detail: '生成中',
      sequence: 3
    }
    const events = [
      { type: 'RUN_STARTED', threadId, runId },
      { type: 'TEXT_MESSAGE_START', messageId, role: 'assistant' },
      {
        type: 'CUSTOM',
        name: 'agent-process',
        value: {
          ...baseStep,
          dagGeneration: {
            stages: [
              {
                id: 'unit_skeleton',
                name: '生成 Unit DAG 骨架',
                status: 'running',
                detail: '生成中'
              }
            ],
            tasks: [],
            summary: { unitCount: 0, taskCount: 0 },
            artifacts: []
          }
        }
      },
      {
        type: 'CUSTOM',
        name: 'agent-process',
        value: {
          ...baseStep,
          status: 'completed',
          dagGeneration: {
            stages: [
              {
                id: 'unit_skeleton',
                name: '生成 Unit DAG 骨架',
                status: 'completed',
                detail: '完成'
              }
            ],
            tasks: [
              {
                id: 'api',
                title: '实现 API',
                owner: 'data_source',
                status: 'pending',
                dependencies: [],
                changePaths: ['backend/api.py'],
                acceptanceCriteria: ['接口可用']
              },
              {
                id: 'page',
                title: '实现页面',
                owner: 'frontend',
                status: 'pending',
                dependencies: ['api'],
                changePaths: ['frontend/Page.tsx'],
                acceptanceCriteria: ['页面可渲染']
              }
            ],
            summary: { unitCount: 2, taskCount: 2, batchCount: 2 },
            artifacts: []
          }
        }
      },
      { type: 'TEXT_MESSAGE_END', messageId },
      { type: 'RUN_FINISHED', threadId, runId, result: {} }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'content-type': 'text/event-stream' },
      status: 200
    })
  }

  try {
    const session = new AgUiChatSession('thread-dag', 'http://agent.test/workflow/run')
    const result = await session.sendMessage('生成 DAG', { editorMode: 'frontend' })

    assert.equal(result.processSteps.length, 1)
    assert.equal(result.processSteps[0]?.sequence, 3)
    assert.equal(result.processSteps[0]?.status, 'completed')
    assert.deepEqual(
      result.processSteps[0]?.dagGeneration?.tasks.map((task) => task.id),
      ['api', 'page']
    )
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('工作区检查快照过滤绝对路径并渲染默认展开的科技感面板', () => {
  const snapshot = readWorkspaceInspectionSnapshot({
    schemaVersion: '1.0.0',
    revision: 'revision-1234567890',
    cacheHit: true,
    fileManifest: { totalFiles: 128, sourceFiles: 96, truncated: false },
    techStack: ['FastAPI', 'React', 'Vite'],
    projectRoots: [
      { path: 'Backend/app', kind: 'backend' },
      { path: '/private/workspace', kind: 'unsafe' }
    ],
    entrypoints: [{ path: 'Frontend/src/renderer/src/main.tsx', kind: 'frontend_renderer' }],
    codeGraph: { provider: 'none', available: false },
    workspaceSnapshotPath: '/private/workspace/cache/snapshot.json'
  })
  assert.ok(snapshot)

  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      steps: [
        {
          id: 'workflow:inspect_workspace',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 工作区快照检查',
          detail: '已索引 128 个文件',
          sequence: 1,
          workspaceInspection: snapshot
        }
      ]
    })
  )

  assert.equal(snapshot.projectRoots.length, 1)
  assert.match(markup, /WORKSPACE SCAN/)
  assert.match(markup, /CACHE HIT/)
  assert.match(markup, /FastAPI/)
  assert.match(markup, /Backend\/app/)
  assert.match(markup, /代码图暂不可用/)
  assert.ok((markup.match(/ open=""/g) || []).length >= 2)
  assert.doesNotMatch(markup, /private\/workspace|snapshot\.json/)
})

test('AG-UI 工作区完成帧解析并保留结构化扫描结果', async () => {
  const originalFetch = globalThis.fetch
  globalThis.fetch = async (_input, init) => {
    const request = JSON.parse(String(init?.body)) as Record<string, unknown>
    const threadId = String(request.threadId)
    const runId = String(request.runId)
    const events = [
      { type: 'RUN_STARTED', threadId, runId },
      {
        type: 'CUSTOM',
        name: 'agent-process',
        value: {
          id: 'workflow:inspect_workspace',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 工作区快照检查',
          detail: '已索引 64 个文件',
          sequence: 2,
          workspaceInspection: {
            schemaVersion: '1.0.0',
            revision: 'live-revision',
            cacheHit: false,
            fileManifest: { totalFiles: 64, sourceFiles: 48, truncated: false },
            techStack: ['React'],
            projectRoots: [{ path: 'frontend/src', kind: 'frontend' }],
            entrypoints: [],
            codeGraph: { provider: 'none', available: false }
          }
        }
      },
      { type: 'RUN_FINISHED', threadId, runId, result: {} }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'content-type': 'text/event-stream' },
      status: 200
    })
  }

  try {
    const session = new AgUiChatSession('thread-workspace', 'http://agent.test/workflow/run')
    const result = await session.sendMessage('检查工作区', { editorMode: 'frontend' })
    assert.equal(result.processSteps[0]?.workspaceInspection?.revision, 'live-revision')
    assert.equal(result.processSteps[0]?.workspaceInspection?.fileManifest.sourceFiles, 48)
  } finally {
    globalThis.fetch = originalFetch
  }
})

test('工作区检查详情优先从完成事件恢复并兼容旧状态字段', () => {
  const eventSteps = processStepsForDisplay(undefined, {
    runId: 'run-workspace-event',
    threadId: 'thread-workspace-event',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'inspect_workspace',
        node: { label: '工作区快照检查' },
        status: 'completed',
        data: {
          detail: {
            workspaceInspection: {
              schemaVersion: '1.0.0',
              revision: 'event-revision',
              cacheHit: false,
              fileManifest: { totalFiles: 20, sourceFiles: 12, truncated: false },
              techStack: ['React'],
              projectRoots: [{ path: 'frontend/src', kind: 'frontend' }],
              entrypoints: [],
              codeGraph: { provider: 'none', available: false }
            }
          }
        }
      }
    ],
    state: {
      workspace_snapshot_summary: {
        schema_version: 'legacy',
        workspace_revision: 'state-should-not-win',
        file_manifest: { total_files_indexed: 1, source_files_indexed: 1 }
      }
    }
  })
  assert.equal(eventSteps?.[0].workspaceInspection?.revision, 'event-revision')
  assert.equal(eventSteps?.[0].workspaceInspection?.fileManifest.totalFiles, 20)

  const legacySteps = processStepsForDisplay(undefined, {
    runId: 'run-workspace-legacy',
    threadId: 'thread-workspace-legacy',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'inspect_workspace',
        node: { label: '工作区快照检查' },
        status: 'completed',
        message: '完成：工作区快照检查',
        data: { stateDelta: { timeline: ['inspect_workspace:cache_hit'] } }
      }
    ],
    state: {
      workspace_snapshot_summary: {
        schema_version: '1.0.0',
        workspace_revision: 'legacy-revision',
        tech_stack: ['React'],
        project_roots: [{ path: 'frontend/src', kind: 'frontend' }],
        entrypoints: [],
        file_manifest: {
          total_files_indexed: 42,
          source_files_indexed: 30,
          truncated: true
        },
        code_graph: { provider: 'none', available: false }
      }
    }
  })

  assert.equal(legacySteps?.[0].workspaceInspection?.revision, 'legacy-revision')
  assert.equal(legacySteps?.[0].workspaceInspection?.fileManifest.sourceFiles, 30)
  assert.equal(legacySteps?.[0].workspaceInspection?.cacheHit, true)
})

test('DAG 快照解析和展示不暴露模型原文或内部 JSON', () => {
  const snapshot = readDagGenerationSnapshot({
    agent_note: 'raw-model-output',
    buildTaskPlanPath: '/workspace/build-task-plan.json',
    stages: [
      {
        id: 'unit_skeleton',
        name: '生成 Unit DAG 骨架',
        status: 'completed',
        detail: '已完成'
      },
      {
        id: 'model_planning',
        name: '生成候选构建任务',
        status: 'completed',
        detail: '已生成 1 项'
      },
      {
        id: 'task_compilation',
        name: '编译任务注册表与依赖',
        status: 'completed',
        detail: '已编译 1 个任务、0 条任务依赖。',
        output: {
          kind: 'compiled_tasks',
          tasks: [
            {
              id: 'page-home',
              title: '实现首页',
              owner: 'frontend',
              status: 'pending',
              dependencies: [],
              changePaths: ['frontend/src/pages/Home.tsx'],
              acceptanceCriteria: ['首页可渲染']
            }
          ],
          edges: { items: [], truncated: false },
          summary: { frontend: 1, backend: 0, database: 0 }
        }
      },
      {
        id: 'artifact_persistence',
        name: '保存 DAG 产物',
        status: 'completed',
        detail: '已保存 DAG 产物。',
        output: {
          kind: 'artifacts',
          artifacts: [
            {
              id: 'dag',
              name: 'build-task-plan.json',
              kind: 'json',
              confirmationStatus: 'pending',
              status: 'saved'
            }
          ],
          count: 1
        }
      }
    ],
    tasks: [
      {
        id: 'page-home',
        title: '实现首页',
        owner: 'frontend',
        status: 'pending',
        dependencies: [],
        changePaths: ['frontend/src/pages/Home.tsx'],
        acceptanceCriteria: ['首页可渲染']
      }
    ],
    summary: {
      unitCount: 2,
      taskCount: 1,
      edgeCount: 0,
      batchCount: 1,
      frontendCount: 1,
      dataSourceCount: 0,
      isValid: true
    },
    artifacts: [
      { id: 'plan', name: '内部 Build Task Plan', kind: 'internal', status: 'saved' },
      {
        id: 'dag',
        name: 'build-task-plan.json',
        kind: 'json',
        status: 'saved',
        confirmationStatus: 'pending'
      }
    ]
  })
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      steps: [
        {
          id: 'workflow:prepare_build_tasks',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 构建任务 DAG 生成',
          detail: '任务数=1',
          sequence: 1,
          dagGeneration: snapshot
        }
      ]
    })
  )

  assert.ok(snapshot)
  assert.match(markup, /生成 Unit DAG 骨架/)
  assert.match(markup, /实现首页/)
  assert.match(markup, /按 DAG 拓扑顺序排列，将在下一阶段执行/)
  assert.match(markup, /build-task-plan\.json/)
  assert.doesNotMatch(
    JSON.stringify(snapshot),
    /raw-model-output|\/workspace\/build-task-plan\.json/
  )
})

test('旧会话从节点完成事件恢复 DAG 生成详情', () => {
  const steps = processStepsForDisplay(undefined, {
    runId: 'run-dag-history',
    threadId: 'thread-dag-history',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'prepare_build_tasks',
        node: { label: '构建任务 DAG 生成' },
        status: 'completed',
        data: {
          detail: {
            dagGeneration: {
              stages: [
                {
                  id: 'unit_skeleton',
                  name: '生成 Unit DAG 骨架',
                  status: 'completed',
                  detail: '完成'
                }
              ],
              tasks: [
                {
                  id: 'page-home',
                  title: '实现首页',
                  owner: 'frontend',
                  status: 'pending',
                  dependencies: [],
                  changePaths: [],
                  acceptanceCriteria: []
                }
              ],
              summary: { unitCount: 1, taskCount: 1 },
              artifacts: []
            }
          }
        }
      }
    ]
  })

  assert.equal(steps?.[0].dagGeneration?.tasks[0]?.id, 'page-home')
})

test('实体数据源绑定把只读计划更新挂到正确执行轮次并默认展开', () => {
  const projectPlanUpdate = readProjectPlanUpdate({
    format: 'markdown',
    readOnly: true,
    documentName: 'project-plan.md',
    status: 'confirmed',
    targetType: 'page',
    targetId: 'inventory-page',
    summary: { pageCount: 1, endpointCount: 1 },
    sections: [
      {
        id: 'page:inventory-page',
        kind: 'page',
        title: '库存列表',
        subtitle: '/inventory',
        content: '### 库存列表 `/inventory`\n\n#### 页面基本信息\n\n- 页面目标：查看库存'
      },
      {
        id: 'endpoint:inventory-api:list-inventory',
        kind: 'endpoint',
        title: 'GET /api/inventory',
        subtitle: 'API 契约 · inventory-api',
        content: '# 实体数据源绑定：GET /api/inventory\n\n## 一、数据用途\n\n- 用途：查询库存'
      }
    ]
  })
  assert.ok(projectPlanUpdate)

  const steps = processStepsForDisplay(
    [
      {
        id: 'workflow:entity_source_binding',
        kind: 'workflow',
        status: 'completed',
        title: '已完成 实体数据源绑定',
        detail: '项目计划书已更新',
        sequence: 1,
        nodeName: 'entity_source_binding',
        attempt: 1
      },
      {
        id: 'workflow:entity_source_binding:2',
        kind: 'workflow',
        status: 'completed',
        title: '已完成 实体数据源绑定',
        detail: '项目计划书已更新',
        sequence: 2,
        nodeName: 'entity_source_binding',
        attempt: 2
      }
    ],
    {
      runId: 'run-plan-update',
      threadId: 'thread-plan-update',
      summary: { status: 'completed' },
      events: [
        {
          type: 'workflow.node.completed',
          nodeName: 'entity_source_binding',
          status: 'completed',
          attempt: 2,
          data: { detail: { projectPlanUpdate } }
        }
      ]
    }
  )

  assert.equal(steps?.[0].projectPlanUpdate, undefined)
  assert.equal(steps?.[1].projectPlanUpdate?.targetId, 'inventory-page')
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, { loading: false, steps: steps?.slice(1) || [] })
  )
  assert.match(markup, /PROJECT PLAN UPDATE/)
  assert.match(markup, /项目计划书本次更新/)
  assert.match(markup, /只读/)
  assert.match(markup, /库存列表/)
  assert.match(markup, /GET \/api\/inventory/)
  assert.match(markup, /<details[^>]*open=""/)
  assert.doesNotMatch(markup, /动作详情/)
})

test('损坏或缺失的计划更新快照继续使用旧动作摘要', () => {
  assert.equal(
    readProjectPlanUpdate({
      format: 'markdown',
      readOnly: true,
      status: 'confirmed',
      targetType: 'page',
      targetId: 'inventory-page',
      documentName: 'project-plan.md',
      sections: []
    }),
    undefined
  )

  const steps = processStepsForDisplay(undefined, {
    runId: 'run-plan-update-legacy',
    threadId: 'thread-plan-update-legacy',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'entity_source_binding',
        node: { label: '实体数据源绑定' },
        status: 'completed',
        message: '项目计划书已更新'
      }
    ]
  })
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, { loading: false, steps: steps || [] })
  )
  assert.match(markup, /项目计划书已更新/)
  assert.doesNotMatch(markup, /PROJECT PLAN UPDATE/)
})

test('集成测试步骤渲染具体检查项而不是数字详情', () => {
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      steps: [
        {
          id: 'workflow:integration_test',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 集成测试与质量门禁',
          detail: '已完成 2/2 项，通过 1 项，跳过 1 项',
          sequence: 1,
          checks: [
            {
              id: 'frontend_build',
              name: '前端构建检查',
              status: 'passed',
              required: true
            },
            {
              id: 'frontend_lint',
              name: '前端 lint 通过',
              status: 'skipped',
              required: false
            },
            {
              id: 'frontend_unit_tests',
              name: '前端单元测试',
              status: 'passed',
              required: true,
              passedTests: 3,
              totalTests: 3
            }
          ]
        }
      ]
    })
  )

  assert.match(markup, /前端构建检查/)
  assert.match(markup, /前端 lint 通过/)
  assert.match(markup, /QUALITY GATE/)
  assert.match(markup, /REQUIRED/)
  assert.match(markup, /OPTIONAL/)
  assert.match(markup, /已通过/)
  assert.match(markup, /已跳过/)
  assert.match(markup, /通过 3\/3 个测试/)
  assert.doesNotMatch(markup, /<pre>[^<]*已完成 2\/2 项，通过 1 项，跳过 1 项/)
})

test('局部修复完成态展示紫色结果卡并隐藏普通动作详情', () => {
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      steps: [
        {
          id: 'workflow:small_task_repair',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 局部修复任务',
          detail: 'SmallTask Agent 已处理 9 个结果，剩余任务=1',
          sequence: 1,
          nodeName: 'small_task_repair',
          attempt: 1
        }
      ]
    })
  )

  assert.match(markup, /AUTO REPAIR · COMPLETE/)
  assert.match(markup, /局部修复任务已完成/)
  assert.match(markup, /repair-completed/)
  assert.match(markup, /aria-busy="false"/)
  assert.match(markup, /SmallTask Agent 已处理 9 个结果/)
  assert.doesNotMatch(markup, /动作详情/)
})

test('单元测试生成期间展示运行中的集成检查矩阵', () => {
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: true,
      steps: [
        {
          id: 'workflow:integration_test',
          kind: 'workflow',
          status: 'running',
          title: '正在执行 集成测试与质量门禁',
          detail: '正在生成单元测试。',
          sequence: 1,
          checks: [
            {
              id: 'frontend_test_generation',
              name: '前端单元测试生成检查',
              status: 'running',
              required: true,
              evidence: '正在调用 TestGeneration Agent 生成或更新受影响的单元测试文件。'
            }
          ]
        }
      ]
    })
  )

  assert.match(markup, /集成检查矩阵/)
  assert.match(markup, /前端单元测试生成检查/)
  assert.match(markup, /正在调用 TestGeneration Agent/)
  assert.match(markup, /检查中/)
  assert.match(markup, /aria-busy="true"/)
  assert.match(markup, /anticon-spin/)
})

test('构建完成后展示单元测试跳过确认按钮', () => {
  const markup = renderToStaticMarkup(
    createElement(WorkflowRunCard, {
      interactionAvailability: 'active',
      workflow: {
        runId: 'run-unit-test-confirmation',
        threadId: 'thread-unit-test-confirmation',
        summary: {
          status: 'requires_user_input',
          phase: 'integration_test',
          message: '项目预览已就绪，请确认是否符合预期。 预览地址：http://127.0.0.1:3000。',
          clarification: {}
        },
        result: {
          clarification: {
            mode: 'unit_test_confirmation',
            status: 'requires_user_input',
            message: '构建检查已完成。单元测试不是必需步骤，可能耗时较长，是否跳过单元测试？',
            questions: [
              {
                id: 'unit_test_confirmation',
                header: '单元测试',
                question: '是否跳过单元测试？',
                type: 'choice',
                options: [
                  { label: '是，跳过单元测试', value: 'skip' },
                  { label: '否，继续执行', value: 'run' }
                ]
              }
            ]
          }
        },
        events: []
      }
    })
  )

  assert.match(markup, /是否跳过单元测试？/)
  assert.match(markup, /是，跳过单元测试/)
  assert.match(markup, /否，继续执行/)
  assert.match(markup, /待确认事项/)
  assert.doesNotMatch(markup, /项目预览已就绪/)
  assert.doesNotMatch(markup, /127\.0\.0\.1:3000/)
})

test('项目启动节点不展示已过期的前端性能测试确认', () => {
  const markup = renderToStaticMarkup(
    createElement(WorkflowRunCard, {
      interactionAvailability: 'stale',
      workflow: {
        runId: 'run-launch-with-stale-performance-confirmation',
        threadId: 'thread-launch-with-stale-performance-confirmation',
        summary: {
          status: 'running',
          phase: 'launch_project',
          clarification: {
            mode: 'frontend_performance_confirmation',
            status: 'requires_user_input',
            message: '单元测试已完成。是否跳过前端性能测试？',
            questions: [
              {
                id: 'frontend_performance_confirmation',
                header: '前端性能测试',
                question: '是否跳过前端性能测试？',
                type: 'choice',
                options: [
                  { label: '是，跳过性能测试', value: 'skip' },
                  { label: '否，继续执行', value: 'run' }
                ]
              }
            ]
          }
        },
        events: []
      }
    })
  )

  assert.match(markup, /正在启动项目预览/)
  assert.doesNotMatch(markup, /是否跳过前端性能测试/)
  assert.doesNotMatch(markup, /是，跳过性能测试/)
  assert.doesNotMatch(markup, /待确认事项/)
  assert.doesNotMatch(markup, /该确认已提交或已失效/)
})

test('UI 确认过渡帧缺少 clarification 时仍可正常渲染', () => {
  assert.doesNotThrow(() =>
    renderToStaticMarkup(
      createElement(WorkflowRunCard, {
        interactionAvailability: 'active',
        workflow: {
          runId: 'run-ui-confirmation-transition',
          threadId: 'thread-ui-confirmation-transition',
          summary: {
            status: 'running',
            phase: 'ui_confirmation',
            message: '正在跳过 UI 设计并进入技术规划。'
          },
          events: [],
          state: {},
          result: {}
        }
      })
    )
  )
})

test('正式工作流仅在开发交接展示代码差异', () => {
  const workflow = (phase: string): WorkflowRunPayload => ({
    runId: `run-${phase}`,
    threadId: 'thread-code-changes',
    summary: { status: 'completed', phase },
    events: []
  })

  assert.equal(workflowShouldShowCodeChanges(workflow('integration_test')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('test_phase_confirmation')), true)
  assert.equal(workflowShouldShowCodeChanges(workflow('review_phase_confirmation')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('code_review')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('acceptance_phase_confirmation')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('launch_project')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('acceptance_review')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('acceptance')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('finalize_project')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('completed')), false)
  assert.equal(workflowShouldShowCodeChanges(workflow('conversation')), true)
  assert.equal(workflowCodeChangesBeforeConfirmation(workflow('test_phase_confirmation')), true)
  assert.equal(workflowCodeChangesBeforeConfirmation(workflow('launch_project')), false)
})

test('开发与测试阶段分别保留当前会话', () => {
  const developmentSelected = withSelectedSessionForPhase(
    {},
    'frontend',
    'development',
    'development-session'
  )
  const bothSelected = withSelectedSessionForPhase(
    developmentSelected,
    'frontend',
    'test',
    'test-session'
  )

  assert.equal(
    selectedSessionIdForPhase(bothSelected, 'frontend', 'development'),
    'development-session'
  )
  assert.equal(selectedSessionIdForPhase(bothSelected, 'frontend', 'test'), 'test-session')
})

test('没有详情的步骤保持静态且只有验证步骤可以展开', () => {
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      steps: [
        {
          id: 'direct:classify_intent',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 识别修改意图',
          detail: '',
          sequence: 10
        },
        {
          id: 'direct:integration_test',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 验证项目',
          detail: '',
          sequence: 80,
          checks: [
            {
              id: 'frontend_build',
              name: '前端构建检查',
              status: 'passed',
              required: true
            }
          ]
        }
      ]
    })
  )

  assert.equal(markup.match(/<details/g)?.length, 2)
  assert.equal(markup.match(/<summary/g)?.length, 2)
  assert.match(markup, /class="[^"]*process-step[^"]*static"/)
  assert.match(markup, /已完成 识别修改意图/)
  assert.match(markup, /前端构建检查/)
})

test('验收阶段步骤只展示标题且等待确认时也不展开动作详情', () => {
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: false,
      waitingForInput: true,
      waitingPrompt: '不应展示的等待提示',
      steps: [
        {
          id: 'workflow:acceptance_phase_confirmation',
          kind: 'workflow',
          status: 'completed',
          title: '已完成 验收阶段确认',
          detail: '不应展示的确认详情',
          nodeName: 'acceptance_phase_confirmation',
          sequence: 1
        },
        {
          id: 'workflow:acceptance_review',
          kind: 'workflow',
          status: 'requires_user_input',
          title: '等待确认 用户验收',
          detail: '验收=False',
          nodeName: 'acceptance_review',
          sequence: 2
        },
        {
          id: 'workflow:launch_project',
          kind: 'workflow',
          status: 'completed',
          title: '已执行 启动本地预览',
          detail: '前后端服务均已就绪，可以开始预览。',
          nodeName: 'launch_project',
          sequence: 3
        },
        {
          id: 'workflow:finalize_project',
          kind: 'workflow',
          status: 'completed',
          title: '已执行 完成项目',
          detail: '正在执行：完成项目',
          nodeName: 'finalize_project',
          sequence: 4
        }
      ]
    })
  )

  assert.equal(markup.match(/<details/g)?.length, 1)
  assert.equal(markup.match(/<summary/g)?.length, 1)
  assert.match(markup, /已完成 验收阶段确认/)
  assert.match(markup, /等待确认 用户验收/)
  assert.match(markup, /已执行 启动本地预览/)
  assert.match(markup, /已执行 完成项目/)
  assert.doesNotMatch(markup, /动作详情|验收=False|前后端服务均已就绪|正在执行：完成项目/)
})

test('结构化步骤存在时隐藏重复 Workflow 摘要并保留真实回复', () => {
  const workflow = {
    runId: 'run-summary',
    threadId: 'thread-summary',
    summary: {
      status: 'requires_user_input',
      message: '项目预览已就绪，请确认是否符合预期。 预览地址：http://127.0.0.1:3000。',
      completedNodeCount: 2,
      failedEventCount: 0,
      timeline: []
    },
    events: [],
    state: {},
    result: {}
  }

  assert.equal(
    workflowMessageContentForDisplay(`${workflow.summary.message}\n`, workflow, true),
    ''
  )
  assert.equal(
    workflowMessageContentForDisplay('这是 Agent 生成的最终说明。', workflow, true),
    '这是 Agent 生成的最终说明。'
  )
  assert.equal(
    workflowMessageContentForDisplay(
      'Workflow 等待用户确认/补充：完成 2 个节点，待确认问题 0 个。 预览地址：http://127.0.0.1:3000。',
      undefined,
      true
    ),
    ''
  )
})

test('自由对话完成后仍保留与摘要相同的助手正文', () => {
  const workflow = {
    runId: 'conversation-summary',
    threadId: 'conversation-thread',
    summary: {
      status: 'completed',
      phase: 'conversation',
      intent: 'casual_chat',
      message: '我是 XCodeAgent。'
    },
    events: [],
    state: {},
    result: {}
  }

  assert.equal(
    workflowMessageContentForDisplay('我是 XCodeAgent。', workflow, true),
    '我是 XCodeAgent。'
  )
})

test('失败的技术规划历史隐藏模型 JSON 并保留结构化错误卡', () => {
  const workflow = {
    runId: 'technical-plan-error',
    threadId: 'technical-plan-error-thread',
    summary: {
      status: 'requires_user_input',
      phase: 'technical_planning',
      clarification: {
        mode: 'technical_plan_generation_error',
        status: 'requires_user_input',
        message: '技术规划自动修复后仍未通过校验。',
        errors: ['entities[0].fields[0] 缺少 label'],
        questions: []
      }
    },
    events: [],
    state: {},
    result: {}
  }

  assert.equal(isStructuredPlanningWorkflow(workflow), true)
  assert.equal(
    workflowMessageContentForDisplay('{"architecture":{"frontend":"React"}}', workflow, false),
    ''
  )
  const markup = renderToStaticMarkup(
    createElement(WorkflowRunCard, { interactionAvailability: 'active', workflow })
  )
  assert.match(markup, /技术规划自动修复后仍未通过校验/)
  assert.match(markup, /重新生成/)
  assert.doesNotMatch(markup, /architecture/)
})

test('失败终态仍可根据技术规划节点识别并隐藏历史 JSON', () => {
  const workflow = {
    runId: 'legacy-technical-plan-error',
    threadId: 'legacy-technical-plan-error-thread',
    summary: { status: 'failed', phase: 'failed' },
    events: [{ type: 'workflow.node.failed', nodeName: 'technical_planning' }],
    state: {},
    result: {}
  }

  assert.equal(isStructuredPlanningWorkflow(workflow), true)
  assert.equal(workflowMessageContentForDisplay('{"entities":[]}', workflow, true), '')
})

test('技术规划确认不依赖 Markdown confirmationArtifact 也能展示结构化摘要', () => {
  const workflow = {
    runId: 'technical-plan-confirmation',
    threadId: 'technical-plan-confirmation-thread',
    summary: {
      status: 'requires_user_input',
      phase: 'technical_planning',
      clarification: {
        mode: 'technical_plan_confirmation',
        status: 'requires_user_input',
        questions: []
      }
    },
    events: [],
    state: {
      technical_plan: {
        artifact_type: 'technical-plan',
        architecture: { frontend: 'React', backend: 'Spring Boot', data: 'MySQL' },
        entities: [],
        api_contracts: [],
        pages: []
      }
    },
    result: {}
  }
  const markup = renderToStaticMarkup(
    createElement(ApplicationPlanningQuestionPanel, {
      onReturnHome: () => undefined,
      onSaveRequirementSpec: async () => undefined,
      onSubmit: () => undefined,
      workflow
    })
  )

  assert.match(markup, /开发技术规划/)
  assert.match(markup, /React/)
  assert.doesNotMatch(markup, /结构化数据暂不可用/)
})

test('Electron 会话持久化保留 Agent 步骤、检查清单和工具调用', () => {
  const message = normalizePersistentSessionMessage({
    id: 1,
    role: 'assistant',
    content: 'done',
    createdAt: 2,
    processSteps: [
      {
        id: 'workflow:integration_test',
        kind: 'workflow',
        status: 'completed',
        title: '已完成 集成测试与质量门禁',
        detail: '检查完成',
        sequence: 1,
        checks: [
          {
            id: 'frontend_build',
            name: '前端构建检查',
            status: 'passed',
            required: true,
            evidence: '命令执行通过。'
          }
        ],
        dagGeneration: {
          stages: [
            { id: 'unit_skeleton', name: '生成 Unit DAG 骨架', status: 'completed', detail: '完成' }
          ],
          tasks: [
            {
              id: 'page-home',
              title: '实现首页',
              owner: 'frontend',
              status: 'pending',
              dependencies: [],
              changePaths: ['frontend/Home.tsx'],
              acceptanceCriteria: ['首页可渲染']
            }
          ],
          summary: { unitCount: 1, taskCount: 1 },
          artifacts: []
        },
        workspaceInspection: {
          schemaVersion: '1.0.0',
          revision: 'persisted-revision',
          cacheHit: true,
          fileManifest: { totalFiles: 42, sourceFiles: 30, truncated: false },
          techStack: ['React'],
          projectRoots: [
            { path: 'frontend/src', kind: 'frontend' },
            { path: '/private/workspace', kind: 'unsafe' }
          ],
          entrypoints: [{ path: 'frontend/src/main.tsx', kind: 'frontend_renderer' }],
          codeGraph: { provider: 'none', available: false }
        }
      }
    ],
    toolCalls: [
      {
        id: 'tool-1',
        name: 'read_file',
        args: '{"path":"README.md"}',
        result: 'ok',
        status: 'completed'
      }
    ]
  })

  assert.equal((message.processSteps as Array<Record<string, unknown>>).length, 1)
  assert.equal(
    ((message.processSteps as Array<Record<string, unknown>>)[0].checks as unknown[]).length,
    1
  )
  assert.equal(
    (
      (
        (message.processSteps as Array<Record<string, unknown>>)[0].dagGeneration as Record<
          string,
          unknown
        >
      ).tasks as unknown[]
    ).length,
    1
  )
  const persistedWorkspaceInspection = (message.processSteps as Array<Record<string, unknown>>)[0]
    .workspaceInspection as Record<string, unknown>
  assert.equal(persistedWorkspaceInspection.revision, 'persisted-revision')
  assert.equal((persistedWorkspaceInspection.projectRoots as unknown[]).length, 1)
  assert.doesNotMatch(JSON.stringify(persistedWorkspaceInspection), /private\/workspace/)
  assert.equal((message.toolCalls as Array<Record<string, unknown>>)[0].status, 'completed')
})

test('旧 session 可从 Workflow 完成事件重建 Agent 步骤和检查清单', () => {
  const steps = processStepsForDisplay(undefined, {
    runId: 'run-history',
    threadId: 'thread-history',
    summary: { status: 'requires_user_input' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'integration_test',
        node: { id: 'integration_test', label: '集成测试与质量门禁' },
        status: 'completed',
        message: '通过=True，检查=2/2',
        data: {
          detail: {
            testReport: {
              checks: [
                {
                  id: 'frontend_build',
                  name: '前端构建检查',
                  passed: true,
                  skipped: false,
                  required: true
                },
                {
                  id: 'frontend_lint',
                  name: '前端 lint 通过',
                  passed: true,
                  skipped: true,
                  required: false
                }
              ]
            }
          }
        }
      },
      {
        type: 'workflow.node.completed',
        nodeName: 'launch_project',
        node: { id: 'launch_project', label: '启动本地预览' },
        status: 'completed',
        message: '预览地址=http://127.0.0.1:3000'
      }
    ]
  })

  assert.deepEqual(
    steps?.map((step) => step.id),
    ['workflow:integration_test', 'workflow:launch_project']
  )
  assert.deepEqual(
    steps?.[0].checks?.map((check) => [check.name, check.status]),
    [
      ['前端构建检查', 'passed'],
      ['前端 lint 通过', 'skipped']
    ]
  )
})

test('多轮构建测试历史按 attempt 展开并把构建卡挂在对应步骤', () => {
  const buildSlice = {
    scope: { type: 'page' as const, targetId: 'orders' },
    tasks: [{ id: 'orders-page', title: '实现订单页', status: 'completed' }],
    summary: { total: 1, completed: 1, failed: 0, pending: 0 }
  }
  const steps = processStepsForDisplay(undefined, {
    runId: 'run-repair-history',
    threadId: 'thread-repair-history',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'build',
        node: { label: '代码生成与构建协调' },
        status: 'completed',
        attempt: 1,
        iterationKind: 'initial_build',
        data: { detail: { buildExecutionSlice: buildSlice } }
      },
      {
        type: 'workflow.node.completed',
        nodeName: 'integration_test',
        node: { label: '集成测试与质量门禁' },
        status: 'failed',
        attempt: 1,
        iterationKind: 'initial_test'
      },
      {
        type: 'workflow.node.completed',
        nodeName: 'build',
        node: { label: '代码生成与构建协调' },
        status: 'completed',
        attempt: 2,
        iterationKind: 'repair_build',
        data: { detail: { buildExecutionSlice: buildSlice } }
      },
      {
        type: 'workflow.node.completed',
        nodeName: 'integration_test',
        node: { label: '集成测试与质量门禁' },
        status: 'completed',
        attempt: 2,
        iterationKind: 'retest'
      }
    ]
  })

  assert.deepEqual(
    steps?.map((step) => [step.id, step.status, step.iterationKind]),
    [
      ['workflow:build', 'completed', 'initial_build'],
      ['workflow:integration_test', 'failed', 'initial_test'],
      ['workflow:build:2', 'completed', 'repair_build'],
      ['workflow:integration_test:2', 'completed', 'retest']
    ]
  )
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, { loading: false, steps: steps || [] })
  )
  assert.equal((markup.match(/构建执行/g) || []).length, 2)
  assert.ok(markup.indexOf('构建执行') < markup.indexOf('集成测试与质量门禁'))
})

test('集成测试重试轮次不继承上一轮失败的检查快照', () => {
  const oldChecks = [
    {
      id: 'frontend_build',
      name: '前端构建检查',
      passed: false,
      required: true
    }
  ]
  const steps = processStepsForDisplay(
    [
      {
        id: 'workflow:integration_test',
        kind: 'workflow',
        status: 'failed',
        title: '执行失败 集成测试与质量门禁',
        detail: '上一轮失败',
        sequence: 1,
        nodeName: 'integration_test',
        attempt: 1,
        checks: oldChecks
      },
      {
        id: 'workflow:small_task_repair',
        kind: 'workflow',
        status: 'completed',
        title: '已完成 局部修复任务',
        detail: '修复完成',
        sequence: 2,
        nodeName: 'small_task_repair',
        attempt: 1
      },
      {
        id: 'workflow:integration_test:2',
        kind: 'workflow',
        status: 'running',
        title: '正在执行 集成测试与质量门禁',
        detail: '正在重新执行检查',
        sequence: 3,
        nodeName: 'integration_test',
        attempt: 2,
        checks: [
          {
            id: 'frontend_install',
            name: '前端依赖安装检查',
            status: 'running',
            required: true
          }
        ]
      }
    ],
    {
      runId: 'run-retest-check-reset',
      threadId: 'thread-retest-check-reset',
      summary: { status: 'running', phase: 'integration_test' },
      events: [
        {
          type: 'workflow.node.completed',
          nodeName: 'integration_test',
          status: 'failed',
          attempt: 1,
          data: { detail: { testReport: { checks: oldChecks } } }
        }
      ],
      state: { testReport: { checks: oldChecks } }
    }
  )

  const retestStep = steps?.find((step) => step.id === 'workflow:integration_test:2')
  assert.deepEqual(
    retestStep?.checks?.map((check) => [check.id, check.status]),
    [['frontend_install', 'running']]
  )
})

test('运行中任务默认折叠并只显示最新工具活动', () => {
  const task = {
    id: 'home-page',
    title: '实现概览页',
    description: '生成页面主体',
    status: 'running' as const,
    activeToolActivity: {
      callId: 'edit-home',
      tool: 'edit_file' as const,
      category: 'write' as const,
      status: 'running' as const,
      message: '正在编辑文件：/apps/demo/frontend/src/pages/Home/index.tsx',
      path: '/apps/demo/frontend/src/pages/Home/index.tsx'
    }
  }
  const markup = renderToStaticMarkup(
    createElement(ProcessSteps, {
      loading: true,
      steps: [
        {
          id: 'workflow:build',
          kind: 'workflow',
          status: 'running',
          title: '正在执行 代码生成与构建协调',
          detail: '正在执行构建任务：home-page',
          sequence: 1,
          nodeName: 'build',
          buildExecutionSlice: {
            scope: { type: 'page', targetId: 'home' },
            tasks: [task],
            summary: { total: 1, running: 1, completed: 0, failed: 0, pending: 0 }
          }
        }
      ]
    })
  )

  assert.equal((markup.match(/aria-live="polite"/g) || []).length, 1)
  assert.ok(markup.includes('正在编辑文件'))
  assert.ok(markup.includes('workflow-build-tool-activity'))
  assert.equal(buildToolActivityPlacement(task, false), 'header')
  assert.equal(buildToolActivityPlacement(task, true), 'details')
  assert.equal(buildToolActivityPlacement({ ...task, status: 'completed' }, false), undefined)
})

test('更早的 session 可从 timeline 和 snake_case 测试报告恢复步骤', () => {
  const steps = processStepsForDisplay(undefined, {
    runId: 'run-legacy',
    threadId: 'thread-legacy',
    summary: { status: 'requires_user_input' },
    events: [],
    result: {
      timeline: ['integration_test', 'launch_project', 'integration_test'],
      test_report: {
        checks: [
          {
            id: 'api_contract',
            name: 'API 契约有效',
            passed: true,
            skipped: false,
            required: true
          }
        ]
      }
    }
  })

  assert.deepEqual(
    steps?.map((step) => step.id),
    ['workflow:integration_test', 'workflow:launch_project']
  )
  assert.deepEqual(
    steps?.[0].checks?.map((check) => [check.name, check.status]),
    [['API 契约有效', 'passed']]
  )
})

test('发送清空草稿标签，认证失败可恢复独立快照', () => {
  const selected = [{ name: 'alpha', description: 'instructions' }]
  const optimistic = beginOptimisticSkillSend(selected)

  assert.deepEqual(optimistic.messageSkills, selected)
  assert.deepEqual(optimistic.nextDraftSkills, [])
  assert.deepEqual(rollbackSkillSelection(optimistic.messageSkills), selected)
  assert.notEqual(rollbackSkillSelection(optimistic.messageSkills), optimistic.messageSkills)
  assert.deepEqual(selectedSkillNames(optimistic.messageSkills), ['alpha'])
})

test('会话恢复只保留有效技能名称与描述字段', () => {
  assert.deepEqual(
    normalizeMessageSkills([
      { name: 'alpha', description: 'first', unsafe: true },
      { name: 'alpha', description: 'duplicate' },
      { name: '', description: 'invalid' }
    ]),
    [{ name: 'alpha', description: 'first' }]
  )
})

test('会话恢复保留有效的二次修改交接回执并拒绝残缺回执', () => {
  const normalized = normalizePersistentSessionMessage({
    id: 1,
    role: 'assistant',
    content: '',
    createdAt: 1,
    revisionHandoff: {
      kind: 'formal_revision',
      formalBranch: 'workbench_plan_revision',
      targetSessionId: 'revision-session',
      targetConversationThreadId: 'revision-thread',
      impactInteractionId: 'impact-1',
      request: '把订单页改成双列布局',
      unsafe: 'ignored'
    }
  })
  const malformed = normalizePersistentSessionMessage({
    id: 2,
    role: 'assistant',
    content: '',
    createdAt: 2,
    revisionHandoff: {
      kind: 'formal_revision',
      formalBranch: 'workbench_plan_revision',
      targetSessionId: 'revision-session'
    }
  })

  assert.deepEqual(normalized.revisionHandoff, {
    kind: 'formal_revision',
    formalBranch: 'workbench_plan_revision',
    targetSessionId: 'revision-session',
    targetConversationThreadId: 'revision-thread',
    impactInteractionId: 'impact-1',
    request: '把订单页改成双列布局'
  })
  assert.equal('revisionHandoff' in malformed, false)

  const development = normalizePersistentSessionMessage({
    id: 3,
    role: 'assistant',
    content: '',
    createdAt: 3,
    revisionHandoff: {
      kind: 'revision_development',
      formalBranch: 'design_stage_revision',
      targetSessionId: 'development-session',
      targetConversationThreadId: 'development-thread',
      impactInteractionId: 'impact-1',
      changeId: 'change-1',
      request: '新增报表页'
    }
  })
  assert.equal(
    (development.revisionHandoff as Record<string, unknown>).changeId,
    'change-1'
  )
})

test('二次修改会话身份必须完整绑定来源、原规划线程和可选 changeId', () => {
  assert.deepEqual(
    normalizeRevisionSessionContext({
      kind: 'formal_revision',
      sessionRole: 'design',
      formalBranch: 'workbench_plan_revision',
      impactInteractionId: 'impact-1',
      sourceSessionId: 'source-session',
      sourceConversationThreadId: 'source-thread',
      sourceRunId: 'source-run',
      planningThreadId: 'planning-thread',
      changeId: 'change-1',
      unsafe: 'ignored'
    }),
    {
      kind: 'formal_revision',
      sessionRole: 'design',
      formalBranch: 'workbench_plan_revision',
      impactInteractionId: 'impact-1',
      sourceSessionId: 'source-session',
      sourceConversationThreadId: 'source-thread',
      sourceRunId: 'source-run',
      planningThreadId: 'planning-thread',
      changeId: 'change-1'
    }
  )
  assert.equal(
    normalizeRevisionSessionContext({
      kind: 'formal_revision',
      sessionRole: 'design',
      formalBranch: 'workbench_plan_revision',
      impactInteractionId: 'impact-1',
      sourceSessionId: 'source-session',
      sourceConversationThreadId: 'source-thread',
      planningThreadId: 'planning-thread'
    }),
    undefined
  )
})

test('输入文本为空时 Backspace 依次删除最后一个技能标签', () => {
  const skills = [
    { name: 'alpha', description: 'first' },
    { name: 'beta', description: 'second' }
  ]

  assert.deepEqual(skillsAfterEmptyBackspace('Backspace', '', skills), [skills[0]])
  assert.equal(skillsAfterEmptyBackspace('Backspace', 'hello', skills), undefined)
  assert.equal(skillsAfterEmptyBackspace('Enter', '', skills), undefined)
  assert.equal(skillsAfterEmptyBackspace('Backspace', '', []), undefined)
})

test('技能页面默认展示用户分类并按当前分类搜索', () => {
  assert.equal(DEFAULT_SKILL_CATEGORY, 'user')
  assert.deepEqual(
    filterCatalogSkills(skillCatalog, 'user', 'disabled').map((skill) => skill.name),
    ['beta']
  )
  assert.deepEqual(
    filterCatalogSkills(skillCatalog, 'builtin', 'react').map((skill) => skill.name),
    ['builtin-react']
  )
})

test('聊天技能目录隐藏关闭项并清理陈旧选择', () => {
  assert.deepEqual(
    enabledUserSkills(skillCatalog.skills).map((skill) => skill.name),
    ['alpha']
  )
  assert.deepEqual(
    reconcileEnabledChatSkills(
      [
        { name: 'alpha', description: 'old description' },
        { name: 'beta', description: 'disabled' },
        { name: 'missing', description: 'missing' }
      ],
      skillCatalog.skills
    ),
    [{ name: 'alpha', description: 'First user skill' }]
  )
})

test('工作区文件路径拆分后保留完整目录和最终文件名', () => {
  assert.deepEqual(
    splitWorkspacePath(
      'Frontend/src/renderer/src/components/AiChatPanel/components/CodeDiffDetailPanel/index.tsx'
    ),
    {
      directory: 'Frontend/src/renderer/src/components/AiChatPanel/components/CodeDiffDetailPanel',
      fileName: 'index.tsx'
    }
  )
  assert.deepEqual(splitWorkspacePath('Backend\\app\\main.py'), {
    directory: 'Backend/app',
    fileName: 'main.py'
  })
})

test('历史变更路径包含工作区根目录且 Diff 默认宽度为 500px', () => {
  assert.equal(workspaceCodeChangeDisplayPath('aa/b.js', '/Users/example/c', 'c'), 'c/aa/b.js')
  assert.equal(workspaceCodeChangeDisplayPath('aa\\b.js', 'C:\\workspace\\c'), 'c/aa/b.js')
  assert.equal(DEFAULT_DIFF_PANEL_WIDTH, 500)
})
