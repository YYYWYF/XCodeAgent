import { CaretDownOutlined, RobotOutlined } from '@ant-design/icons'
import { Alert } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import { cx } from '../../../../utils'
import type { DevelopmentArtifacts } from '../../../../typings'
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import DevelopmentStatusDot from './DevelopmentStatusDot'
import IterationBadge from '../../../../components/IterationBadge'

type Props = {
  agents: BusinessAgentOption[]
  query: string
  selectedAgentId?: string
  error?: string
  onSelect?: (agent: BusinessAgentOption) => void
  developmentArtifacts?: DevelopmentArtifacts
  currentBranch?: string
}

/** 智能体沿用页面和接口的完成圆点、分组计数与迭代标记，目录由现有状态事件刷新。 */
export default function AgentOutlineSection({
  agents,
  query,
  selectedAgentId,
  error,
  onSelect,
  developmentArtifacts,
  currentBranch
}: Props): ReactElement | null {
  const [expanded, setExpanded] = useState(true)
  const visibleAgents = agents.filter((agent) =>
    `${agent.label} ${agent.purpose}`.toLocaleLowerCase().includes(query)
  )
  const completed = developmentCompletedCount(agents.map((agent) => developmentArtifacts?.agents?.[agent.agentId]))
  if (!agents.length && !error) return null

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
          <span className={cx('development-count')}>{completed}/{agents.length}</span>
        </button>
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
              <DevelopmentStatusDot progress={developmentArtifacts?.agents?.[agent.agentId]} />
              <IterationBadge
                artifactBranch={developmentArtifacts?.agents?.[agent.agentId]?.completedBranchName}
                currentBranch={currentBranch}
              />
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
