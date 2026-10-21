import { useEffect, useRef } from 'react'
import type { ApplicationLifecycle } from '../../../typings'
import type { ChatSessionRevisionContext, ChatSessionSummary } from '../../../service/chatSessions'
import type { WorkbenchPhase } from '../../../workbenchPhase'
import type { AgentChatMessage } from '../types'
import type { SessionIdentity } from './sessionRuntime'
import type { PersistSessionInput } from './useChatSessions'
import { activeFormalRevisionStageSession } from './revisionSession'

type HandoffRecoveryInput = {
  applicationId: string
  lifecycle?: ApplicationLifecycle
  sessions: ChatSessionSummary[]
  phase: WorkbenchPhase
  loading: boolean
  loadSessionIdentity: (id: string) => Promise<SessionIdentity>
  ensurePlanningSession: (
    key: string, phase?: WorkbenchPhase, context?: ChatSessionRevisionContext,
    source?: SessionIdentity
  ) => Promise<SessionIdentity>
  getSessionMessages: (key: string) => AgentChatMessage[]
  setSessionMessages: (key: string, messages: AgentChatMessage[]) => void
  persistSession: (input: PersistSessionInput) => Promise<void>
  onRestored: (identity: SessionIdentity, phase: 'product' | 'planning') => void
  onError: (error: unknown) => void
}

/** 只用当前批准修订和精确来源会话恢复展示归属，不发送请求或启动新的 Graph。 */
export async function restoreFormalRevisionHandoff(input: HandoffRecoveryInput): Promise<SessionIdentity> {
  const active = input.lifecycle?.activeFormalRevision
  if (!active || active.formalBranch !== 'design_stage_revision' || active.status !== 'design_planning' ||
    !['product', 'planning'].includes(input.phase) ||
    active.planningThreadId !== input.lifecycle?.initialization.threadId) {
    throw new Error('当前生命周期没有可恢复的设计阶段交接。')
  }
  const phase = input.phase as 'product' | 'planning'
  const source = input.sessions.find((session) => session.workflowId === input.applicationId &&
    session.stage === 'DEVELOPMENT' && session.threadId === active.sourceThreadId)
  if (!source) throw new Error('找不到发起本次正式修订的原开发会话。')
  const sourceIdentity = await input.loadSessionIdentity(source.id)
  const existing = activeFormalRevisionStageSession(input.sessions, input.lifecycle, input.applicationId, phase)
  const context: ChatSessionRevisionContext = {
    kind: 'formal_revision', sessionRole: 'design', formalBranch: active.formalBranch,
    impactInteractionId: active.impactInteractionId, changeId: active.changeId,
    sourceSessionId: source.id, sourceConversationThreadId: active.sourceThreadId,
    sourceRunId: active.sourceRunId, planningThreadId: active.planningThreadId
  }
  // 后端已证明批准和交接完成；展示会话缺失时只补当前修订的 StageSession。
  // 原 planning checkpoint 和执行身份继续保持权威，绝不重做 impact 或意图识别。
  const identity = await input.ensurePlanningSession(
    existing?.threadId || (phase === 'product'
      ? `revision:${active.formalBranch}:${active.impactInteractionId}`
      : `revision-plan:${active.changeId}:recovery`),
    phase, context, sourceIdentity
  )
  await input.persistSession({
    editorMode: identity.editorMode, sessionId: identity.sessionId, threadId: identity.threadId,
    revisionContext: context, messages: input.getSessionMessages(identity.key)
  })
  const sourceMessages = input.getSessionMessages(sourceIdentity.key)
  if (!sourceMessages.some((item) => item.revisionHandoff?.impactInteractionId === active.impactInteractionId &&
    item.revisionHandoff.targetSessionId === identity.sessionId)) {
    const timestamp = Date.now() * 1000
    const messages: AgentChatMessage[] = [...sourceMessages, {
      id: timestamp, role: 'assistant', content: '', createdAt: timestamp,
      revisionHandoff: {
        kind: 'formal_revision', formalBranch: active.formalBranch,
        impactInteractionId: active.impactInteractionId, changeId: active.changeId,
        targetSessionId: identity.sessionId, targetConversationThreadId: identity.threadId,
        request: String(active.request || '')
      }
    }]
    await input.persistSession({
      editorMode: sourceIdentity.editorMode, sessionId: sourceIdentity.sessionId,
      threadId: sourceIdentity.threadId, messages
    })
    input.setSessionMessages(sourceIdentity.key, messages)
  }
  const restored = { ...identity, revisionContext: context }
  input.onRestored(restored, phase)
  return restored
}

/** 首次校准到当前设计修订时定位一次，之后保留用户主动浏览其他阶段的选择。 */
export function useFormalRevisionHandoffRecovery(input: HandoffRecoveryInput): void {
  const attempted = useRef(new Set<string>())
  const latest = useRef(input)
  latest.current = input
  useEffect(() => {
    const active = input.lifecycle?.activeFormalRevision
    if (input.loading || !active || active.formalBranch !== 'design_stage_revision' ||
      active.status !== 'design_planning' || !['product', 'planning'].includes(input.phase)) return
    const key = `${input.applicationId}:${active.changeId}:${input.phase}`
    if (attempted.current.has(key)) return
    attempted.current.add(key)
    /** 工作区或当前修订切换后，旧异步恢复不能改写新界面的阶段选择。 */
    const isCurrent = (): boolean => latest.current.applicationId === input.applicationId &&
      latest.current.lifecycle?.activeFormalRevision?.changeId === active.changeId &&
      latest.current.phase === input.phase
    void restoreFormalRevisionHandoff({
      ...input,
      onRestored: (identity, phase) => {
        if (isCurrent()) latest.current.onRestored(identity, phase)
      }
    }).catch((error) => { if (isCurrent()) latest.current.onError(error) })
  }, [input.applicationId, input.lifecycle, input.sessions, input.phase, input.loading])
}
