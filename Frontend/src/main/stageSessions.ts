export type EditorMode = 'frontend' | 'backend'

export type WorkbenchPhase =
  | 'product'
  | 'planning'
  | 'development'
  | 'test'
  | 'review'
  | 'acceptance'

export type AgentStage = 'DESIGN' | 'PLAN' | 'DEVELOPMENT'

const stageSessionCreationLocks = new Map<string, Promise<void>>()

/** 将前三个工作台阶段映射为会话业务阶段，后三阶段沿用现有执行会话。 */
export function stageForWorkbenchPhase(value: WorkbenchPhase): AgentStage | undefined {
  if (value === 'product') return 'DESIGN'
  if (value === 'planning') return 'PLAN'
  if (value === 'development') return 'DEVELOPMENT'
  return undefined
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
