import assert from 'node:assert/strict'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import { createSessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import { restoreFormalRevisionHandoff } from '../src/renderer/src/components/AiChatPanel/hooks/useFormalRevisionHandoffRecovery'
import { conversationRevisionHandoffCommitted } from '../src/renderer/src/components/AiChatPanel/conversationMode'
import { workflowInteractionAvailability, workflowMessageInteractionAvailability } from '../src/renderer/src/components/AiChatPanel/planExecutionMode'
import { formalRevisionHandoffRestorable, retainFormalRevisionSessionAfterStart } from '../src/renderer/src/components/AiChatPanel/hooks/revisionSession'
import type { WorkflowDesignStageRevisionStart } from '../src/renderer/src/typings'

const activeRevisionLifecycle = {
  activeFormalRevision: {
    changeId: 'change-1', formalBranch: 'design_stage_revision', impactInteractionId: 'impact-1',
    sourceThreadId: 'source-thread', sourceRunId: 'source-run', planningThreadId: 'planning-graph-thread',
    status: 'design_planning', currentArtifact: 'requirement-spec'
  }
} as ApplicationLifecycle
const revisionSessionBase = {
  title: '正式修订', editorMode: 'frontend' as const, workbenchPhase: 'product' as const,
  workflowId: 'workflow-1', stage: 'DESIGN' as const, sequence: 2,
  entryKey: 'revision:design_stage_revision:impact-1', createdAt: 1, updatedAt: 1, messageCount: 1
}
const sourceDevelopmentSession = {
  ...revisionSessionBase, id: 'source-session', threadId: 'source-thread',
  workbenchPhase: 'development' as const, stage: 'DEVELOPMENT' as const,
  entryKey: 'development-entry:source'
}
const oldDevelopmentSession = { ...sourceDevelopmentSession, id: 'old-session', threadId: 'old-thread' }

// 当前修订已交接时，旧 Conversation 确认不能因仍 requires_user_input 而重新激活。
const handedOffConversation = {
  runId: 'source-run', threadId: 'source-thread', events: [],
  summary: { status: 'requires_user_input', intent: 'formal_revision' },
  state: { workflow_scope: 'conversation' }
} as WorkflowRunPayload
assert.equal(conversationRevisionHandoffCommitted(handedOffConversation, activeRevisionLifecycle), true)
assert.equal(conversationRevisionHandoffCommitted({ ...handedOffConversation, runId: 'other-run' }, activeRevisionLifecycle), false)

// 重连只补展示归属；原开发会话缺失时失败关闭，已有目标复用且交接回执不重复。
{
  const lifecycle = {
    ...activeRevisionLifecycle,
    initialization: { threadId: 'planning-graph-thread', stage: 'analyzing_requirement', status: 'running' }
  } as ApplicationLifecycle
  const source = createSessionIdentity({
    workspaceRoot: '/workspace', editorMode: 'frontend', sessionId: sourceDevelopmentSession.id,
    threadId: sourceDevelopmentSession.threadId, workflowId: 'workflow-1', workbenchPhase: 'development',
    stage: 'DEVELOPMENT', sequence: 1, entryKey: sourceDevelopmentSession.entryKey
  })
  const target = createSessionIdentity({
    workspaceRoot: '/workspace', editorMode: 'frontend', sessionId: 'restored-design',
    threadId: 'restored-design-thread', workflowId: 'workflow-1', workbenchPhase: 'product',
    stage: 'DESIGN', sequence: 2, entryKey: 'revision:design_stage_revision:impact-1'
  })
  const store = new Map<string, AgentChatMessage[]>([[source.key, []], [target.key, []]])
  const lookups: string[] = []
  let navigationCount = 0
  const input = {
    applicationId: 'workflow-1', lifecycle, sessions: [sourceDevelopmentSession],
    phase: 'product' as const, loading: false,
    /** 只加载精确来源身份，不创建开发会话。 */
    loadSessionIdentity: async (id: string) => { assert.equal(id, source.sessionId); return source },
    /** 记录定位入口；测试阶段会话创建或复用同一展示身份。 */
    ensurePlanningSession: async (key: string) => { lookups.push(key); return target },
    /** 读取测试历史。 */
    getSessionMessages: (key: string) => store.get(key) || [],
    /** 写入测试历史。 */
    setSessionMessages: (key: string, messages: AgentChatMessage[]) => { store.set(key, messages) },
    /** 用真实消息类型检查持久化输入，但不产生外部写请求。 */
    persistSession: async () => {},
    /** 确认导航到设计阶段，Graph 仍使用原 planning thread。 */
    onRestored: (identity: typeof target, phase: 'product' | 'planning') => {
      navigationCount += 1
      assert.equal(phase, 'product')
      assert.equal(identity.revisionContext?.planningThreadId, 'planning-graph-thread')
    },
    /** 测试函数直接传播错误。 */
    onError: () => {}
  }
  const restored = await restoreFormalRevisionHandoff(input)
  assert.equal(lookups[0], 'revision:design_stage_revision:impact-1')
  assert.equal(store.get(source.key)?.length, 1)
  await restoreFormalRevisionHandoff({ ...input, sessions: [sourceDevelopmentSession, {
    ...revisionSessionBase, id: target.sessionId, threadId: target.threadId,
    revisionContext: restored.revisionContext
  }] })
  assert.equal(lookups[1], target.threadId)
  assert.equal(store.get(source.key)?.length, 1)
  assert.equal(navigationCount, 2)
  await assert.rejects(restoreFormalRevisionHandoff({ ...input, sessions: [oldDevelopmentSession] }), /原开发会话/)
  assert.equal(lookups.length, 2)
}

assert.equal(workflowInteractionAvailability(handedOffConversation, activeRevisionLifecycle), 'stale')
assert.equal(workflowMessageInteractionAvailability(handedOffConversation, activeRevisionLifecycle, false, true), 'stale')

// TechnicalPlan-only 已接纳的断线启动必须保留展示身份，并能从精确来源重建 PLAN 会话。
{
  const lifecycle = {
    ...activeRevisionLifecycle,
    initialization: { threadId: 'planning-graph-thread', stage: 'awaiting_technical_plan_confirmation', status: 'awaiting_user' },
    activeFormalRevision: { ...activeRevisionLifecycle.activeFormalRevision!, formalBranch: 'workbench_plan_revision', status: 'drafting' }
  } as ApplicationLifecycle
  const input = {
    impact: { formalBranch: 'workbench_plan_revision', interactionId: 'impact-1' },
    sourceConversationThreadId: 'source-thread', sourceRunId: 'source-run'
  } as WorkflowDesignStageRevisionStart
  assert.equal(retainFormalRevisionSessionAfterStart(input, undefined), true)
  assert.equal(retainFormalRevisionSessionAfterStart(input, lifecycle), true)
  assert.equal(retainFormalRevisionSessionAfterStart(input, { ...lifecycle, activeFormalRevision: undefined }), false)
  assert.equal(retainFormalRevisionSessionAfterStart({ ...input, sourceRunId: 'other-run' }, lifecycle), false)
  assert.equal(formalRevisionHandoffRestorable(lifecycle, 'planning'), true)
  assert.equal(formalRevisionHandoffRestorable(lifecycle, 'product'), false)
  assert.equal(formalRevisionHandoffRestorable(lifecycle, 'development'), false)
  const source = createSessionIdentity({
    workspaceRoot: '/workspace', editorMode: 'frontend', sessionId: sourceDevelopmentSession.id,
    threadId: sourceDevelopmentSession.threadId, workflowId: 'workflow-1', workbenchPhase: 'development',
    stage: 'DEVELOPMENT', sequence: 1, entryKey: sourceDevelopmentSession.entryKey
  })
  const target = createSessionIdentity({
    workspaceRoot: '/workspace', editorMode: 'frontend', sessionId: 'restored-plan',
    threadId: 'restored-plan-thread', workflowId: 'workflow-1', workbenchPhase: 'planning',
    stage: 'PLAN', sequence: 2, entryKey: 'revision-plan:change-1:recovery'
  })
  const restored = await restoreFormalRevisionHandoff({
    applicationId: 'workflow-1', lifecycle, sessions: [sourceDevelopmentSession], phase: 'planning', loading: false,
    /** 只加载原始开发来源。 */
    loadSessionIdentity: async () => source,
    /** 恢复展示会话，绝不触发模型或 Graph。 */
    ensurePlanningSession: async (_key, phase, context) => {
      assert.equal(phase, 'planning')
      assert.equal(context?.formalBranch, 'workbench_plan_revision')
      return target
    },
    /** 空历史用于覆盖断线后展示身份缺失。 */
    getSessionMessages: () => [],
    /** 本测试不写外部会话存储。 */
    setSessionMessages: () => {},
    /** 本测试不写外部会话存储。 */
    persistSession: async () => {},
    /** 确认恢复后仍使用原 planning Graph。 */
    onRestored: identity => { assert.equal(identity.revisionContext?.planningThreadId, 'planning-graph-thread') },
    /** 测试直接传播异常。 */
    onError: () => {}
  })
  assert.equal(restored.sessionId, 'restored-plan')
}
console.log('formal revision handoff recovery tests passed')
