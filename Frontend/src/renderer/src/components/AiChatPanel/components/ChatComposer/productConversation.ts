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

export {
  buildProductConversationInteraction,
  productConversationSubmissionError
} from '../../../../service/applicationPlanningProductConversation'
