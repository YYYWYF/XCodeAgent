import {
  CheckCircleOutlined,
  DownOutlined,
  LoadingOutlined,
  ToolOutlined,
  UpOutlined
} from '@ant-design/icons'
import { Typography } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { ToolCallRecord } from '../../../../service/agUiAgent'
import { cx } from '../../../../utils'
import './ToolCallCard.less'

const { Text } = Typography

type Props = {
  toolCall: ToolCallRecord
}

/** 渲染单条标准 AG-UI 工具调用，并保留入参和结果详情。 */
export default function ToolCallCard({ toolCall }: Props): ReactElement {
  const completed = toolCall.status === 'completed'
  const title = toolCallTitle(toolCall)

  return (
    <details className={cx('tool-call-card', completed && 'completed')}>
      <summary className={cx('tool-call-summary')}>
        <span className={cx('tool-call-icon')}>
          {completed ? <CheckCircleOutlined /> : <LoadingOutlined />}
        </span>
        <Text strong>{title}</Text>
      </summary>
      <div className={cx('tool-call-body')}>
        <ToolCallSection title="入参" value={toolCall.args} empty="暂无入参" />
        {completed && (
          <ToolCallSection title="结果" value={toolCall.result} empty="工具未返回结果内容" />
        )}
      </div>
    </details>
  )
}

/** 将标准 AG-UI 工具事件收拢为最新一条摘要，并支持展开完整调用链。 */
export function ToolCallChain({ toolCalls }: { toolCalls: ToolCallRecord[] }): ReactElement {
  const [expanded, setExpanded] = useState(false)
  const latestToolCall = toolCalls[toolCalls.length - 1]
  if (!latestToolCall) return <></>

  return (
    <section aria-label="工具调用链" className={cx('tool-call-chain')}>
      <div className={cx('tool-call-chain-summary')}>
        <span className={cx('tool-call-chain-icon')}>
          {latestToolCall.status === 'completed' ? <CheckCircleOutlined /> : <LoadingOutlined />}
        </span>
        <span className={cx('tool-call-chain-copy')}>
          <Text strong>{toolCallTitle(latestToolCall)}</Text>
          <Text type="secondary">已记录 {toolCalls.length} 次工具调用</Text>
        </span>
        <button
          aria-expanded={expanded}
          className={cx('tool-call-chain-toggle')}
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          {expanded ? <UpOutlined /> : <DownOutlined />}
          <span>{expanded ? '收起调用链' : '展开调用链'}</span>
        </button>
      </div>
      {expanded && (
        <div className={cx('tool-call-chain-body')}>
          {toolCalls.map((toolCall) => (
            <ToolCallCard key={toolCall.id} toolCall={toolCall} />
          ))}
        </div>
      )}
    </section>
  )
}

/** 渲染工具调用的入参或结果区块。 */
function ToolCallSection({
  title,
  value,
  empty
}: {
  title: string
  value?: string
  empty: string
}): ReactElement {
  return (
    <section className={cx('tool-call-section')}>
      <Text className={cx('tool-call-section-title')}>
        <ToolOutlined /> {title}
      </Text>
      <pre className={cx('tool-call-content')}>{formatToolCallValue(value) || empty}</pre>
    </section>
  )
}

/** 返回标准工具事件对应的可读标题。 */
function toolCallTitle(toolCall: ToolCallRecord): string {
  return toolCall.status === 'completed'
    ? `已调用 ${toolCall.name} 工具`
    : `调用 ${toolCall.name} 工具中`
}

/** 尝试把 JSON 工具内容格式化为易读文本，普通字符串保持原样。 */
function formatToolCallValue(value?: string): string {
  const trimmedValue = value?.trim()
  if (!trimmedValue) return ''

  try {
    return JSON.stringify(JSON.parse(trimmedValue), null, 2)
  } catch {
    return trimmedValue
  }
}
