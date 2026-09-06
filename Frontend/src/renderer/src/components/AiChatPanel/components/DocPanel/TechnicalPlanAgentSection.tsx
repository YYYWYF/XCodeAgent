import { RobotOutlined } from '@ant-design/icons'
import { Tag } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import { asRecord, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'
import {
  AgentPlatformDetails,
  AgentProductFacts,
  AgentSettingsReview
} from './TechnicalPlanAgentContractDetails'
import './TechnicalPlanAgentSection.less'

type Props = {
  contracts: JsonRecord[]
  sectionKey: string
}

/** 渲染 TechnicalPlan 中的平台智能体契约，普通应用不会挂载本章节。 */
export default function AgentContractsSection({ contracts, sectionKey }: Props): ReactElement {
  return (
    <section
      aria-label="智能体契约"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <RobotOutlined /> <span>智能体契约</span>
        <Tag>{contracts.length}</Tag>
      </div>
      <div className={cx('technical-plan-agent-list')}>
        {contracts.map((contract, index) => {
          const agentId = textValue(contract.agentId, `agent-${index + 1}`)
          const identity = asRecord(contract.identity)
          const runtime = asRecord(contract.runtime)
          return (
            <article className={cx('technical-plan-agent-card')} key={agentId}>
              <header className={cx('technical-plan-agent-header')}>
                <div>
                  <strong>{textValue(identity.name, agentId)}</strong>
                  <span>{textValue(runtime.serviceName, 'agent-runtime')}</span>
                </div>
                <div>
                  <Tag>{textValue(runtime.framework, 'DeepAgents')}</Tag>
                  <Tag>{textValue(runtime.deployment, 'sidecar')}</Tag>
                </div>
              </header>
              <AgentProductFacts contract={contract} />
              <AgentSettingsReview contract={contract} />
              <AgentPlatformDetails contract={contract} />
            </article>
          )
        })}
      </div>
    </section>
  )
}
