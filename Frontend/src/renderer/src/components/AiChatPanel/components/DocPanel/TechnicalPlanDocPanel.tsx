import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  ArchitectureSection,
  ContractSection,
  EntitiesSection,
  PageBindingsSection,
  type SectionKey
} from './TechnicalPlanDocSections'
import { AuthorizationSection } from './TechnicalPlanAuthorizationSection'
import { authorizationDesignView } from './TechnicalPlanAuthorizationData'
import AgentContractsSection from './TechnicalPlanAgentSection'
import { asRecord, recordItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'
import './TechnicalPlanDocPanel.less'

type Props = {
  plan: JsonRecord
  productPlan: JsonRecord
}

const technicalPlanSections: Array<{ key: SectionKey; label: string }> = [
  { key: 'architecture', label: '架构' },
  { key: 'entities', label: '实体' },
  { key: 'api-contracts', label: 'API 契约' },
  { key: 'page-bindings', label: '页面绑定' }
]

/** 渲染右侧技术规划产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function TechnicalPlanDocPanel({ plan, productPlan }: Props): ReactElement {
  const architecture = asRecord(plan.architecture)
  const entities = recordItems(plan.entities)
  const contracts = recordItems(plan.api_contracts)
  const agentContracts = recordItems(plan.agent_contracts)
  const pages = recordItems(plan.pages)
  const hasAuthorization = Boolean(authorizationDesignView(plan))
  const sections = [
    ...technicalPlanSections.slice(0, 3),
    ...(agentContracts.length ? [{ key: 'agent-contracts' as const, label: '智能体契约' }] : []),
    ...technicalPlanSections.slice(3),
    ...(hasAuthorization ? [{ key: 'authorization' as const, label: '权限' }] : [])
  ]
  const [activeSection, setActiveSection] = useState<SectionKey>(
    agentContracts.length ? 'agent-contracts' : 'api-contracts'
  )
  const resolvedActiveSection = sections.some((section) => section.key === activeSection)
    ? activeSection
    : 'api-contracts'
  const [selectedContractId, setSelectedContractId] = useState('')
  const [selectedEndpointId, setSelectedEndpointId] = useState('')
  const resolvedContract =
    contracts.find(
      (contract, index) => textValue(contract.id, `contract-${index + 1}`) === selectedContractId
    ) ||
    contracts[0] ||
    {}
  const resolvedContractId = textValue(resolvedContract.id, contracts.length ? 'contract-1' : '')
  const endpoints = recordItems(resolvedContract.endpoints)
  const resolvedEndpoint =
    endpoints.find(
      (endpoint, index) =>
        textValue(endpoint.id, `${resolvedContractId}-endpoint-${index + 1}`) === selectedEndpointId
    ) ||
    endpoints[0] ||
    {}
  const resolvedEndpointId = textValue(
    resolvedEndpoint.id,
    endpoints.length ? `${resolvedContractId}-endpoint-1` : ''
  )

  return (
    <div className={cx('technical-plan-doc-panel')}>
      <div className={cx('technical-plan-section-tabs')} role="tablist" aria-label="技术规划章节">
        {sections.map((section) => (
          <button
            aria-controls={`technical-plan-panel-${section.key}`}
            aria-selected={resolvedActiveSection === section.key}
            className={cx(
              'technical-plan-section-tab',
              resolvedActiveSection === section.key && 'is-active'
            )}
            key={section.key}
            id={`technical-plan-tab-${section.key}`}
            onClick={() => setActiveSection(section.key)}
            role="tab"
            type="button"
          >
            {section.label}
          </button>
        ))}
      </div>
      {resolvedActiveSection === 'architecture' ? (
        <ArchitectureSection architecture={architecture} sectionKey="architecture" />
      ) : null}
      {resolvedActiveSection === 'entities' ? (
        <EntitiesSection entities={entities} sectionKey="entities" />
      ) : null}
      {resolvedActiveSection === 'api-contracts' ? (
        <ContractSection
          contracts={contracts}
          onContractChange={(id) => {
            setSelectedContractId(id)
            setSelectedEndpointId('')
          }}
          onEndpointChange={setSelectedEndpointId}
          selectedContract={resolvedContract}
          selectedContractId={resolvedContractId}
          selectedEndpoint={resolvedEndpoint}
          selectedEndpointId={resolvedEndpointId}
          sectionKey="api-contracts"
        />
      ) : null}
      {resolvedActiveSection === 'page-bindings' ? (
        <PageBindingsSection
          contracts={contracts}
          pages={pages}
          productPlan={productPlan}
          sectionKey="page-bindings"
        />
      ) : null}
      {resolvedActiveSection === 'agent-contracts' ? (
        <AgentContractsSection contracts={agentContracts} sectionKey="agent-contracts" />
      ) : null}
      {resolvedActiveSection === 'authorization' ? (
        <AuthorizationSection plan={plan} sectionKey="authorization" />
      ) : null}
    </div>
  )
}
