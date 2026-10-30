import { ArrowRightOutlined, BranchesOutlined } from '@ant-design/icons'
import { Typography } from 'antd'
import { useMemo, type ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  asRecord,
  recordItems,
  textValue,
  stringItems,
  type JsonRecord
} from './TechnicalPlanDocPanelData'
import { agentInvocation } from './TechnicalPlanAgentContracts'

const { Text } = Typography

type PageBindingItem = {
  key: string
  triggerLabel: string
  endpointLabel: string
  agentLabel?: string
}

type PageBindingGroup = {
  key: string
  pageName: string
  bindings: PageBindingItem[]
}

/** 从已启用产品入口和当前技术合同中读取页面的智能体动作，复用固定调用契约。 */
function agentPageBindings(
  pageId: string,
  agents: JsonRecord[],
  productPlan: JsonRecord
): PageBindingItem[] {
  const productPage = recordItems(productPlan.pages).find((page) => page.pageId === pageId)
  const actions = recordItems(productPage?.actions)
  return agents.flatMap((agent): PageBindingItem[] => {
    const agentId = textValue(agent.agentId)
    if (!agentId || !stringItems(agent.pageIds).includes(pageId)) return []
    const productAgent = recordItems(productPlan.agents).find((item) => item.agentId === agentId)
    const invocation = agentInvocation(agentId)
    return recordItems(productAgent?.pageActionBindings)
      .filter((binding) => binding.pageId === pageId && asRecord(binding.surface).enabled === true)
      .flatMap((binding) => stringItems(binding.actionIds))
      .map(
        (actionId): PageBindingItem => ({
          key: `${pageId}:agent:${agentId}:${actionId}`,
          triggerLabel: textValue(
            actions.find((action) => action.actionId === actionId)?.name,
            actionId
          ),
          endpointLabel: `${invocation.method} ${invocation.path}`,
          agentLabel: `${textValue(agent.name, agentId)} · AG-UI`
        })
      )
  })
}

/** 汇总页面的业务接口与智能体调用，并使用产品规划中的中文页面名称。 */
function pageBindingGroups(
  pages: JsonRecord[],
  contracts: JsonRecord[],
  productPlan: JsonRecord,
  agents: JsonRecord[]
): PageBindingGroup[] {
  const endpointMap = new Map<string, JsonRecord>()
  const pageNameMap = new Map<string, string>()
  contracts.forEach((contract) =>
    recordItems(contract.endpoints).forEach((endpoint) => {
      const endpointId = textValue(endpoint.id)
      if (endpointId) endpointMap.set(endpointId, endpoint)
    })
  )
  recordItems(productPlan.pages).forEach((page) => {
    const pageId = textValue(page.pageId)
    const pageName = textValue(page.name)
    if (pageId && pageName) pageNameMap.set(pageId, pageName)
  })

  return pages.flatMap((page, pageIndex): PageBindingGroup[] => {
    const pageId = textValue(page.pageId, `page-${pageIndex + 1}`)
    const references = asRecord(page.references)
    const bindings = recordItems(references.endpoint_dependencies).map(
      (dependency, dependencyIndex): PageBindingItem => {
        const endpointId = textValue(dependency.endpoint_id)
        const endpoint = endpointMap.get(endpointId) || {}
        return {
          key: `${pageId}:${endpointId || dependencyIndex}`,
          triggerLabel: textValue(dependency.trigger, '未声明触发条件'),
          endpointLabel: `${textValue(endpoint.method, 'GET').toUpperCase()} ${textValue(endpoint.path, endpointId || '未解析')}`
        }
      }
    )
    bindings.push(...agentPageBindings(pageId, agents, productPlan))
    return bindings.length
      ? [{ key: pageId, pageName: pageNameMap.get(pageId) || pageId, bindings }]
      : []
  })
}

/** 按页面分组展示中文页面名、触发条件和 Endpoint 三段调用关系。 */
export function PageBindingsSection({
  pages,
  contracts,
  productPlan,
  agents = [],
  sectionKey
}: {
  pages: JsonRecord[]
  contracts: JsonRecord[]
  productPlan: JsonRecord
  agents?: JsonRecord[]
} & { sectionKey: 'page-bindings' }): ReactElement {
  const groups = useMemo(
    () => pageBindingGroups(pages, contracts, productPlan, agents),
    [contracts, pages, productPlan, agents]
  )

  return (
    <section
      aria-label="页面绑定"
      className={cx('technical-plan-section')}
      id={`technical-plan-panel-${sectionKey}`}
      role="tabpanel"
    >
      <div className={cx('technical-plan-section-title')}>
        <BranchesOutlined /> <span>页面绑定</span>
      </div>
      <div className={cx('technical-plan-page-binding-list')}>
        {groups.length ? (
          groups.map((group) => (
            <article className={cx('technical-plan-page-binding-group')} key={group.key}>
              <strong className={cx('technical-plan-page-binding-name')}>{group.pageName}</strong>
              <div className={cx('technical-plan-page-binding-rows')}>
                {group.bindings.map((binding) => (
                  <div className={cx('technical-plan-page-binding-row')} key={binding.key}>
                    <span>{binding.triggerLabel}</span>
                    <ArrowRightOutlined aria-hidden="true" />
                    {binding.agentLabel ? (
                      <div className={cx('technical-plan-page-binding-agent')}>
                        <span>{binding.agentLabel}</span>
                        <code title={binding.endpointLabel}>{binding.endpointLabel}</code>
                      </div>
                    ) : (
                      <code>{binding.endpointLabel}</code>
                    )}
                  </div>
                ))}
              </div>
            </article>
          ))
        ) : (
          <Text type="secondary">暂无页面绑定</Text>
        )}
      </div>
    </section>
  )
}
