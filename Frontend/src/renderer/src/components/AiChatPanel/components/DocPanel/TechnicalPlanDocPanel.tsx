import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  ContractSection,
  EntitiesSection,
  PageBindingsSection,
  type SectionKey
} from './TechnicalPlanDocSections'
import { AuthorizationSection } from './TechnicalPlanAuthorizationSection'
import { recordItems, textValue, type JsonRecord } from './TechnicalPlanDocPanelData'
import type { ChangeGroup } from './technicalPlanDiff'
import './TechnicalPlanDocPanel.less'
import './TechnicalPlanContractResponsive.less'

type Props = {
  plan: JsonRecord
  productPlan: JsonRecord
  changes?: ChangeGroup[]
  /** 是否存在上一迭代基线。首轮创建时为 false：仍展示"新增"标识，但不分区。 */
  hasBaseline?: boolean
  /** 规划正在重新生成：此时对比结果不可信，不展示变更标识。 */
  updating?: boolean
  onReviseAuthorization?: () => void
}

const technicalPlanSections: Array<{ key: SectionKey; label: string }> = [
  { key: 'entities', label: '实体' },
  { key: 'api-contracts', label: 'API 契约' },
  { key: 'page-bindings', label: '页面绑定' }
]

/** 渲染右侧技术规划产物视图，所有交互仅改变本面板内的阅读位置。 */
export default function TechnicalPlanDocPanel({
  plan,
  productPlan,
  changes,
  hasBaseline: hasBaselineProp,
  updating,
  onReviseAuthorization
}: Props): ReactElement {
  const entities = recordItems(plan.entities)
  const contracts = recordItems(plan.api_contracts)
  const endpointCount = contracts.reduce(
    (count, contract) => count + recordItems(contract.endpoints).length,
    0
  )
  const pages = recordItems(plan.pages)
  const sections = [...technicalPlanSections, { key: 'authorization' as const, label: '接口权限绑定' }]
  // 正在重新生成时对比结果不可信：不展示任何变更标识，避免用户误判。
  const changeKinds = updating
    ? {}
    : Object.fromEntries(
        (changes || []).flatMap((group) =>
          group.objects.map((object) => [object.id, object.declaredKind || object.kind])
        )
      )
  // 是否分区由调用方给出的"有无基线"决定：首轮全部是新增，不该分出"未变更"一组。
  // 未显式传入时退回旧推导（有变更项即视为有基线），保持其它调用点行为不变。
  const hasBaseline = !updating && (hasBaselineProp ?? (changes || []).some((group) => group.objects.length > 0))
  const [activeSection, setActiveSection] = useState<SectionKey>('api-contracts')
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
        {sections.map((section) => {
          // API 契约展示所有分组的接口总数，其余章节仅在有变更时显示计数。
          const count =
            section.key === 'api-contracts'
              ? endpointCount
              : changes?.find((group) => group.name === section.label)?.objects.length || undefined
          return (
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
              {count !== undefined ? ` · ${count}` : ''}
            </button>
          )
        })}
      </div>
      {resolvedActiveSection === 'entities' ? (
        <EntitiesSection entities={entities} sectionKey="entities" changeKinds={changeKinds} hasBaseline={hasBaseline} />
      ) : null}
      {resolvedActiveSection === 'api-contracts' ? (
        <ContractSection
          changeKinds={changeKinds}
          contracts={contracts}
          hasBaseline={hasBaseline}
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
          changeKinds={changeKinds}
          contracts={contracts}
          pages={pages}
          productPlan={productPlan}
          hasBaseline={hasBaseline}
          sectionKey="page-bindings"
        />
      ) : null}
      {resolvedActiveSection === 'authorization' ? (
        <AuthorizationSection
          changeKinds={changeKinds}
          hasBaseline={hasBaseline}
          onReviseAuthorization={onReviseAuthorization}
          plan={plan}
          productPlan={productPlan}
          sectionKey="authorization"
        />
      ) : null}
    </div>
  )
}
