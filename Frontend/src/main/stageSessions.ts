export type EditorMode = 'frontend' | 'backend'

export type WorkbenchPhase =
  | 'product'
  | 'planning'
  | 'development'
  | 'test'
  | 'review'
  | 'acceptance'

export type AgentStage = 'DESIGN' | 'PLAN' | 'DEVELOPMENT'

export type ChatSessionTargetType = 'workflow' | 'page' | 'api' | 'entity'

const stageSessionCreationLocks = new Map<string, Promise<void>>()

/** 将前三个工作台阶段映射为会话业务阶段，后三阶段沿用现有执行会话。 */
export function stageForWorkbenchPhase(value: WorkbenchPhase): AgentStage | undefined {
  if (value === 'product') return 'DESIGN'
  if (value === 'planning') return 'PLAN'
  if (value === 'development') return 'DEVELOPMENT'
  return undefined
}

/** 校验并返回会话的明确目标类型。 */
export function assertChatSessionTargetType(value: unknown): ChatSessionTargetType {
  if (value !== 'workflow' && value !== 'page' && value !== 'api' && value !== 'entity') {
    throw new Error('targetType must be workflow, page, api, or entity')
  }
  return value
}

/** 校验会话目标类型与页面、API、实体标识互斥且完整。 */
export function assertChatSessionTargetBinding(
  targetType: ChatSessionTargetType,
  binding: {
    pageId?: string
    apiContractId?: string
    endpointId?: string
    entityId?: string
  }
): void {
  const { pageId, apiContractId, endpointId, entityId } = binding
  const valid =
    (targetType === 'workflow' && !pageId && !apiContractId && !endpointId && !entityId) ||
    (targetType === 'page' && Boolean(pageId) && !apiContractId && !endpointId && !entityId) ||
    (targetType === 'api' &&
      Boolean(apiContractId) &&
      Boolean(endpointId) &&
      !pageId &&
      !entityId) ||
    (targetType === 'entity' && Boolean(entityId) && !pageId && !apiContractId && !endpointId)
  if (!valid) throw new Error('session target binding does not match targetType')
}

/** 计算同一 Workflow/Stage 下一个会话序号。 */
export function nextStageSessionSequence(
  sessions: Array<{ workflowId: string; stage?: AgentStage; sequence?: number }>,
  workflowId: string,
  stage: AgentStage
): number {
  return (
    Math.max(
      0,
      ...sessions
        .filter((session) => session.workflowId === workflowId && session.stage === stage)
        .map((session) => Number(session.sequence || 0))
    ) + 1
  )
}

/** 串行执行同一 Workflow/Stage 的会话创建，防止并发分配重复 sequence。 */
export async function withStageSessionCreationLock<T>(
  key: string,
  action: () => Promise<T>
): Promise<T> {
  const previous = stageSessionCreationLocks.get(key) || Promise.resolve()
  let release: () => void = () => undefined
  const current = new Promise<void>((resolve) => {
    release = resolve
  })
  const tail = previous.then(() => current)
  stageSessionCreationLocks.set(key, tail)
  await previous
  try {
    return await action()
  } finally {
    release()
    if (stageSessionCreationLocks.get(key) === tail) stageSessionCreationLocks.delete(key)
  }
}
