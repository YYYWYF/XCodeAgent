import type { DagGenerationSnapshot, DagGenerationUnitRecord } from '../../service/agUiAgent'
import { newerDagGenerationSnapshot } from '../../service/agUiAgent'
import { processStepsForDisplay } from '../../service/processStepHistory'
import type {
  ApplicationLifecycle,
  PlanningRefreshState,
  WorkbenchExecution,
  WorkflowBuildTargetReview,
  WorkflowBuildTaskPlan,
  WorkflowBuildTaskPlanConfirmation,
  WorkflowRunPayload
} from '../../typings'
import type { AgentChatMessage } from './types'

export type StageOutputPhase = 'generation' | 'confirmation' | 'other'

/** 读取并校验 lifecycle GET 临时附加的 Planning refresh 投影。 */
export function planningRefreshState(
  lifecycle: ApplicationLifecycle | undefined
): PlanningRefreshState | undefined {
  const value = lifecycle?.extensions?.planningRefresh
  if (
    !value ||
    value.schemaVersion !== 'planning-refresh.v1' ||
    !['pending_plan', 'none'].includes(value.source) ||
    !['awaiting_confirmation', 'idle'].includes(value.status)
  ) {
    return undefined
  }
  return value
}

/** 从服务端草稿身份中读取可比较键，确保同 run/thread 的旧确认卡不会串到新草稿。 */
function dagDraftIdentityKey(value: unknown): string | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const identity = value as Record<string, unknown>
  const planningRunId = String(identity.planningRunId || '').trim()
  const draftDigest = String(identity.draftDigest || '').trim()
  return planningRunId && /^[0-9a-f]{64}$/.test(draftDigest)
    ? `${planningRunId}:${draftDigest}`
    : undefined
}

/** 判断 execution 是否仍承载可提交的 Build DAG 确认。 */
function isAwaitingDagConfirmation(execution: WorkbenchExecution | undefined): boolean {
  return Boolean(
    execution?.status === 'awaiting_user' &&
      !execution.pendingInteraction?.submittedAt &&
      (execution.pendingInteraction?.type === 'task_plan_confirmation' ||
        execution.pendingInteraction?.payload?.mode === 'build_task_plan_confirmation')
  )
}

/**
 * 从 lifecycle 中选择最新的 DAG 确认 execution。
 * lifecycle 可能仍保留更早运行的 awaiting_user 记录；basedOnRevision 才能标识
 * 哪张确认卡由最新 lifecycle 写入，不能依赖对象插入顺序取第一条。
 */
function latestPendingDagExecution(
  lifecycle: ApplicationLifecycle | undefined
): WorkbenchExecution | undefined {
  return Object.values(lifecycle?.activeExecutions || {})
    .filter(isAwaitingDagConfirmation)
    .reduce<WorkbenchExecution | undefined>((latest, execution) => {
      if (!latest) return execution
      const latestRevision = Number(latest.pendingInteraction?.basedOnRevision || 0)
      const executionRevision = Number(execution.pendingInteraction?.basedOnRevision || 0)
      if (executionRevision !== latestRevision) {
        return executionRevision > latestRevision ? execution : latest
      }
      return String(execution.updatedAt || '') > String(latest.updatedAt || '') ? execution : latest
    }, undefined)
}

/** 从持久化生命周期中读取当前唯一的 Build DAG 待确认 execution。
 *  Backend PendingPlan 不持久化 threadId；合成恢复 execution 时只接受 owner session
 * 已有的真实 threadId 作为 renderer 上下文，不根据 workflowRunId 伪造 threadId。
 */
