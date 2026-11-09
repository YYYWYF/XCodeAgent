import { normalizePersistentSessionMessage } from '../src/main/sessionMessageNormalization'
import { latestConversationFailure } from '../src/renderer/src/components/AiChatPanel/globalFallbackState'
import assert from 'node:assert/strict'
import { test } from 'node:test'
import { Children, createElement, isValidElement, type ReactNode } from 'react'
import { Button, message, Modal } from 'antd'
import { renderToStaticMarkup } from 'react-dom/server'
import { AgUiChatSession, type ProcessStepRecord } from '../src/renderer/src/service/agUiAgent'
import { readAuthorizationBootstrap } from '../src/renderer/src/service/authorizationBootstrap'
import { processStepsForDisplay } from '../src/renderer/src/service/processStepHistory'
import type { WorkflowRunPayload } from '../src/renderer/src/typings'
import {
  AuthorizationBootstrapRecoveryContext,
  BuiltinAuthorizationActionsContext,
  AuthorizationBootstrapRetryNotice,
  authorizationBootstrapRecoveryOwner
} from '../src/renderer/src/components/AiChatPanel/authorizationBootstrapRecovery'
import ProcessSteps from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps'
import RecoverySurface from '../src/renderer/src/components/AiChatPanel/recoverySurface'
import type { ExecutionRecoveryCandidate } from '../src/renderer/src/typings'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import { ProcessStep } from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps'
import Panel from '../src/renderer/src/components/AiChatPanel/components/ProcessSteps/AuthorizationBootstrapPanel'
import { refreshBuiltinAuthorization, runBuiltinPatch } from '../src/renderer/src/service/builtinAuthorization'
import BuiltinPatchCard from '../src/renderer/src/components/AiChatPanel/components/WorkspaceDebugDock/BuiltinPatchCard'
import './authorizationBootstrapTask.test'

