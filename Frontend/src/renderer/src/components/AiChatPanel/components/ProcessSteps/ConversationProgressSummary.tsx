import { useEffect, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import { Typography } from 'antd'
import type { WorkflowEvent, WorkflowRunPayload } from '../../../../typings/workflow'

const { Text } = Typography

type Props = {
  loading: boolean
  waitingForInput: boolean
  workflow?: WorkflowRunPayload
  runFinishedAt?: string
  currentStageLabel: string
}

/** 展示二次修改的当前状态与真实耗时，工具调用留在下方详情。 */
export default function ConversationProgressSummary({
  loading,
  waitingForInput,
  workflow,
  runFinishedAt,
  currentStageLabel
}: Props): ReactElement {
  const [mountedAt] = useState(() => Date.now())
  const [now, setNow] = useState(mountedAt)
  const [settledAt, setSettledAt] = useState<number>()
  const wasLoadingRef = useRef(loading)

  useEffect(() => {
    if (loading) {
      wasLoadingRef.current = true
      setSettledAt(undefined)
    } else {
      setNow(Date.now())
      // 记录组件实际观察到的结束时刻，避免最后一个业务节点完成后还在等待时长被截短。
      if (wasLoadingRef.current) setSettledAt(Date.now())
      wasLoadingRef.current = false
    }
    if (!loading) return
    // 运行期间每秒刷新耗时，完成后保留最终时长。
    const timer = window.setInterval(() => setNow(Date.now()), 1_000)
    return () => window.clearInterval(timer)
  }, [loading])

  const events = currentConversationEvents(workflow)
  const startedAt = earliestConversationStart(events) ?? (loading ? mountedAt : undefined)
  const latestCompletedEvent = [...events]
    .reverse()
    .find((event) => event.type === 'conversation.node.completed')
  const elapsedEnd = loading
    ? now
    : settledAt ??
      timestampValue(runFinishedAt) ??
      eventTimestamp(latestCompletedEvent) ??
      (startedAt === undefined ? undefined : now)
  const elapsed =
    startedAt !== undefined && elapsedEnd !== undefined
      ? formatElapsed(Math.max(0, elapsedEnd - startedAt))
      : ''
  const primaryLabel = loading
    ? `${currentStageLabel || '正在处理当前阶段'}${elapsed ? ` · 已耗时 ${elapsed}` : ''}`
    : waitingForInput
      ? `等待补充信息${elapsed ? ` · 已耗时 ${elapsed}` : ''}`
      : `处理完成${elapsed ? ` · 总耗时 ${elapsed}` : ''}`

  return (
    <>
      <Text type="secondary">{primaryLabel}</Text>
    </>
  )
}

/** 仅读取当前 Run 的二次修改业务节点事件，避免沿用同 Thread 的历史耗时。 */
function currentConversationEvents(workflow?: WorkflowRunPayload): WorkflowEvent[] {
  if (!workflow) return []
  return workflow.events.filter(
    (event) =>
      event.type.startsWith('conversation.node.') &&
      (!event.runId || event.runId === workflow.runId)
  )
}

/** 返回当前 Run 最早业务节点开始时间，供总耗时计算使用。 */
function earliestConversationStart(events: WorkflowEvent[]): number | undefined {
  const timestamps = events
    .filter((event) => event.type === 'conversation.node.started')
    .map(eventTimestamp)
    .filter((value): value is number => value !== undefined)
  return timestamps.length > 0 ? Math.min(...timestamps) : undefined
}

/** 解析 AG-UI 节点事件的 ISO 时间戳。 */
function eventTimestamp(event?: WorkflowEvent): number | undefined {
  return timestampValue(event?.timestamp)
}

/** 解析事件或生命周期投影中的 ISO 时间戳。 */
function timestampValue(value?: string): number | undefined {
  if (!value) return undefined
  const timestamp = Date.parse(value)
  return Number.isFinite(timestamp) ? timestamp : undefined
}

/** 用易读的分钟和秒格式展示经过时间。 */
function formatElapsed(milliseconds: number): string {
  const seconds = Math.floor(milliseconds / 1_000)
  if (seconds < 60) return `${seconds} 秒`
  return `${Math.floor(seconds / 60)} 分 ${String(seconds % 60).padStart(2, '0')} 秒`
}

