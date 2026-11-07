import type { ApplicationLifecycle, WorkflowRunPayload } from '../../typings'
import { deriveWorkbenchPhase, type WorkbenchPhase } from '../../workbenchPhase'

export type PlanningStageAccess = {
  stageActive: boolean
  historyOnly: boolean
  message: string
  destination?: WorkbenchPhase
  transitioning?: boolean
}

/** 阶段线程已交接但入口确认尚未提交时，原确认门仍归当前 PLAN 会话处理。 */
export function planningStageEntryTransferred(
  workflow: WorkflowRunPayload | undefined,
  lifecycle: ApplicationLifecycle | undefined
): boolean {
  return lifecycle?.initialization.stage === 'awaiting_planning_stage_entry' &&
    workflow?.threadId === lifecycle.initialization.threadId &&
    (workflow?.state?.planning_stage_handoff as { targetPhase?: string } | undefined)?.targetPhase === 'planning'
}

/** 初始会话通过明确 entryKey 绑定后台规划，交接后的会话则直接使用其 Graph thread。 */
export function planningSessionOwnsThread(
  session: { threadId?: string; entryKey?: string } | undefined,
  threadId: string | undefined
): boolean {
  return Boolean(threadId && session && (session.threadId === threadId || session.entryKey === threadId))
}

/** 结束规划只在原阶段和原会话展示继续入口，历史输入区仍保持只读。 */
export function stoppedPlanningForSession(input: {
  phase: WorkbenchPhase
  lifecycle?: ApplicationLifecycle
  session?: { threadId?: string; entryKey?: string }
}): ApplicationLifecycle['endedPlanningExecution'] | undefined {
  const { lifecycle, phase, session } = input
  const archived = lifecycle?.endedPlanningExecution
  if (!lifecycle || !archived || lifecycle.initialization.threadId !== archived.threadId ||
      lifecycle.activeRunId || lifecycle.activeFormalRevision || lifecycle.pendingRevisionImpact ||
      Object.keys(lifecycle.activeExecutions || {}).length || !planningSessionOwnsThread(session, archived.threadId)) return undefined
  return deriveWorkbenchPhase({ ...lifecycle, initialization: { ...lifecycle.initialization, stage: archived.stage } }) === phase
    ? archived : undefined
}

/** 只以当前业务阶段和权威线程开放规划操作，浏览记录不能重新激活历史阶段。 */
export function planningStageAccess(input: {
  phase: WorkbenchPhase
  lifecycle?: ApplicationLifecycle
  enteredDevelopment: boolean
  sessionThreadId?: string
  sessionEntryKey?: string
  enteringStage?: boolean
  pendingStageEntryTransferred?: boolean
}): PlanningStageAccess {
  const { phase, lifecycle, enteredDevelopment, sessionThreadId } = input
  if (!['product', 'planning'].includes(phase)) {
    return { stageActive: false, historyOnly: false, message: '' }
  }
  if (!lifecycle) {
    if (phase === 'planning' && input.enteringStage) return planningStageTransitionAccess()
    return { stageActive: false, historyOnly: true, message: '正在同步当前流程，历史会话暂时仅供查看。' }
  }
  const current = input.pendingStageEntryTransferred ? 'planning' : deriveWorkbenchPhase(lifecycle)
  const developmentArtifacts = lifecycle.developmentArtifacts
  const developmentStarted = [
    ...Object.values(developmentArtifacts?.pages || {}),
    ...Object.values(developmentArtifacts?.endpoints || {}).flatMap((group) => Object.values(group))
  ].some((artifact) => artifact.initialDevelopmentStatus !== 'pending')
  // 首次模板就绪后保留计划卡的“进入开发”操作；进入开发后不再开放该历史阶段。
  const initialPlanningReady = current === 'development' && phase === 'planning' && !enteredDevelopment && !developmentStarted &&
    lifecycle.initialization.stage === 'ready_for_workbench' && Boolean(lifecycle.initialization.threadId) &&
    !lifecycle.activeFormalRevision && !lifecycle.endedPlanningExecution && Object.keys(lifecycle.activeExecutions || {}).length === 0
  const formalPlanning = lifecycle.activeFormalRevision &&
    ['design_planning', 'drafting', 'awaiting_user'].includes(lifecycle.activeFormalRevision.status)
  const stageActive = initialPlanningReady || (current === phase &&
    (lifecycle.initialization.stage !== 'ready_for_workbench' || Boolean(formalPlanning)))
  const historicalSession = Boolean(sessionThreadId && !planningSessionOwnsThread({
    threadId: sessionThreadId, entryKey: input.sessionEntryKey
  }, lifecycle.initialization.threadId))
  // 仅真实交接事务在途时展示同步态，不把尚未换绑的 Lifecycle 当作历史回退。
  if (phase === 'planning' && input.enteringStage && (!stageActive || historicalSession)) {
    return planningStageTransitionAccess()
  }
  if (stageActive && !historicalSession) {
    return { stageActive, historyOnly: false, message: '' }
  }
  if (stageActive) {
    return { stageActive, historyOnly: true,
      message: '此会话仅供查看，请返回当前流程的会话继续对话和确认。', destination: phase }
  }
  if (current === 'product') {
    return { stageActive, historyOnly: true,
      message: '当前流程已返回需求阶段，请完成需求和 UI 确认后再进入计划阶段。', destination: 'product' }
  }
  if (current === 'planning') {
    return { stageActive, historyOnly: true,
      message: '需求阶段已完成。如需调整需求，请在当前计划阶段提出。', destination: 'planning' }
  }
  return { stageActive, historyOnly: true,
    message: '此阶段仅供查看历史会话。如需新增或调整需求，请在开发阶段提出。', destination: 'development' }
}

/** 交接期间保持只读门禁，但不展示错误阶段判断或跳转到旧会话的按钮。 */
function planningStageTransitionAccess(): PlanningStageAccess {
  return { stageActive: false, historyOnly: true, transitioning: true,
    message: '正在同步已确认的设计与当前计划会话，请稍候。' }
}