export function pendingDagConfirmationExecution(
  lifecycle: ApplicationLifecycle | undefined,
  ownerSessionThreadId?: string
): WorkbenchExecution | undefined {
  const recovery = planningRefreshState(lifecycle)
  const pending = latestPendingDagExecution(lifecycle)
  if (recovery) {
    if (recovery.source === 'pending_plan' && recovery.status === 'awaiting_confirmation') {
      const recoveryIdentity = dagDraftIdentityKey({
        planningRunId: recovery.planningRunId,
        draftDigest: recovery.draftDigest
      })
      const exact = recovery.workflowRunId
        ? lifecycle?.activeExecutions?.[recovery.workflowRunId]
        : undefined
      const exactIdentity = dagDraftIdentityKey(exact?.pendingInteraction?.payload?.draftIdentity)
      if (
        isAwaitingDagConfirmation(exact) &&
        (!recoveryIdentity || recoveryIdentity === exactIdentity)
      ) {
        return exact
      }
      const identityMatchedPending = recoveryIdentity
        ? Object.values(lifecycle?.activeExecutions || {}).find(
            (execution) =>
              isAwaitingDagConfirmation(execution) &&
              dagDraftIdentityKey(execution.pendingInteraction?.payload?.draftIdentity) ===
                recoveryIdentity
          )
        : pending
      if (identityMatchedPending) return identityMatchedPending
      const threadId = String(ownerSessionThreadId || exact?.threadId || '').trim()
      if (!recovery.workflowRunId || !threadId) return undefined
      const now = lifecycle?.updatedAt || new Date(0).toISOString()
      const scope = recovery.buildExecutionScope || { type: 'application', targetId: 'application' }
      return {
        scope: scope.type,
        targetId: scope.targetId || 'application',
        pageId: scope.type === 'page' ? scope.targetId : undefined,
        threadId,
        runId: recovery.workflowRunId,
        phase: 'prepare_build_tasks',
        status: 'awaiting_user',
        pendingInteraction: {
          id: `planning-refresh:${recovery.draftDigest || recovery.planningRunId || 'pending'}`,
          type: 'task_plan_confirmation',
          basedOnRevision: Math.max(1, lifecycle?.revision || 1),
          payload: recovery.confirmation || { mode: 'build_task_plan_confirmation' },
          artifactRefs: [],
          createdAt: now
        },
        startedAt: now,
        updatedAt: now
      }
    }
    // none/idle 是明确的非 Pending 状态，禁止旧 execution 复活。
    return undefined
  }
  return pending
}

/** 读取当前 PendingPlan 绑定的页面会话；Workflow Run 只用于定位执行，不充当业务 owner。 */
export function pendingDagOwnerSessionId(
  lifecycle: ApplicationLifecycle | undefined
): string | undefined {
  const recovery = planningRefreshState(lifecycle)
  if (recovery?.source === 'pending_plan' && recovery.status === 'awaiting_confirmation') {
    const recoveredOwner = String(recovery.ownerSessionId || '').trim()
    if (recoveredOwner) return recoveredOwner
    const confirmationOwner = draftOwnerSessionId(recovery.confirmation?.draftIdentity)
    if (confirmationOwner) return confirmationOwner
  }
  const execution = pendingDagConfirmationExecution(lifecycle)
  return draftOwnerSessionId(execution?.pendingInteraction?.payload?.draftIdentity)
}

/** 从服务端 DraftIdentity 投影中严格读取页面会话 ID。 */
function draftOwnerSessionId(value: unknown): string | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const ownerSessionId = String((value as Record<string, unknown>).ownerSessionId || '').trim()
  return ownerSessionId || undefined
}

/** 在某个持久化会话中定位与 lifecycle execution 对应的 DAG 确认快照。 */
export function pendingDagConfirmationWorkflow(
  messages: AgentChatMessage[],
  execution: WorkbenchExecution | undefined,
  lifecycle?: ApplicationLifecycle
): WorkflowRunPayload | undefined {
  if (!execution) return undefined
  const recovery = planningRefreshState(lifecycle)
  if (
    recovery?.source === 'pending_plan' &&
    recovery.status === 'awaiting_confirmation' &&
    recovery.confirmation
  ) {
    return {
      runId: recovery.workflowRunId || execution.runId,
      threadId: execution.threadId,
      events: [],
      summary: {
        status: 'requires_user_input',
        phase: 'prepare_build_tasks',
        message: recovery.message,
        clarification: recovery.confirmation,
        buildTaskPlanConfirmation: recovery.confirmation,
        lifecycle
      },
      state: {
        status: 'requires_user_input',
        phase: 'prepare_build_tasks',
        clarification: recovery.confirmation,
        lifecycle
      },
      result: {
        status: 'requires_user_input',
        phase: 'prepare_build_tasks',
        clarification: recovery.confirmation,
        lifecycle
      }
    }
  }
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (
      workflow?.runId === execution.runId &&
      workflow.threadId === execution.threadId &&
      currentDagConfirmationPlan(workflow)
    ) {
      return workflow
    }
  }
  return undefined
}

