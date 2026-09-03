import type {
  ChatSessionDevelopmentContinuation,
  ChatSessionDevelopmentTarget
} from '../../service/chatSessions'
import type {
  DevelopmentPlanningApiContract,
  DevelopmentPlanningPageOption,
  WorkflowDevelopmentContinuation,
  WorkflowRunPayload
} from '../../typings'

/** 从 Workflow 公开快照读取后端签发的实体前置续接合同。 */
export function workflowDevelopmentContinuation(
  workflow: WorkflowRunPayload | undefined,
  pages: DevelopmentPlanningPageOption[] = [],
  apiContracts: DevelopmentPlanningApiContract[] = []
): WorkflowDevelopmentContinuation | undefined {
  if (!workflow) return undefined
  return normalizeWorkflowContinuation(
    workflow.summary.developmentContinuation,
    pages,
    apiContracts
  )
}

/** 把 ready 的服务端合同转换为可持久化的一次性续接卡，不从历史消息推断目标。 */
export function developmentContinuationFromWorkflow(
  workflow: WorkflowRunPayload | undefined,
  pages: DevelopmentPlanningPageOption[] = [],
  apiContracts: DevelopmentPlanningApiContract[] = []
): ChatSessionDevelopmentContinuation | undefined {
  const continuation = workflowDevelopmentContinuation(workflow, pages, apiContracts)
  if (
    !continuation ||
    continuation.status !== 'ready' ||
    continuation.action !== 'continue_after_entity_binding' ||
    !continuation.token ||
    !continuation.technicalPlanSha256
  ) {
    return undefined
  }
  return {
    id: continuation.id,
    status: 'ready',
    sourceThreadId: continuation.sourceThreadId,
    sourceRunId: continuation.sourceRunId,
    token: continuation.token,
    technicalPlanSha256: continuation.technicalPlanSha256,
    target: continuation.target
  }
}

/** 校验公开 continuation 的身份、状态、目标与一次性 token 形态。 */
function normalizeWorkflowContinuation(
  value: unknown,
  pages: DevelopmentPlanningPageOption[],
  apiContracts: DevelopmentPlanningApiContract[]
): WorkflowDevelopmentContinuation | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const candidate = value as Record<string, unknown>
  const id = text(candidate.id)
  const status = text(candidate.status)
  const action = text(candidate.action)
  const sourceThreadId = text(candidate.sourceThreadId)
  const sourceRunId = text(candidate.sourceRunId)
  const target = developmentTarget(candidate.target, pages, apiContracts)
  if (
    !id ||
    !sourceThreadId ||
    !sourceRunId ||
    !target ||
    !['awaiting_entity_binding', 'ready', 'consumed'].includes(status) ||
    !['start_entity_binding', 'continue_after_entity_binding'].includes(action)
  ) {
    return undefined
  }
  const token = text(candidate.token)
  const technicalPlanSha256 = text(candidate.technicalPlanSha256)
  if (
    status === 'ready' &&
    (action !== 'continue_after_entity_binding' ||
      token.length < 32 ||
      !/^[0-9a-f]{64}$/.test(technicalPlanSha256))
  ) {
    return undefined
  }
  return {
    id,
    status: status as WorkflowDevelopmentContinuation['status'],
    action: action as WorkflowDevelopmentContinuation['action'],
    sourceThreadId,
    sourceRunId,
    target,
    requiredEntityIds: textList(candidate.requiredEntityIds),
    remainingEntityIds: textList(candidate.remainingEntityIds),
    ...(token ? { token } : {}),
    ...(technicalPlanSha256 ? { technicalPlanSha256 } : {})
  }
}

/** 把服务端目标映射到当前页面/API显示信息，目标 ID 仍以服务端为权威。 */
function developmentTarget(
  value: unknown,
  pages: DevelopmentPlanningPageOption[],
  apiContracts: DevelopmentPlanningApiContract[]
): ChatSessionDevelopmentTarget | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined
  const candidate = value as Record<string, unknown>
  const type = text(candidate.type)
  if (type === 'page') {
    const pageId = text(candidate.pageId)
    if (!pageId) return undefined
    const page = pages.find((item) => item.pageId === pageId || item.key === pageId)
    return { type, pageId, label: text(page?.label) || text(candidate.label) || pageId }
  }
  if (type === 'endpoint') {
    const apiContractId = text(candidate.apiContractId)
    const endpointId = text(candidate.endpointId)
    if (!apiContractId || !endpointId) return undefined
    const endpoint = apiContracts
      .find((contract) => contract.id === apiContractId)
      ?.endpoints.find((item) => item.id === endpointId)
    return {
      type,
      apiContractId,
      endpointId,
      label: endpoint
        ? `${String(endpoint.method || 'API').toUpperCase()} ${endpoint.path}`
        : text(candidate.label) || endpointId
    }
  }
  return undefined
}

/** 将不可信公开字段收敛为去空格字符串。 */
function text(value: unknown): string {
  return typeof value === 'string' ? value.trim() : ''
}

/** 将不可信数组收敛为去重后的非空字符串列表。 */
function textList(value: unknown): string[] {
  return Array.isArray(value) ? Array.from(new Set(value.map(text).filter(Boolean))) : []
}
