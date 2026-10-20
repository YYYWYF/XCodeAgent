import type { WorkflowRunPayload } from '../../typings'

export type PlanningStreamChunk = { content?: string; workflow?: WorkflowRunPayload }

type Options = {
  sourceSessionKey: string
  prepareDesignSession: (workflow: WorkflowRunPayload) => Promise<string>
  publish: (sessionKey: string, chunk: PlanningStreamChunk) => void
}

/** 按本次真实执行的首节点交接展示会话，准备完成前缓存流帧，避免设计输出落入计划历史。 */
export function createPlanningStageReentry(options: Options): {
  publish: (chunk: PlanningStreamChunk) => void
  finish: () => Promise<void>
} {
  let sessionKey = options.sourceSessionKey
  let decided = false
  let preparing: Promise<void> | undefined
  let failure: unknown
  let pending: PlanningStreamChunk[] = []

  /** 按原始顺序把缓存帧交给已准备好的阶段会话。 */
  const flush = (): void => {
    const chunks = pending
    pending = []
    chunks.forEach((chunk) => options.publish(sessionKey, chunk))
  }

  /** 仅对当前请求的新工作流做交接；问答和技术生成继续留在来源计划会话。 */
  const publish = (chunk: PlanningStreamChunk): void => {
    pending.push(chunk)
    const workflow = chunk.workflow
    const phase = workflow?.summary.phase || workflow?.state?.phase || workflow?.result?.phase
    if (!decided && workflow && phase) {
      decided = true
      if (['design_intent_analysis', 'requirements', 'product_planning', 'ui_confirmation'].includes(String(phase))) {
        preparing = options.prepareDesignSession(workflow).then((key) => {
          sessionKey = key
        }).catch((reason) => {
          failure = reason
        }).finally(() => {
          preparing = undefined
          flush()
        })
      }
    }
    if (decided && !preparing) flush()
  }

  /** 等待阶段会话交接完成；无工作流的只读回复仍投递到原会话。 */
  const finish = async (): Promise<void> => {
    await preparing
    flush()
    if (failure) throw failure
  }

  return { publish, finish }
}
