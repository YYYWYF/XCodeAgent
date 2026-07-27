export const MESSAGE_LIST_BOTTOM_THRESHOLD = 24

export type MessageListScrollMetrics = {
  clientHeight: number
  scrollHeight: number
  scrollTop: number
}

/** 判断消息列表当前位置是否处于底部容差内，供自动跟随与按钮状态复用。 */
export function isMessageListNearBottom(
  metrics: MessageListScrollMetrics,
  threshold = MESSAGE_LIST_BOTTOM_THRESHOLD
): boolean {
  const maximumScrollTop = Math.max(metrics.scrollHeight - metrics.clientHeight, 0)
  const distanceFromBottom = maximumScrollTop - Math.max(metrics.scrollTop, 0)
  return distanceFromBottom <= threshold
}

/** 判断消息列表是否溢出且已离开底部，供悬浮跳转按钮控制可见性。 */
export function shouldShowScrollToBottom(
  metrics: MessageListScrollMetrics,
  threshold = MESSAGE_LIST_BOTTOM_THRESHOLD
): boolean {
  const maximumScrollTop = Math.max(metrics.scrollHeight - metrics.clientHeight, 0)
  return maximumScrollTop > 0 && !isMessageListNearBottom(metrics, threshold)
}
