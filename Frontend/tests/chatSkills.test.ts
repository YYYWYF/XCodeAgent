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
  readDagGenerationSnapshot
} from '../src/renderer/src/service/agUiAgent'
import ProcessSteps from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps'
import { buildToolActivityPlacement } from '../src/renderer/src/components/AiChatPanel/components/WorkflowRunCard'
import {
  processStepsForDisplay,
  workflowMessageContentForDisplay
} from '../src/renderer/src/service/processStepHistory'
import { normalizePersistentSessionMessage } from '../src/main/sessionMessageNormalization'
import { normalizeMessageSkills } from '../src/renderer/src/service/chatSessions'
import { DEFAULT_DIFF_PANEL_WIDTH } from '../src/renderer/src/components/AiChatPanel/constants'
import {
  splitWorkspacePath,
  workspaceCodeChangeDisplayPath
} from '../src/renderer/src/components/AiChatPanel/utils'
import {
  DEFAULT_SKILL_CATEGORY,
  enabledUserSkills,
  filterCatalogSkills,
  reconcileEnabledChatSkills
} from '../src/renderer/src/components/SkillsPage/skillCatalog'
import type { UserSkillCatalog } from '../src/renderer/src/typings'

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
    directModification: true
  })

  assert.deepEqual(forwardedProps.directModification, {
    workspaceRoot: '/workspace',
    selectedSkillNames: ['alpha']
  })
  assert.equal(
    Object.hasOwn(forwardedProps.directModification as Record<string, unknown>, 'target'),
    false
  )
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
        phase: 'direct_modification',
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
      { type: 'CUSTOM', name: 'direct-modification', value: runningValue },
      { type: 'CUSTOM', name: 'direct-modification', value },
      { type: 'TEXT_MESSAGE_CONTENT', messageId, delta: '快速修改完成' },
      { type: 'TEXT_MESSAGE_END', messageId },
      { type: 'RUN_FINISHED', threadId, runId, result: { directModification: value } }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'content-type': 'text/event-stream' },
      status: 200
    })
  }

  try {
    const session = new AgUiChatSession(
      'thread-direct',
      'http://agent.test/direct-modification/run'
    )
    const result = await session.sendMessage('修改页面样式', {
      editorMode: 'frontend',
      workspaceRoot: '/workspace',
      directModification: true,
      onWorkflow: (workflow) => {
        workflowStatus = String(workflow.summary.status)
      },
      onProcessSteps: (steps) => {
        processStepCount = steps.length
        processStepStatuses.push(String(steps[0]?.status))
      }
    })
    assert.equal(result.workflow?.summary.phase, 'direct_modification')
  } finally {
    globalThis.fetch = originalFetch
  }

  assert.equal(workflowStatus, 'completed')
  assert.equal(processStepCount, 1)
  assert.deepEqual(processStepStatuses, ['running', 'completed'])
})

test('AG-UI 继续执行只发送旧 runId 作为资源锁转移令牌', () => {
  const forwardedProps = buildWorkflowForwardedProps({
    editorMode: 'frontend',
    resumeExecutionRunId: 'run-stopped'
  })

  assert.equal(forwardedProps.resumeExecutionRunId, 'run-stopped')
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

test('DAG 快照解析和展示不暴露模型原文或内部 JSON', () => {
  const snapshot = readDagGenerationSnapshot({
    agent_note: 'raw-model-output',
    buildTaskPlanPath: '/workspace/build-task-plan.json',
    stages: [
      { id: 'unit_skeleton', name: '生成 Unit DAG 骨架', status: 'completed', detail: '已完成' },
      { id: 'model_planning', name: '生成候选构建任务', status: 'completed', detail: '已生成 1 项' }
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
        name: 'BUILD_TASK_DAG.md',
        kind: 'markdown',
        status: 'saved',
        path: '/workspace/BUILD_TASK_DAG.md'
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
  assert.match(markup, /BUILD_TASK_DAG\.md/)
  assert.doesNotMatch(JSON.stringify(snapshot), /raw-model-output|build-task-plan\.json/)
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
  assert.doesNotMatch(markup, /<pre>[^<]*已完成 2\/2 项，通过 1 项，跳过 1 项/)
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
