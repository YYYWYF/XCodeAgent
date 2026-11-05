import { workflowDebugNodesForStage } from '../../../../service/workflowDebugNodes'
import type { PlanningConversationTarget, WorkflowDebugStage } from '../../../../typings'

export const PRODUCT_CONVERSATION_PLACEHOLDER =
  '可以提问或描述修改意见，输入 @ 指定要执行的节点…'

export const PRODUCT_CONVERSATION_RUNNING_HINT =
  '当前设计正在生成，完成后即可发送新的调整。'

export type ProductDesignStageMention = {
  node: PlanningConversationTarget
  label: string
  description: string
}

const DESIGN_STAGE_MENTION_LABELS: Record<string, Omit<ProductDesignStageMention, 'node'>> = {
  requirements: {
    label: '需求设计',
    description: '携带正文返回需求设计并执行，生成后需确认'
  },
  ui_confirmation: {
    label: 'UI设计',
    description: '携带正文返回 UI 设计并执行，生成后需确认'
  },
  technical_planning: {
    label: '技术规划',
    description: '携带正文执行技术规划，生成后需确认'
  }
}

/** 计划对话可指定技术规划或上游设计目标；调试节点白名单仍独立管理。 */
export function workflowStageMentions(stage: WorkflowDebugStage): ProductDesignStageMention[] {
  const options = stage === 'planning'
    ? [{ value: 'technical_planning' }, ...workflowDebugNodesForStage('design')]
    : workflowDebugNodesForStage(stage)
  return options.flatMap((option) => {
    const copy = DESIGN_STAGE_MENTION_LABELS[option.value]
    return copy ? [{ node: option.value as PlanningConversationTarget, ...copy }] : []
  })
}

type StageMentionMatch = { mention: ProductDesignStageMention; start: number; length: number }

/** 收集已完成的目标标签位置，供发送解析与标签替换共用。 */
function stageMentionMatches(text: string, stage: WorkflowDebugStage): StageMentionMatch[] {
  return workflowStageMentions(stage).flatMap((mention) => {
    const escapedLabel = mention.label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
    const expression = new RegExp(`@${escapedLabel}(?=$|[\\s，。,.!！?？:：])`, 'ig')
    const mentionMatches: StageMentionMatch[] = []
    let match = expression.exec(text)
    while (match) {
      mentionMatches.push({ mention, start: match.index, length: match[0].length })
      match = expression.exec(text)
    }
    return mentionMatches
  }).sort((left, right) => left.start - right.start)
}

/** 只移除标签及其一个分隔空格，保留正文中的换行、缩进和标点。 */
function removeStageMentions(text: string, matches: StageMentionMatch[]): string {
  let request = text
  matches.slice().reverse().forEach((match) => {
    let end = match.start + match.length
    if (request[end] === ' ') end += 1
    request = `${request.slice(0, match.start)}${request.slice(end)}`
  })
  return request
}

/** 解析当前阶段的执行目标，并从实际正文中移除标签。 */
export function productDesignStageMentionFromText(
  text: string, stage: WorkflowDebugStage
): (ProductDesignStageMention & { request: string }) | undefined {
  const matches = stageMentionMatches(text, stage)
  const selected = matches[matches.length - 1]
  return selected ? { ...selected.mention, request: removeStageMentions(text, matches).trim() } : undefined
}

export type PlanningConversationInput = {
  request: string
  conversationTarget?: PlanningConversationTarget
}

/** 历史消息展示执行目标，但发送给后端的正文始终不携带路由标签。 */
export function planningConversationText(input: PlanningConversationInput): string {
  return input.conversationTarget
    ? `@${DESIGN_STAGE_MENTION_LABELS[input.conversationTarget].label} ${input.request}`
    : input.request
}

/** 在输入边界分离执行目标与正文；只选标签时保留空正文供发送门禁提示。 */
export function planningConversationInput(text: string, stage: WorkflowDebugStage): PlanningConversationInput {
  const mention = productDesignStageMentionFromText(text, stage)
  return mention
    ? { request: mention.request, conversationTarget: mention.node }
    : { request: text.trim() }
}

/** 选择新目标时移除已选标签，正文和光标两侧内容保持完整。 */
export function replacePlanningConversationMention(
  text: string, start: number, caret: number, mention: ProductDesignStageMention, stage: WorkflowDebugStage
): { value: string; caret: number } {
  const before = text.slice(0, start)
  const after = text.slice(caret)
  const selected = `${removeStageMentions(before, stageMentionMatches(before, stage))}@${mention.label} `
  const suffix = removeStageMentions(after, stageMentionMatches(after, stage))
  return { value: selected + (suffix.startsWith(' ') ? suffix.slice(1) : suffix), caret: selected.length }
}

export type ProductConversationRoute =
  | 'initial_planning'
  | 'historical_product'
  | 'development_conversation'

/** 已完成需求阶段只保留历史路由，后续正式修改由开发阶段提出。 */
export function productConversationRoute(
  productPhaseSelected: boolean,
  lifecycleReadyForWorkbench: boolean
): ProductConversationRoute {
  if (!productPhaseSelected) return 'development_conversation'
  return lifecycleReadyForWorkbench ? 'historical_product' : 'initial_planning'
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
