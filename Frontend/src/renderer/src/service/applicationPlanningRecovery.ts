import type { ApplicationConfig, ApplicationLifecycle, WorkflowRunPayload } from '../typings'
import { readWorkflowPayload } from './agUiAgent'
import { getApplicationLifecycle } from './applicationLifecycle'
import { recoveryStore, type RecoveryOutput } from './recoveryStore'
import {
  parseRecoveryActionPlan as parseSharedRecoveryActionPlan,
  type RecoveryAction,
  type RecoveryActionPlan,
  type RecoveryFailureDiagnostic
} from './recoveryActionPlan'

export type ApplicationPlanningAuthoritativeSnapshot = {
  workflow: WorkflowRunPayload
  lifecycle: ApplicationLifecycle
  recovery: ApplicationPlanningRecoveryProjection
  output?: RecoveryOutput | null
}

/** 后端已响应但恢复投影不可用；保留读取错误，不将项目状态问题标记为断连。 */
export class ApplicationPlanningRecoveryReadError extends Error {
  /** 为恢复结果的身份、Workflow 与解释校验失败提供明确的通信分类。 */
  constructor(message: string) {
    super(message)
    this.name = 'ApplicationPlanningRecoveryReadError'
  }
}

export type {
  RecoveryActionKind,
  RecoveryFailureDiagnostic,
  RecoveryIncidentStatus
} from './recoveryActionPlan'
export type ApplicationPlanningFailureDiagnostic = RecoveryFailureDiagnostic
export type ApplicationPlanningRecoveryAction = RecoveryAction
export type ApplicationPlanningRecoveryActionPlan = RecoveryActionPlan<'application_planning'>
export { parseRecoveryAction, parseRecoveryActionPlan } from './recoveryActionPlan'

export type ApplicationPlanningRecoveryProjection = {
  schemaVersion: 'application-planning-recovery.v1'
  classification:
    | 'awaiting_user'
    | 'running'
    | 'ready_to_continue'
    | 'completed'
    | 'cancelled'
    | 'stopped'
    | 'failed'
    | 'blocked'
    | 'conflict'
    | 'legacy_unverified'
  sourceRunId?: string
  threadId: string
  canContinue: boolean
  userActionRequired: boolean
  inputCommitted: boolean
  reasonCode: string
  message: string
  failureDiagnostic?: ApplicationPlanningFailureDiagnostic | null
  recoveryActionPlan?: ApplicationPlanningRecoveryActionPlan | null
  uiGenerationRecovery?: { pageIds: string[] } | null
}

/** 只在后端确认的当前 UI 中断任务存在时显示统一重试，不用本地生成中状态猜测。 */
export function uiDesignRecoveryError(recovery?: ApplicationPlanningRecoveryProjection): string | undefined {
  return recovery?.classification === 'awaiting_user' && recovery.uiGenerationRecovery?.pageIds.length
    ? 'UI 设计生成已中断，请在对应页面重新生成，或停止该页任务；刷新只同步状态。'
    : undefined
}

const APPLICATION_PLANNING_RECOVERY_CLASSIFICATIONS = new Set([
  'awaiting_user',
  'running',
  'ready_to_continue',
  'completed',
  'cancelled',
  'stopped',
  'failed',
  'blocked',
  'conflict',
  'legacy_unverified'
])

/** 从公开投影中读取可展示的可选文本，拒绝对象、数组和未定义字段。 */
function optionalDiagnosticText(value: unknown): string | null | undefined {
  if (value === null) return null
  if (typeof value !== 'string') return undefined
  const normalized = value.trim()
  return normalized || null
}

/** 从公开投影中读取合法 HTTP 状态，避免 UI 信任任意数字。 */
function optionalDiagnosticHttpStatus(value: unknown): number | null | undefined {
  if (value === null) return null
  return typeof value === 'number' && Number.isInteger(value) && value >= 100 && value <= 599
    ? value
    : undefined
}

/** 严格解析 Backend 提供的安全失败诊断，历史缺失字段保持兼容。 */
function parseFailureDiagnostic(
  value: unknown
): ApplicationPlanningFailureDiagnostic | null | undefined {
  if (value === null) return null
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const candidate = value as Record<string, unknown>
  const sourceRunId = String(candidate.sourceRunId || '').trim()
  const origin = String(candidate.origin || '').trim()
  const code = String(candidate.code || '').trim()
  if (!sourceRunId || !origin || !code) return undefined
  const httpStatus = optionalDiagnosticHttpStatus(candidate.httpStatus)
  return {
    sourceRunId,
    origin,
    code,
    operation: optionalDiagnosticText(candidate.operation),
    dependency: optionalDiagnosticText(candidate.dependency),
    provider: optionalDiagnosticText(candidate.provider),
    model: optionalDiagnosticText(candidate.model),
    ...(httpStatus !== undefined ? { httpStatus } : {}),
    message: optionalDiagnosticText(candidate.message)
  }
}