/** 从当前会话最后一个 PlanningRun 读取 revision 最大的 DAG 生成快照。 */
export function latestDagGenerationSnapshot(
  messages: AgentChatMessage[],
  lifecycle?: ApplicationLifecycle
): DagGenerationSnapshot | undefined {
  const recovery = planningRefreshState(lifecycle)
  if (recovery?.source === 'none' && recovery.status === 'idle') {
    // Regenerate 消费 Pending A 后，生成进度只在中间区域展示；阶段产物没有当前
    // Pending 内容时保持为空，不能把本轮或历史 DAG 快照投影到右侧面板。
    return undefined
  }
  let latest: DagGenerationSnapshot | undefined
  let reachedPreviousRun = false
  for (let messageIndex = messages.length - 1; messageIndex >= 0; messageIndex -= 1) {
    const message = messages[messageIndex]
    const steps = processStepsForDisplay(message.processSteps, message.workflow)
    if (!steps?.length) continue
    for (let stepIndex = steps.length - 1; stepIndex >= 0; stepIndex -= 1) {
      const snapshot = steps[stepIndex].dagGeneration
      if (!snapshot) continue
      if (latest && latest.planningRunId !== snapshot.planningRunId) {
        reachedPreviousRun = true
        break
      }
      latest = newerDagGenerationSnapshot(latest, snapshot)
    }
    if (reachedPreviousRun) break
  }
  if (!recovery) return latest
  // Refresh 已给出权威终态或 Pending 时，不允许历史消息里的旧 DAG 再次占据阶段产物。
  return undefined
}

/** 仅在当前 Workflow 正处于 DAG 确认时读取任务计划，避免历史确认数据污染后续阶段。 */
export function currentDagConfirmationPlan(
  workflow: WorkflowRunPayload | undefined
): WorkflowBuildTaskPlan | undefined {
  const clarification = currentDagConfirmationPayload(workflow)
  if (!clarification) return undefined
  return clarification.taskPlan
}

/** 读取当前 DAG 确认目标，供右侧确认卡展示页面及其实际关联接口。 */
export function currentDagConfirmationTargetReview(
  workflow: WorkflowRunPayload | undefined
): WorkflowBuildTargetReview | undefined {
  return currentDagConfirmationPayload(workflow)?.targetReview
}

/** 读取 Backend 签发的当前 DraftIdentity，供 Abandon 精确绑定 Pending result。 */
export function currentDagConfirmationDraftIdentity(
  workflow: WorkflowRunPayload | undefined
): { planningRunId: string; draftDigest: string } | undefined {
  const value = currentDagConfirmationPayload(workflow)?.draftIdentity
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const planningRunId = String((value as Record<string, unknown>).planningRunId || '').trim()
  const draftDigest = String((value as Record<string, unknown>).draftDigest || '').trim()
  if (!planningRunId || !/^[0-9a-f]{64}$/.test(draftDigest)) return undefined
  return { planningRunId, draftDigest }
}

/** 把 Backend 签发的 DraftIdentity 绑定到 DAG 动作，供 Confirm/Abandon/Regenerate 精确提交。
 *  缺少服务端身份时返回 undefined，调用方必须 fail closed，不得提交无身份请求。 */
export function bindDagConfirmationDraftIdentity(
  workflow: WorkflowRunPayload | undefined,
  action: WorkflowBuildTaskPlanConfirmation
): WorkflowBuildTaskPlanConfirmation | undefined {
  const identity = currentDagConfirmationDraftIdentity(workflow)
  return identity ? { ...action, ...identity } : undefined
}

/** 读取当前 DAG 确认卡的结构化错误，供右侧交互卡复用原始反馈。 */
export function currentDagConfirmationErrors(workflow: WorkflowRunPayload | undefined): string[] {
  const errors = currentDagConfirmationPayload(workflow)?.errors
  return Array.isArray(errors)
    ? errors.flatMap((error) => (typeof error === 'string' && error.trim() ? [error.trim()] : []))
    : []
}

type DagConfirmationPayload = {
  taskPlan?: WorkflowBuildTaskPlan
  targetReview?: WorkflowBuildTargetReview
  draftIdentity?: unknown
  errors?: unknown
}

