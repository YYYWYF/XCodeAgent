import assert from 'node:assert/strict'
import type { ApplicationLifecycle, WorkflowRunPayload } from '../src/renderer/src/typings'
import type { AgentChatMessage } from '../src/renderer/src/components/AiChatPanel/types'
import { createSessionIdentity } from '../src/renderer/src/components/AiChatPanel/hooks/sessionRuntime'
import { restoreFormalRevisionHandoff } from '../src/renderer/src/components/AiChatPanel/hooks/useFormalRevisionHandoffRecovery'
import { conversationRevisionHandoffCommitted } from '../src/renderer/src/components/AiChatPanel/conversationMode'
import { workflowInteractionAvailability, workflowMessageInteractionAvailability } from '../src/renderer/src/components/AiChatPanel/planExecutionMode'

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
console.log('formal revision handoff recovery tests passed')
