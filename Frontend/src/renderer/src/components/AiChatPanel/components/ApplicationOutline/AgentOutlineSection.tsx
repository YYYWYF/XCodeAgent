import { CaretDownOutlined, RobotOutlined } from '@ant-design/icons'
import { Alert, Button } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import { cx } from '../../../../utils'

type Props = {
  agents: BusinessAgentOption[]
  query: string
  selectedAgentId?: string
  error?: string
  onSelect?: (agent: BusinessAgentOption) => void
  onRefresh?: () => void
}

/** 在目录底部以统一分组和行样式展示智能体，保留搜索、刷新和折叠入口。 */
export default function AgentOutlineSection({
  agents,
  query,
  selectedAgentId,
  error,
  onSelect,
  onRefresh
}: Props): ReactElement | null {
  const [expanded, setExpanded] = useState(true)
  const visibleAgents = agents.filter((agent) =>
    `${agent.label} ${agent.purpose}`.toLocaleLowerCase().includes(query)
  )
  if (!agents.length && !onRefresh && !error) return null

  return (
    <section aria-label="智能体分组" className={cx('outline-section')}>
      <div className={cx('agent-outline-heading')}>
        <button
          aria-expanded={expanded}
          className={cx('outline-section-heading')}
          onClick={() => setExpanded((current) => !current)}
          type="button"
        >
          <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
          <span>智能体</span>
          <span className={cx('development-count')}>{agents.length}</span>
        </button>
        {onRefresh ? (
          <Button aria-label="刷新智能体" size="small" onClick={onRefresh}>
            刷新
          </Button>
        ) : null}
      </div>
      {expanded ? (
        <div className={cx('api-list')}>
          {error ? <Alert type="error" message={error} showIcon /> : null}
          {visibleAgents.map((agent) => (
            <button
              aria-pressed={selectedAgentId === agent.agentId}
              className={cx(
                'api-row',
                'agent-outline-row',
                selectedAgentId === agent.agentId && 'selected'
              )}
              key={agent.agentId}
              onClick={() => onSelect?.(agent)}
              title={agent.purpose}
              type="button"
            >
              <RobotOutlined className={cx('api-endpoint-icon')} />
              <span className={cx('api-copy')}>
                <strong>{agent.label}</strong>
                <small>{agent.purpose}</small>
              </span>
            </button>
          ))}
          {!visibleAgents.length && !error ? (
            <div className={cx('outline-empty')}>
              {agents.length ? '没有匹配的智能体' : '当前计划中暂无智能体'}
            </div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