/** 严格解析当前 RecoveryActionPlan，并校验它与外层 Planning thread/source 身份一致。 */
function parsePlanningRecoveryActionPlan(
  value: unknown,
  outer?: { threadId: string; sourceRunId?: string }
): ApplicationPlanningRecoveryActionPlan | null | undefined {
  return parseSharedRecoveryActionPlan<'application_planning'>(
    value,
    outer
      ? {
          ...outer,
          executionKind: 'application_planning'
        }
      : undefined
  )
}

/** 从 Workflow result/state 严格读取 Backend 给出的 Planning 恢复分类。 */
export function applicationPlanningRecoveryProjection(
  workflow?: WorkflowRunPayload
): ApplicationPlanningRecoveryProjection | undefined {
  for (const source of [workflow?.state]) {
    const raw = source?.applicationPlanningRecovery
    if (!raw || typeof raw !== 'object' || Array.isArray(raw)) continue
    const value = raw as Record<string, unknown>
    const classification = String(value.classification || '')
    const threadId = String(value.threadId || '').trim()
    const reasonCode = String(value.reasonCode || '').trim()
    const message = String(value.message || '').trim()
    if (
      value.schemaVersion !== 'application-planning-recovery.v1' ||
      !APPLICATION_PLANNING_RECOVERY_CLASSIFICATIONS.has(classification) ||
      !threadId ||
      !reasonCode ||
      !message ||
      typeof value.canContinue !== 'boolean' ||
      typeof value.userActionRequired !== 'boolean' ||
      typeof value.inputCommitted !== 'boolean'
    ) {
      continue
    }
    const sourceRunId = String(value.sourceRunId || '').trim()
    const failureDiagnostic = parseFailureDiagnostic(value.failureDiagnostic)
    const hasRecoveryActionPlan = Object.prototype.hasOwnProperty.call(
      value,
      'recoveryActionPlan'
    )
    const recoveryActionPlan = parsePlanningRecoveryActionPlan(value.recoveryActionPlan, {
      threadId,
      ...(sourceRunId ? { sourceRunId } : {})
    })
    // ActionPlan 一旦出现但无法严格解析，整帧拒绝，避免前端根据旧 canContinue 猜动作。
    if (hasRecoveryActionPlan && recoveryActionPlan === undefined) continue
    const uiRecovery = value.uiGenerationRecovery
    let uiGenerationRecovery: { pageIds: string[] } | null | undefined
    if (uiRecovery === null) uiGenerationRecovery = null
    else if (uiRecovery !== undefined) {
      if (!uiRecovery || typeof uiRecovery !== 'object' || Array.isArray(uiRecovery)) continue
      const pageIds = (uiRecovery as Record<string, unknown>).pageIds
      if (!Array.isArray(pageIds) || !pageIds.length || !pageIds.every(id => typeof id === 'string' && id.trim() === id && id.length > 0)) continue
      uiGenerationRecovery = { pageIds: [...new Set(pageIds)] }
    }
    return {
      schemaVersion: 'application-planning-recovery.v1',
      classification: classification as ApplicationPlanningRecoveryProjection['classification'],
      ...(sourceRunId ? { sourceRunId } : {}),
      threadId,
      canContinue: value.canContinue,
      userActionRequired: value.userActionRequired,
      inputCommitted: value.inputCommitted,
      reasonCode,
      message,
      ...(uiGenerationRecovery !== undefined ? { uiGenerationRecovery } : {}),
      ...(failureDiagnostic !== undefined ? { failureDiagnostic } : {}),
      ...(recoveryActionPlan !== undefined ? { recoveryActionPlan } : {})
    }
  }
  return undefined
}

/** 规划与工作台共享恢复 store 和 Lifecycle AG-UI 读取，不再维护第二套查询超时与重试。 */
export async function readApplicationPlanningAuthoritativeSnapshot(
  application: ApplicationConfig, threadId: string
): Promise<ApplicationPlanningAuthoritativeSnapshot | undefined> {
  const key = recoveryStore.key(application.workspaceRoot || '', application.id, threadId)
  const lifecycle = await recoveryStore.read(key,
    () => getApplicationLifecycle(application, threadId, { planningThreadId: threadId }))
  const snapshot = lifecycle.extensions.recoverySnapshot
  if (!snapshot || snapshot.kind !== 'planning' || snapshot.applicationId !== application.id || snapshot.threadId !== threadId)
    throw new ApplicationPlanningRecoveryReadError('规划恢复快照与当前应用或线程不匹配。')
  if (!snapshot.checkpointAvailable) return undefined
  const workflow = readWorkflowPayload(snapshot.workflow)
  if (!workflow || workflow.threadId !== threadId) throw new ApplicationPlanningRecoveryReadError('当前规划没有有效的 Workflow 快照。')
  const recovery = applicationPlanningRecoveryProjection(workflow)
  if (!recovery || recovery.threadId !== threadId) throw new ApplicationPlanningRecoveryReadError('当前规划没有有效的恢复解释。')
  return { workflow, lifecycle, recovery, output: snapshot.output }
}
