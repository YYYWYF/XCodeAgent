import { RobotOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { BusinessAgentOption } from '../../../../../../shared/businessAgent'
import type { DevelopmentArtifacts } from '../../../../typings'
import { cx } from '../../../../utils'
import { agentInvocation } from '../DocPanel/TechnicalPlanAgentContracts'
import DevelopmentStatusDot from './DevelopmentStatusDot'
import './AgentInterfaceRows.less'

type Props = {
  agents: BusinessAgentOption[]
  selectedAgentId?: string
  developmentArtifacts?: DevelopmentArtifacts
  onSelect?: (agent: BusinessAgentOption) => void
}

/** 按正式 Agent 派生只读接口行，状态沿用 Agent，不新增 Endpoint 开发任务。 */
export default function AgentInterfaceRows({
  agents,
  selectedAgentId,
  developmentArtifacts,
  onSelect
}: Props): ReactElement {
  return (
    <>
      {agents.map((agent) => {
        const invocation = agentInvocation(agent.agentId)
        return (
          <button
            key={agent.agentId}
            type="button"
            aria-current={selectedAgentId === agent.agentId ? 'true' : undefined}
            aria-label={`${agent.label}，智能体接口，${invocation.method} ${invocation.path}`}
            className={cx(
              'api-row',
              'agent-interface-row',
              selectedAgentId === agent.agentId && 'selected'
            )}
            disabled={!onSelect}
            onClick={() => onSelect?.(agent)}
            title={`${invocation.method} ${invocation.path} · AG-UI / SSE`}
          >
            <RobotOutlined className={cx('api-endpoint-icon')} />
            <span className={cx('api-copy')}>
              <strong>{agent.label}</strong>
              <span className={cx('agent-interface-badge')}>智能体接口</span>
            </span>
            <DevelopmentStatusDot progress={developmentArtifacts?.agents?.[agent.agentId]} />
          </button>
        )
      })}
    </>
  )
}
