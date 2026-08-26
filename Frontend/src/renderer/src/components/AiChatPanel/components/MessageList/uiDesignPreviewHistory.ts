import type { WorkflowClarification, WorkflowRunPayload } from '../../../../typings'
import type { AgentChatMessage } from '../../types'
import { planningWorkflowPhase } from '../../../Welcome/planningWorkflowState'

/** 从公开 Workflow 快照读取当前确认模式，兼容流式投影的三个权威位置。 */
function clarificationMode(workflow: WorkflowRunPayload): string {
  const candidates = [
    workflow.summary?.clarification,
    workflow.state?.clarification,
    workflow.result?.clarification
  ]
  const clarification = candidates.find((value): value is WorkflowClarification =>
    Boolean(value && typeof value === 'object')
  )
  return String(clarification?.mode || '')
}

/** 判断消息是否为 UI 设计预览，供渲染与持久化压缩共用同一规则。 */
function isUiDesignPreviewMessage(message: AgentChatMessage): boolean {
  return Boolean(
    message.workflow &&
      (message.workflow.summary?.phase === 'ui_confirmation' ||
        clarificationMode(message.workflow) === 'ui_design_confirmation')
  )
}

/** 判断消息是否为当前规划阶段入口卡。 */
function isPlanningStageEntryMessage(message: AgentChatMessage): boolean {
  return Boolean(
    message.workflow &&
      (message.workflow.summary?.phase === 'planning_stage_entry' ||
        clarificationMode(message.workflow) === 'planning_stage_entry_confirmation')
  )
}

/** 读取规划消息的最新节点，兼容 summary 尚未追上 node.started 事件的流式帧。 */
function planningMessagePhase(message: AgentChatMessage): string {
  return planningWorkflowPhase(message.workflow)
}

/** 判断与当前权威规划阶段冲突的入口或 TechnicalPlan 卡片；UI 设计稿始终保留为历史。 */
export function isSupersededPlanningPhaseMessage(
  message: AgentChatMessage,
  currentPhase: string
): boolean {
  if (!message.workflow || !currentPhase) return false
  const messagePhase = planningMessagePhase(message)
  if (currentPhase === 'planning_stage_entry') {
    return messagePhase === 'technical_planning'
  }
  if (currentPhase === 'technical_planning') {
    return messagePhase === 'planning_stage_entry'
  }
  return currentPhase === 'ui_confirmation' && messagePhase === 'technical_planning'
}

/** TechnicalPlan 已开始后，设计窗口遗留的“进入规划阶段”卡不再属于当前消息流。 */
export function isSupersededPlanningStageEntryMessage(
  messages: AgentChatMessage[],
  messageIndex: number
): boolean {
  if (!isPlanningStageEntryMessage(messages[messageIndex])) return false
  return messages
    .slice(messageIndex + 1)
    .some((message) => planningMessagePhase(message) === 'technical_planning')
}

/** 判断入口点击后的 assistant 消息是否只是失败恢复残留。 */
function isFailedPlanningEntryAttempt(message: AgentChatMessage): boolean {
  if (message.role !== 'assistant') return false
  const status = String(message.workflow?.summary?.status || '')
  const phase = String(message.workflow?.summary?.phase || '')
  return (
    (status === 'failed' && phase === 'failed') ||
    (Boolean(message.planningLoading) && !message.content.trim() && !message.workflow)
  )
}

/** 定位消息流中最后一张 UI 设计预览卡，旧版本不再重复渲染预览。 */
export function latestUiDesignPreviewMessageIndex(messages: AgentChatMessage[]): number {
  return messages.reduce((latestIndex, message, index) => {
    return isUiDesignPreviewMessage(message) ? index : latestIndex
  }, -1)
}

/** 仅在最新状态仍为空时追加规划加载占位，避免覆盖刚回放的待确认问题卡。 */
export function appendPlanningLoadingPlaceholder(
  messages: AgentChatMessage[],
  placeholder: AgentChatMessage
): AgentChatMessage[] {
  return messages.length > 0 ? messages : [placeholder]
}

/** 判断空的规划进度消息是否已被后续 assistant 结果取代，避免确认卡出现后仍残留 loading。 */
export function isSupersededPlanningProgressMessage(
  message: AgentChatMessage,
  latestAssistantMessageId: number | undefined
): boolean {
  if (
    message.role !== 'assistant' ||
    message.id === latestAssistantMessageId ||
    message.content.trim() ||
    message.error
  ) {
    return false
  }
  if (message.planningLoading && !message.workflow) return true
  return message.workflow?.summary?.status === 'running'
}

/** 模板准备终态覆盖空的规划进度，避免模板已就绪后仍显示“正在恢复规划阶段”。 */
export function isTemplateSupersededPlanningProgressMessage(
  message: AgentChatMessage,
  templatePreparationVisible: boolean
): boolean {
  if (
    !templatePreparationVisible ||
    message.role !== 'assistant' ||
    message.content.trim() ||
    message.error
  ) {
    return false
  }
  return Boolean(message.planningLoading) || message.workflow?.summary?.status === 'running'
}

/** 压缩创建规划会话：保留最新 UI 预览，删除重复预览和入口失败轮次。 */
export function compactPlanningMessageHistory(
  messages: AgentChatMessage[],
  authoritativeWorkflow?: WorkflowRunPayload
): AgentChatMessage[] {
  const latestUiPreviewIndex = latestUiDesignPreviewMessageIndex(messages)
  const latestAssistantMessageId = [...messages]
    .reverse()
    .find((message) => message.role === 'assistant')?.id
  const latestPlanningEntryIndex = messages.reduce(
    (latestIndex, message, index) => (isPlanningStageEntryMessage(message) ? index : latestIndex),
    -1
  )
  const removedIndexes = new Set<number>()
  const authoritativePhase = planningWorkflowPhase(authoritativeWorkflow)

  messages.forEach((message, index) => {
    if (isSupersededPlanningPhaseMessage(message, authoritativePhase)) {
      removedIndexes.add(index)
    }
    if (isSupersededPlanningProgressMessage(message, latestAssistantMessageId)) {
      removedIndexes.add(index)
    }
    if (isUiDesignPreviewMessage(message) && index !== latestUiPreviewIndex) {
      removedIndexes.add(index)
    }
    if (isPlanningStageEntryMessage(message) && index !== latestPlanningEntryIndex) {
      removedIndexes.add(index)
    }
    if (isSupersededPlanningStageEntryMessage(messages, index)) {
      removedIndexes.add(index)
    }
  })

  // 入口卡片自身已经完整表达用户动作，因此不再保存重复的“进入规划阶段”文本；
  // 若下一条只是这次恢复产生的失败快照或占位，也一起删除，保留入口卡供重试。
  if (latestPlanningEntryIndex >= 0) {
    for (let index = latestPlanningEntryIndex + 1; index < messages.length; index += 1) {
      const message = messages[index]
      if (message.role !== 'user' || message.content.trim() !== '进入规划阶段') continue
      removedIndexes.add(index)
      const nextMessage = messages[index + 1]
      if (nextMessage && isFailedPlanningEntryAttempt(nextMessage)) {
        removedIndexes.add(index + 1)
        index += 1
      }
    }
  }

  if (removedIndexes.size === 0) return messages
  return messages.filter((_, index) => !removedIndexes.has(index))
}