/** 只接受尚未完成的 DAG 确认，阻止 Confirm 结果中的 clear projection 重新变成操作卡。 */
function isActionableDagConfirmationPayload(value: unknown): value is DagConfirmationPayload {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false
  const payload = value as Record<string, unknown>
  if (String(payload.mode || '') !== 'build_task_plan_confirmation') return false
  const status = String(payload.status || '').trim()
  const confirmationStatus = String(payload.confirmationStatus || '').trim()
  return (
    (!status || status === 'requires_user_input') &&
    (!confirmationStatus || confirmationStatus === 'pending')
  )
}

/** 从 Workflow 当前投影位置解析 DAG 确认载荷，不读取历史阶段快照。 */
function currentDagConfirmationPayload(
  workflow: WorkflowRunPayload | undefined
): DagConfirmationPayload | undefined {
  if (!workflow) return undefined
  const currentClarifications = [
    workflow.summary.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  const currentClarification = currentClarifications.find(
    (value) => value && typeof value === 'object' && !Array.isArray(value)
  )
  if (currentClarification) {
    return isActionableDagConfirmationPayload(currentClarification)
      ? currentClarification
      : undefined
  }
  // 当前 clarification 完全缺失时，保留 PendingPlan recovery 对 buildTaskPlanConfirmation 的兼容回退。
  const historicalProjection = workflow.summary.buildTaskPlanConfirmation
  return isActionableDagConfirmationPayload(historicalProjection) ? historicalProjection : undefined
}

/** 区分 DAG 生成、DAG 确认和其它大阶段，供右侧面板只在大阶段变化时自动跟随。 */
export function stageOutputPhase(
  workflow: WorkflowRunPayload | undefined,
  snapshot: DagGenerationSnapshot | undefined,
  confirmationPlan: WorkflowBuildTaskPlan | undefined
): StageOutputPhase {
  if (confirmationPlan) return 'confirmation'
  const phase = String(
    workflow?.summary.phase || workflow?.result?.phase || workflow?.state?.phase || ''
  )
  if (phase === 'prepare_build_tasks') return 'generation'
  if (snapshot?.status === 'active') return 'generation'
  return 'other'
}

/** 按实际生成状态返回中文文案，不从参与方式反向推断状态。 */
export function dagGenerationUnitStatusLabel(unit: DagGenerationUnitRecord): string {
  return {
    not_required: '无需生成',
    pending: '等待生成',
    generating: '生成中',
    validating: '校验中',
    candidate_ready: '生成完成',
    round_exhausted: '本轮尝试已用尽',
    aborted: '已终止'
  }[unit.status]
}

/** 仅模型生成 Unit 展示 Local attempt，确定性 Unit 不制造 0/0 次数。 */
export function dagGenerationUnitAttemptCopy(unit: DagGenerationUnitRecord): string {
  if (
    unit.generationStrategy !== 'model' ||
    unit.localAttemptLimit <= 0 ||
    unit.attemptInRound <= 0
  ) {
    return ''
  }
  return `第 ${unit.attemptInRound}/${unit.localAttemptLimit} 次尝试`
}

/** 返回复用与候选任务的安全数量，不展示 Candidate Task 身份或正文。 */
export function dagGenerationUnitTaskCopy(unit: DagGenerationUnitRecord): string {
  if (unit.retainedTaskCount === 0 && unit.candidateTaskCount === 0) return ''
  const retained = unit.retainedTaskCount
  const added = unit.candidateTaskCount
  if (retained > 0 && added > 0) return `复用 ${retained} 项 · 新增 ${added} 项`
  if (retained > 0) return `复用 ${retained} 项任务`
  return `新增 ${added} 项任务`
}

/** 返回与生成状态分离的处理策略文案。 */
export function dagGenerationStrategyLabel(unit: DagGenerationUnitRecord): string {
  if (unit.participation === 'reuse_and_generate') return '部分复用 · 部分新增'
  if (unit.generationStrategy === 'model') return 'AI 生成'
  if (unit.generationStrategy === 'deterministic') return '系统生成'
  if (unit.generationStrategy === 'reuse_only') {
    if (unit.retainedTaskCount > 0) return '复用已有任务'
    if (unit.reusableCapabilityCount > 0) return '沿用已有能力'
    return '已有职责已满足'
  }
  if (unit.generationStrategy === 'prerequisite_only') return '前置条件已满足'
  if (unit.generationStrategy === 'structural_only') return '用于组织任务，无需单独开发'
  return '无新增任务需求'
}

/** 返回 Unit 的用户可见名称，动态业务名称优先，历史快照缺名时按类别安全兜底。 */
export function dagGenerationUnitDisplayName(
  unit: DagGenerationUnitRecord,
  index: number,
  units: DagGenerationUnitRecord[]
): string {
  const displayName = unit.displayName?.trim()
  if (displayName && displayName !== unit.id) return displayName

  const knownNames: Record<string, string> = {
    'frontend:api-client': '前端接口调用封装',
    'frontend:data:static': '前端静态数据处理',
    'frontend:auth-guard': '页面访问权限配置',
    'frontend:shell': '前端应用环境',
    'frontend:route-registry': '前端页面路由配置',
    'backend:bootstrap': '后端基础环境配置',
    'application:root': '应用任务结构',
    'app:integration': '应用集成节点'
  }
  const knownName = knownNames[unit.id]
  if (knownName) return knownName

  const pageUnit = unit.kind === 'page' || unit.id.startsWith('page:')
  const endpointUnit = unit.id.startsWith('backend:endpoint:')
  const categoryName = pageUnit
    ? '未命名页面'
    : endpointUnit
      ? '未命名接口'
      : unit.id.startsWith('frontend:data:')
        ? '前端数据处理'
        : unit.kind === 'frontend'
          ? '前端公共能力'
          : unit.kind === 'backend'
            ? '后端公共能力'
            : unit.kind === 'database'
              ? '数据库开发任务'
              : unit.kind === 'authorization'
                ? '权限配置任务'
                : unit.kind === 'app'
                  ? '应用集成任务'
                  : '开发任务'
  const sameCategoryOrdinal = units
    .slice(0, index + 1)
    .filter((candidate) => {
      if (pageUnit) return candidate.kind === 'page' || candidate.id.startsWith('page:')
      if (endpointUnit) return candidate.id.startsWith('backend:endpoint:')
      return candidate.kind === unit.kind && candidate.id !== 'application:root'
    }).length
  const sameCategoryCount = units.filter((candidate) => {
    if (pageUnit) return candidate.kind === 'page' || candidate.id.startsWith('page:')
    if (endpointUnit) return candidate.id.startsWith('backend:endpoint:')
    return candidate.kind === unit.kind && candidate.id !== 'application:root'
  }).length
  return sameCategoryCount > 1 ? `${categoryName}（第 ${sameCategoryOrdinal} 项）` : categoryName
}

/** 在面向普通用户的校验说明中替换当前快照可识别的 Unit ID。 */
export function dagGenerationIssueMessage(
  message: string,
  units: DagGenerationUnitRecord[]
): string {
  return units
    .map((unit, index) => ({
      id: unit.id,
      displayName: dagGenerationUnitDisplayName(unit, index, units)
    }))
    .sort((left, right) => right.id.length - left.id.length)
    .reduce((copy, item) => {
      return copy.split(item.id).join(item.displayName)
    }, message)
}

/** 返回 Run 级摘要；round_exhausted 只描述局部轮次，不提升为 Run 失败。 */
export function dagGenerationSummaryCopy(snapshot: DagGenerationSnapshot): string {
  if (snapshot.status === 'failed') return '执行计划生成失败'
  if (snapshot.status === 'cancelled') return '执行计划生成已取消'
  // 当前阶段优先于累计修复轮次，后续汇总、校验和保存不能继续标成修复中。
  const phaseMessage = {
    preparing: '正在准备开发任务',
    global_check: '正在进行全局校验',
    assembling: '正在汇总开发任务',
    validating: '正在校验执行计划',
    persisting_pending: '正在保存待确认计划'
  }
  if (snapshot.phase !== 'generating_units') return phaseMessage[snapshot.phase]
  if (snapshot.summary.roundExhaustedUnitCount > 0) return '部分开发任务本轮尝试已用尽'
  if (snapshot.globalRepairRound > 0) {
    return `正在进行全局修复，第 ${snapshot.globalRepairRound}/${snapshot.globalRepairLimit} 轮`
  }
  if (snapshot.summary.activeUnitCount > 0) return '正在处理开发任务'
  if (snapshot.summary.pendingUnitCount > 0) return '开发任务等待安排'
  return '开发任务已生成，等待全局校验'
}
