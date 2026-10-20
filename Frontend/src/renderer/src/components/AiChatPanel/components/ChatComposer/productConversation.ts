import { workflowDebugNodesForStage } from '../../../../service/workflowDebugNodes'
import type { WorkflowDebugStage } from '../../../../typings'

export const PRODUCT_CONVERSATION_PLACEHOLDER =
  '告诉产品 Agent 你想调整的需求、页面或 UI，也可以直接提问…'

export const PRODUCT_CONVERSATION_RUNNING_HINT =
  '当前设计正在生成，完成后即可发送新的调整。'

export type ProductDesignStageMention = {
  node: string
  label: string
  description: string
}

const DESIGN_STAGE_MENTION_LABELS: Record<string, Omit<ProductDesignStageMention, 'node'>> = {
  requirements: {
    label: '需求设计',
    description: '返回需求与产品规划节点'
  },
  ui_confirmation: {
    label: 'UI设计',
    description: '返回 UI 设计稿节点'
  },
  technical_planning: {
    label: '技术规划',
    description: '返回技术规划节点'
  }
}

/** 计划对话可指定技术规划或上游设计目标；调试节点白名单仍独立管理。 */
export function workflowStageMentions(stage: WorkflowDebugStage): ProductDesignStageMention[] {
  const options = stage === 'planning'
    ? [{ value: 'technical_planning' }, ...workflowDebugNodesForStage('design')]
    : workflowDebugNodesForStage(stage)
  return options.flatMap((option) => {
    const copy = DESIGN_STAGE_MENTION_LABELS[option.value]
    return copy ? [{ node: option.value, ...copy }] : []
  })
}

/** 解析当前阶段的节点提及，并从实际指令里移除路由标记。 */
export function productDesignStageMentionFromText(
  text: string,
  stage: WorkflowDebugStage
): (ProductDesignStageMention & { request: string }) | undefined {
  const matches = workflowStageMentions(stage).flatMap((mention) => {
    const escapedLabel = mention.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const expression = new RegExp(`@${escapedLabel}(?=$|[\\s，。,.!！?？:：])`, 'ig')
    const mentionMatches: Array<{ mention: ProductDesignStageMention; start: number; length: number }> = []
    let match = expression.exec(text)
    while (match) {
      mentionMatches.push({ mention, start: match.index, length: match[0].length })
      match = expression.exec(text)
    }
    return mentionMatches
  }).sort((left, right) => left.start - right.start)
  const selected = matches[matches.length - 1]
  if (!selected) return undefined

  let request = text
  matches.reverse().forEach((match) => {
    let end = match.start + match.length
    if (request[end] === ' ') end += 1
    request = `${request.slice(0, match.start)}${request.slice(end)}`
  })
  return { ...selected.mention, request: request.trim() }
}

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