test('Builtin 自动运行隐藏人工按钮，显式原操作查询保持单次 AG-UI', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const requests: Record<string, unknown>[] = []
  const builtin = { status: 'manual_required', executor: 'builtin_saas', operationStatus: 'RUNNING', operationId: '123', schemaReady: false, initializationStatus: 'running', initializationStage: 'schema' }
  const markup = renderToStaticMarkup(createElement(Panel, { result: readAuthorizationBootstrap(builtin)!, workspaceRoot: '/workspace' }))
  assert.match(markup, /正在自动处理/)
  assert.doesNotMatch(markup, /刷新执行状态|继续初始化权限数据/)
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://authorization.test' } } })
  globalThis.fetch = async (url, init) => {
    assert.equal(String(url), 'http://authorization.test/builtin-authorization/run')
    const payload = JSON.parse(String(init?.body))
    requests.push(payload.forwardedProps)
    const patch = payload.forwardedProps.builtinAuthorization.action === 'run_patch'
    const value = patch
      ? { schemaVersion: 1, status: 'completed', patch: {
          stage: 'done', status: 'ready', schemaReady: true, seedReady: true, message: '权限数据已回读核验。',
          createdCount: 5, existingCount: 0, resourceCount: 2, roleGrantCount: 1
        } }
      : { schemaVersion: 1, status: 'completed', authorizationBootstrap: builtin }
    const events = [
      { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
      ...(patch ? [{ type: 'CUSTOM', name: 'builtin-authorization', value: {
        status: 'in_progress', progress: { stage: 'seed', message: '正在核对原写入记录…' }
      } }] : []),
      { type: 'CUSTOM', name: 'builtin-authorization', value },
      { type: 'STATE_SNAPSHOT', snapshot: { builtinAuthorization: value } },
      { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), { headers: { 'Content-Type': 'text/event-stream' } })
  }
  try {
    const result = await refreshBuiltinAuthorization('/workspace', '123')
    assert.equal(result.operationStatus, 'RUNNING')
    assert.deepEqual(requests, [{ builtinAuthorization: { action: 'refresh', workspaceRoot: '/workspace', operationId: '123' } }])
    const restored = normalizePersistentSessionMessage({ id: 1, role: 'assistant', content: '', processSteps: [{ id: 'bootstrap', kind: 'workflow', nodeName: 'builtin_authorization_bootstrap', title: '权限', status: 'completed', authorizationBootstrap: result }] }) as unknown as AgentChatMessage
    assert.equal(restored.processSteps?.[0].authorizationBootstrap?.operationStatus, 'RUNNING')
    const progress: string[] = []
    const patched = await runBuiltinPatch('/workspace', 'run_patch', (stage) => progress.push(stage))
    assert.deepEqual(progress, ['seed'])
    assert.deepEqual(requests[1], { builtinAuthorization: { action: 'run_patch', workspaceRoot: '/workspace' } })
    const receipt = renderToStaticMarkup(createElement(BuiltinPatchCard, {
      card: { result: patched! }, disabled: false
    }))
    assert.match(receipt, /新增 5 条，复用 0 条/)
    assert.match(receipt, /资源点 2 条，角色授权 1 条/)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
})

const ddl =
  'CREATE TABLE role (id int);\n-- <script>alert("unsafe")</script>\n' + 'SELECT 1;\n'.repeat(4000)
const result = {
  status: 'manual_required' as const,
  datasourceMode: 'dbid' as const,
  ddlAvailable: true,
  ddlPath: 'backend/sql/authorization/schema.sql',
  ddl,
  message: '当前数据源需手动执行权限 DDL。',
  requiresManualAction: true,
  executionAttempted: false
}

test('Builtin 自动阶段经历史与 Electron 落盘保留真实状态', () => {
  const builtin = {
    status: 'executed' as const,
    executor: 'builtin_saas' as const,
    datasourceMode: 'builtin' as const,
    schemaReady: true,
    seedReady: false,
    initializationStatus: 'running' as const,
    initializationStage: 'seed' as const,
    operationId: '123',
    initializedTables: ['cf2b2661f3e9_role', 'cf2b2661f3e9_resource', 'cf2b2661f3e9_role_resource', 'cf2b2661f3e9_role_member'],
    message: '正在自动处理，无需手动刷新。'
  }
  const current = workflow()
  current.events = [
    {
      ...current.events[0],
      nodeName: 'builtin_authorization_bootstrap',
      data: { detail: { authorizationBootstrap: builtin } }
    }
  ]
  const steps = processStepsForDisplay(undefined, current)!
  assert.equal(steps[0].title, '权限数据库初始化运行中')
  const restored = normalizePersistentSessionMessage({
    id: 1,
    role: 'assistant',
    content: '',
    workflow: current,
    processSteps: steps
  }) as unknown as AgentChatMessage
  assert.deepEqual(
    restored.processSteps![0].authorizationBootstrap,
    readAuthorizationBootstrap(builtin)
  )
  const markup = renderToStaticMarkup(createElement(Panel, { result: builtin }))
  assert.match(markup, /权限数据库初始化/)
  assert.match(markup, /正在自动查询数据写入结果/)
  assert.doesNotMatch(markup, /Seed|seed/)
  assert.match(markup, /以下四张权限表已初始化完成/)
  for (const name of builtin.initializedTables) assert.ok(markup.includes(name))
  assert.doesNotMatch(markup, /权限数据库初始化成功|复制完整 DDL|已尝试执行脚本/)
})

test('当前与历史 Run 都不提供手动 Seed 继续入口', () => {
  const current = workflow()
  current.events = [{
    ...current.events[0],
    nodeName: 'builtin_authorization_bootstrap',
    data: { detail: { authorizationBootstrap: {
      status: 'executed', executor: 'builtin_saas', schemaReady: true, seedReady: false,
      operationId: '123', nextAction: 'continue'
    } } }
  }]
  /** 用不同 Run 身份渲染真实 ProcessSteps，检查动作是否绑定当前节点。 */
  function render(runId: string): string {
    return renderToStaticMarkup(createElement(BuiltinAuthorizationActionsContext.Provider, {
      value: { runId, threadId: current.threadId, continueDisabled: false }
    }, createElement(ProcessSteps, {
      steps: processStepsForDisplay(undefined, current)!, workflow: current, loading: false, workspaceRoot: '/workspace'
    })))
  }
  assert.doesNotMatch(render(current.runId), /继续初始化权限数据/)
  assert.doesNotMatch(render('old-run'), /继续初始化权限数据/)
})

test('Seed 刷新使用独立操作编号，校验响应身份及安全字段', async () => {
  const originalWindow = globalThis.window
  const originalFetch = globalThis.fetch
  const seed = {
    status: 'manual_required', executor: 'builtin_saas', schemaReady: true, seedReady: false,
    operationId: '123', operationStatus: 'SCHEMA_READY', seedOperationId: '456',
    seedOperationStatus: 'RUNNING', nextAction: 'refresh_seed', initializationStatus: 'running', initializationStage: 'seed', initialAdministratorSubjects: ['private-subject']
  }
  const pending = readAuthorizationBootstrap(seed)!
  const markup = renderToStaticMarkup(createElement(Panel, { result: pending, workspaceRoot: '/workspace' }))
  assert.match(markup, /正在自动查询数据写入结果/)
  assert.match(markup, /运行中/)
  assert.doesNotMatch(markup, /刷新执行状态|继续初始化权限数据/)
  assert.doesNotMatch(markup, /private-subject/)
  Object.defineProperty(globalThis, 'window', { configurable: true, value: { devAgentStudio: { agentBaseUrl: 'http://authorization.test' } } })
  let requests = 0
  let seedOperationId = '456'
  globalThis.fetch = async (_url, init) => {
    const payload = JSON.parse(String(init?.body))
    requests += 1
    assert.deepEqual(payload.forwardedProps.builtinAuthorization, {
      action: 'refresh', workspaceRoot: '/workspace', operationKind: 'seed', operationId: '456'
    })
    const value = { status: 'completed', authorizationBootstrap: {
      ...seed, status: 'executed', seedOperationId, seedReady: true, seedOperationStatus: 'SUCCEEDED', nextAction: 'none'
    } }
    const events = [
      { type: 'RUN_STARTED', runId: payload.runId, threadId: payload.threadId },
      { type: 'CUSTOM', name: 'builtin-authorization', value },
      { type: 'RUN_FINISHED', runId: payload.runId, threadId: payload.threadId }
    ]
    return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  }
  try {
    const ready = await refreshBuiltinAuthorization('/workspace', '456', 'seed')
    assert.equal(ready.seedReady, true)
    assert.equal(ready.operationId, '123')
    assert.equal(requests, 1)
    assert.doesNotMatch(JSON.stringify(ready), /private-subject/)
    const readyMarkup = renderToStaticMarkup(createElement(Panel, { result: ready, workspaceRoot: '/workspace' }))
    assert.match(readyMarkup, /权限表与权限数据均已核验就绪/)
    assert.doesNotMatch(readyMarkup, /刷新执行状态|继续初始化权限数据/)
    seedOperationId = '789'
    await assert.rejects(refreshBuiltinAuthorization('/workspace', '456', 'seed'), /与原操作不符/)
    assert.equal(requests, 2)
  } finally {
    globalThis.fetch = originalFetch
    Object.defineProperty(globalThis, 'window', { configurable: true, value: originalWindow })
  }
})

test('Builtin 未完成结果显示待核对且不提供脚本或 SQL 重放入口', () => {
  const builtin = readAuthorizationBootstrap({
    status: 'manual_required',
    executor: 'builtin_saas',
    schemaReady: false,
    seedReady: false,
    operationId: '123',
    message: '原操作仍在运行，不会重复建表。'
  })!
  const markup = renderToStaticMarkup(createElement(Panel, { result: builtin }))
  assert.match(markup, /待处理/)
  assert.match(markup, /权限数据库初始化/)
  assert.doesNotMatch(markup, /已就绪|已尝试执行脚本|复制完整 DDL/)
})

/** 构造带节点轮次的持久化完成事件，测试恢复合并边界。 */
function workflow(): WorkflowRunPayload {
  return {
    runId: 'run',
    threadId: 'thread',
    summary: { status: 'completed' },
    events: [
      {
        type: 'workflow.node.completed',
        nodeName: 'authorization_bootstrap',
        attempt: 1,
        status: 'completed',
        data: { detail: { authorizationBootstrap: result } }
      },
      {
        type: 'workflow.node.completed',
        nodeName: 'authorization_bootstrap',
        attempt: 2,
        status: 'completed',
        data: { detail: { authorizationBootstrap: { status: 'executed' } } }
      }
    ]
  } as WorkflowRunPayload
}

test('有界解析拒绝超长 SQL，剔除未知字段及成功状态的 DDL', () => {
  assert.equal(
    readAuthorizationBootstrap({ ...result, ddl: '中'.repeat(100000) })?.ddlAvailable,
    false
  )
  assert.equal(readAuthorizationBootstrap({ ...result, status: 'executed' })?.ddl, undefined)
  assert.equal(
    (
      readAuthorizationBootstrap({ ...result, password: 'secret' }) as unknown as Record<
        string,
        unknown
      >
    ).password,
    undefined
  )
  assert.equal(readAuthorizationBootstrap({ status: 'unexpected' }), undefined)
  assert.equal(readAuthorizationBootstrap(result)?.ddl, ddl)
})

test('历史步骤与实时步骤按节点轮次合并，不重复且新成功不继承旧 SQL', () => {
  const steps = processStepsForDisplay(undefined, workflow())!
  assert.equal(steps.length, 2)
  assert.equal(steps[0].authorizationBootstrap?.ddl, ddl)
  assert.equal(steps[1].authorizationBootstrap?.ddl, undefined)
  const recovered = processStepsForDisplay(
    steps.map(({ authorizationBootstrap: _, ...step }) => step),
    workflow()
  )!
  assert.equal(recovered.length, 2)
  assert.equal(recovered[0].authorizationBootstrap?.ddl, ddl)
  assert.equal(recovered[1].authorizationBootstrap?.status, 'executed')
})

test('警告显示完整 SQL 并转义，缺少 DDL 和成功状态不提供复制按钮', () => {
  const markup = renderToStaticMarkup(createElement(Panel, { result }))
  assert.match(markup, /复制完整 DDL/)
  assert.match(markup, /<details[^>]*><summary>查看完整 DDL<\/summary>/)
  assert.doesNotMatch(markup, /<details[^>]*\bopen=/)
  assert.equal(markup.split(result.message).length - 1, 1)
  assert.match(markup, /Build 已继续/)
  assert.match(markup, /&lt;script&gt;/)
  assert.ok(!markup.includes('<script>'))
  assert.equal((markup.match(/SELECT 1;/g) || []).length, 4000)
  for (const state of [
    { ...result, ddl: undefined, ddlAvailable: false },
    { status: 'executed' as const }
  ]) {
    assert.ok(
      !renderToStaticMarkup(createElement(Panel, { result: state })).includes('复制完整 DDL')
    )
  }
})

test('AG-UI SDK 实时帧与历史恢复保留同一完整 DDL', async () => {
  const savedFetch = globalThis.fetch
  const frames = [
    { type: 'RUN_STARTED', threadId: 'thread', runId: 'run' },
    {
      type: 'CUSTOM',
      name: 'agent-process',
      value: {
        id: 'workflow:authorization_bootstrap',
        kind: 'workflow',
        status: 'completed',
        title: '权限数据库初始化',
        detail: 'Build 已继续',
        sequence: 1,
        nodeName: 'authorization_bootstrap',
        attempt: 1,
        authorizationBootstrap: result
      }
    },
    { type: 'RUN_FINISHED', threadId: 'thread', runId: 'run' }
  ]
  globalThis.fetch = async () =>
    new Response(frames.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(''), {
      headers: { 'Content-Type': 'text/event-stream' }
    })
  let steps: ProcessStepRecord[] = []
  try {
    const session = new AgUiChatSession('thread', 'https://example.invalid/workflow/run')
    await session.sendMessage('继续', {
      editorMode: 'frontend',
      onProcessSteps: (value) => {
        steps = value
      }
    })
    assert.equal(steps[0].authorizationBootstrap?.ddl, ddl)
    const recovered = processStepsForDisplay(steps, workflow())!
    assert.equal(recovered.length, 2)
    assert.equal(recovered[0].authorizationBootstrap?.ddl, ddl)
  } finally {
    globalThis.fetch = savedFetch
  }
})

test('复制按钮只写入完整 SQL，不改变初始化状态', async () => {
  const saved = Object.getOwnPropertyDescriptor(globalThis, 'navigator')
  const copied: string[] = []
  Object.defineProperty(globalThis, 'navigator', {
    configurable: true,
    value: {
      clipboard: {
        writeText: async (text: string) => {
          copied.push(text)
        }
      }
    }
  })
  const savedSuccess = message.success
  message.success = (() => undefined) as unknown as typeof message.success
  /** 从无状态组件返回的元素树定位复制按钮，执行真实事件闭包。 */
  function copyAction(node: ReactNode): (() => Promise<void>) | undefined {
    for (const child of Children.toArray(node)) {
      if (!isValidElement<{ children?: ReactNode; onClick?: () => Promise<void> }>(child)) continue
      if (child.type === Button) return child.props.onClick
      const action = copyAction(child.props.children)
      if (action) return action
    }
    return undefined
  }
  try {
    const action = copyAction(Panel({ result }))
    assert.ok(action)
    await action()
    assert.deepEqual(copied, [ddl])
    assert.equal(result.status, 'manual_required')
  } finally {
    message.success = savedSuccess
    if (saved) Object.defineProperty(globalThis, 'navigator', saved)
    else Reflect.deleteProperty(globalThis, 'navigator')
  }
})

test('权限结果仅在动作详情呈现一次', () => {
  const current = workflow()
  current.events = [current.events[0]]
  const step = processStepsForDisplay(undefined, current)![0]
  const markup = renderToStaticMarkup(
    createElement(ProcessStep, {
      step: { ...step, detail: result.message, result: result.message },
      conversation: false,
      isLast: true,
      settled: true,
      waitingForInput: false,
      waitingPrompt: ''
    })
  )
  assert.equal(markup.split(result.message).length - 1, 1)
  assert.match(markup, /动作详情/)
  assert.doesNotMatch(markup, /执行结果/)
})

/** 创建后端已签发的权限恢复动作，模拟截图所示工作台恢复卡。 */
function recoveryCandidate(): ExecutionRecoveryCandidate {
  return {
    sourceRunId: 'run',
    threadId: 'thread',
    ownerSessionId: 'session',
    executionKind: 'workbench',
    workflowScope: 'conversation',
    executionStatus: 'failed',
    currentNode: 'authorization_bootstrap',
    availability: 'ready',
    canContinue: true,
    reasonCode: 'RETRY_FAILED_NODE',
    message: result.message,
    updatedAt: '2026-11-02T00:00:00Z',
    recoveryActionPlan: {
      schemaVersion: 'recovery-action-plan.v1',
      incidentId: 'incident',
      sourceRunId: 'run',
      threadId: 'thread',
      executionKind: 'workbench',
      status: 'recoverable',
      reasonCode: 'RETRY_FAILED_NODE',
      message: result.message,
      primaryAction: {
        actionId: 'retry',
        kind: 'retry_business_node',
        targetNode: 'authorization_bootstrap',
        label: '重试',
        description: '重试权限初始化',
        requiresConfirmation: false
      },
      alternateActions: []
    }
  }
}

test('权限恢复卡移入同一 Run 最后一个权限节点，不串会话且保留 DAG/Build 全局故障', () => {
  const current = workflow()
  current.summary = {
    ...current.summary,
    phase: 'authorization_bootstrap',
    status: 'failed',
    message: result.message
  }
  current.events[1] = {
    ...current.events[1],
    data: { detail: { authorizationBootstrap: { ...result, status: 'failed' } } }
  }
  const messages: AgentChatMessage[] = [
    { id: 1, role: 'assistant', content: '', workflow: current }
  ]
  const candidate = recoveryCandidate()
  const owner = authorizationBootstrapRecoveryOwner({ candidate, messages })
  assert.deepEqual(owner, { runId: 'run', threadId: 'thread' })
  assert.equal(
    authorizationBootstrapRecoveryOwner({
      candidate: { ...candidate, threadId: 'other' },
      messages
    }),
    undefined
  )
  assert.equal(
    authorizationBootstrapRecoveryOwner({
      candidate: { ...candidate, currentNode: 'build' },
      messages
    }),
    undefined
  )
  const invalid = workflow()
  invalid.events = [
    {
      ...invalid.events[0],
      data: {
        detail: {
          authorizationBootstrap: { status: 'failed', failureCategory: 'build_plan_invalid' }
        }
      }
    }
  ]
  assert.equal(
    authorizationBootstrapRecoveryOwner({
      candidate,
      messages: [{ ...messages[0], workflow: invalid }]
    }),
    undefined
  )
  const notice = createElement(AuthorizationBootstrapRetryNotice, {
    message: result.message,
    onRetry: () => undefined
  })
  const steps = processStepsForDisplay(undefined, current)!
  const markup = renderToStaticMarkup(
    createElement(
      AuthorizationBootstrapRecoveryContext.Provider,
      { value: { ...owner!, notice } },
      createElement(ProcessSteps, { steps, workflow: current, loading: false })
    )
  )
  const buttons = markup.match(/<button\b[^>]*>[\s\S]*?<\/button>/g) || []
  assert.equal(buttons.filter((button) => button.includes('>重试<')).length, 1)
  assert.ok(markup.indexOf('动作详情') < markup.indexOf('authorization-bootstrap-recovery'))
  assert.doesNotMatch(markup, /任务执行异常|agent-error-card/)
})

test('内联恢复复用原重试回调与确认门，不显示红色异常卡', async () => {
  let retries = 0
  const candidate = recoveryCandidate()
  const element = RecoverySurface({
    inlineAuthorizationBootstrap: true,
    isApplicationPlanningPhase: false,
    activeExecutionRecovery: candidate,
    currentWorkflow: workflow(),
    recoveryRunning: false,
    onExecuteRecoveryAction: () => {
      throw new Error('应优先使用原对账重试入口')
    },
    onRetryCurrentRecovery: () => {
      retries += 1
    }
  })!
  const notice = element.props as { onRetry: () => void }
  notice.onRetry()
  assert.equal(retries, 1)
  const markup = renderToStaticMarkup(element)
  assert.match(markup, /重试/)
  assert.doesNotMatch(markup, /任务执行异常|agent-error-card/)
  const saved = Modal.confirm
  let confirmed: (() => void) | undefined
  Modal.confirm = ((props) => {
    confirmed = props.onOk as () => void
    return { destroy: () => {}, update: () => {} }
  }) as typeof Modal.confirm
  try {
    candidate.recoveryActionPlan.primaryAction!.requiresConfirmation = true
    const gated = RecoverySurface({
      inlineAuthorizationBootstrap: true,
      isApplicationPlanningPhase: false,
      activeExecutionRecovery: candidate,
      recoveryRunning: false,
      onExecuteRecoveryAction: () => {},
      onRetryCurrentRecovery: () => {
        retries += 1
      }
    })!
    gated.props.onRetry()
    assert.equal(retries, 1)
    assert.ok(confirmed)
    confirmed()
    assert.equal(retries, 2)
  } finally {
    Modal.confirm = saved
  }
})

test('Electron 落盘往返保留权限节点身份与完整 DDL，事件恢复也补齐节点身份', () => {
  const current = workflow()
  current.events = [current.events[0]]
  const step = processStepsForDisplay(undefined, current)![0]
  const saved = normalizePersistentSessionMessage({
    id: 1,
    role: 'assistant',
    content: '',
    workflow: current,
    processSteps: [step]
  })
  const restored = saved as unknown as AgentChatMessage
  assert.equal(restored.processSteps![0].nodeName, 'authorization_bootstrap')
  assert.equal(restored.processSteps![0].attempt, 1)
  assert.equal(restored.processSteps![0].authorizationBootstrap?.ddl, ddl)
  const { nodeName: _, attempt: __, authorizationBootstrap: ___, ...persistedShape } = step
  const recovered = processStepsForDisplay([persistedShape], current)!
  assert.equal(recovered.length, 1)
  assert.equal(recovered[0].nodeName, 'authorization_bootstrap')
  assert.equal(recovered[0].authorizationBootstrap?.ddl, ddl)
})

test('真实会话路径：权限已放行，Build 任务停止失败时恢复卡展示 Build 原因而非权限旧文案', () => {
  const notice = '权限数据库初始化脚本执行失败，请检查本地 Bootstrap 日志。'
  const current = workflow()
  current.summary = {
    ...current.summary,
    status: 'failed',
    phase: 'failed',
    message: notice,
    buildSummary: { status: 'failed', total: 8, failed: 2 }
  }
  current.events = [
    current.events[0],
    {
      type: 'workflow.node.completed',
      nodeName: 'build',
      status: 'failed',
      data: {
        detail: {
          buildSummary: { status: 'failed' },
          buildExecutionSlice: {
            tasks: [
              {
                id: 'backend:bootstrap::bootstrap',
                status: 'failed',
                failure_reason: '任务执行器异常退出：当前运行已停止。'
              },
              {
                id: 'page:target',
                status: 'failed',
                failure_reason: '任务执行器异常退出：拒绝继续执行 Agent。'
              }
            ]
          }
        }
      }
    },
    { type: 'workflow.node.completed', nodeName: 'handle_failure', status: 'failed' }
  ] as WorkflowRunPayload['events']
  const candidate = recoveryCandidate()
  candidate.failureDiagnostic = {
    sourceRunId: 'run',
    origin: 'workflow',
    code: 'WORKBENCH_EXECUTION_FAILED',
    message: notice
  }
  candidate.currentNode = 'build'
  candidate.recoveryActionPlan.primaryAction!.targetNode = 'build'
  const messages: AgentChatMessage[] = [
    { id: 1, role: 'assistant', content: '', workflow: current }
  ]
  assert.equal(
    authorizationBootstrapRecoveryOwner({ candidate, workflow: current, messages }),
    undefined
  )
  assert.match(latestConversationFailure(messages)!, /当前运行已停止/)
  assert.doesNotMatch(latestConversationFailure(messages)!, /权限数据库初始化/)
  const markup = renderToStaticMarkup(
    createElement(RecoverySurface, {
      isApplicationPlanningPhase: false,
      activeExecutionRecovery: candidate,
      currentWorkflow: current,
      recoveryRunning: false,
      onExecuteRecoveryAction: () => {},
      onRetryCurrentRecovery: () => {}
    })
  )
  assert.match(markup, /当前运行已停止/)
  assert.doesNotMatch(markup, /权限数据库初始化/)
  assert.match(markup, /重试/)
})

test('节点 completed 仅放行流程，标题与动作详情使用真实权限初始化子状态', () => {
  for (const [status, label] of [
    ['failed', '权限数据库初始化未完成'],
    ['manual_required', '权限数据库待手动初始化'],
    ['executed', '权限数据库初始化成功'],
    ['reused', '权限数据库初始化成功（复用已有结果）']
  ] as const) {
    const current = workflow()
    current.events = [
      {
        ...current.events[0],
        status: 'completed',
        data: { detail: { authorizationBootstrap: { ...result, status } } }
      }
    ]
    const step = processStepsForDisplay(undefined, current)![0]
    assert.equal(step.status, 'completed')
    assert.equal(step.title, label)
    const markup = renderToStaticMarkup(
      createElement(ProcessStep, {
        step,
        conversation: false,
        isLast: true,
        settled: true,
        waitingForInput: false,
        waitingPrompt: ''
      })
    )
    assert.equal(markup.split(label).length - 1, 2)
    assert.doesNotMatch(markup, /已完成\s*权限数据库初始化/)
  }
})
