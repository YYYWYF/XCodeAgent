import type { ApplicationPlanningInteraction } from '../../../../typings'

export const PRODUCT_CONVERSATION_PLACEHOLDER =
  '告诉产品 Agent 你想调整的需求、页面或 UI，也可以直接提问…'

export const PRODUCT_CONVERSATION_RUNNING_HINT =
  '当前设计正在生成，完成后即可发送新的调整。'

export type ProductConversationRoute =
  | 'initial_planning'
  | 'completed_product'
  | 'development_conversation'

/** 阶段决定 Agent 能力边界，应用完成态只决定产品修改的执行入口。 */
export function productConversationRoute(
  productPhaseSelected: boolean,
  lifecycleReadyForWorkbench: boolean
): ProductConversationRoute {
  if (!productPhaseSelected) return 'development_conversation'
  return lifecycleReadyForWorkbench ? 'completed_product' : 'initial_planning'
}

/** 只在产品对话绑定的 planning run 正在执行时阻止发送，输入内容仍可继续编辑。 */
export function productConversationSendBlocked(
  productConversationAvailable: boolean,
  planningRunActive: boolean
): boolean {
  return productConversationAvailable && planningRunActive
}

type ProductConversationGate = Pick<
  ApplicationPlanningInteraction,
  'gateId' | 'artifact' | 'artifactRevision'
>

/** 用服务端最新审阅门身份构造自由输入，禁止把自然语言伪装成当前卡片回答。 */
export function buildProductConversationInteraction(
  gate: ProductConversationGate,
  request: string
): ApplicationPlanningInteraction {
  const trimmed = request.trim()
  if (!gate.gateId || !gate.artifactRevision) {
    throw new Error('当前规划确认卡版本信息不完整，请刷新后重试。')
  }
  if (!trimmed) {
    throw new Error('产品阶段自由输入不能为空。')
  }
  return {
    ...gate,
    action: 'design_change',
    request: trimmed
  }
}

/** 把过期审阅门错误转换为明确的刷新提示，其他错误保留原始原因。 */
export function productConversationSubmissionError(reason: unknown): string {
  const detail = reason instanceof Error ? reason.message : String(reason || '未知错误')
  if (detail.includes('过期') || detail.includes('已经更新')) {
    return `${detail} 请刷新到最新产品设计状态后重试。`
  }
  return `设计变更提交失败：${detail}`
}
