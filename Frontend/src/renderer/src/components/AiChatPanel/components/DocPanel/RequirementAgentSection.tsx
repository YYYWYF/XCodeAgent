import { cx } from '../../../../utils'
import { RobotOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import {
  asRecord,
  recordItems,
  requirementPageRows,
  stringItems,
  textValue,
  type JsonRecord
} from './RequirementDocPanelData'
import './RequirementAgents.less'

type Props = {
  productPlan: JsonRecord
  spec: JsonRecord
}

/** 以需求文档统一卡片只读展示智能体职责、能力、边界及关联页面。 */
export default function RequirementAgentSection({ productPlan, spec }: Props): ReactElement {
  const plannedAgents = recordItems(productPlan.agents)
  const requirements = recordItems(spec.agent_requirements)
  const agents = plannedAgents.length ? plannedAgents : requirements
  const pageNames = new Map(
    requirementPageRows(productPlan, recordItems(spec.pages)).map((page) => [
      page.pageId,
      page.name
    ])
  )

  return (
    <section
      className={cx('requirement-doc-section')}
      role="tabpanel"
      id="requirement-doc-panel-agents"
      aria-labelledby="requirement-doc-tab-agents"
    >
      <div className={cx('requirement-doc-section-title')}>
        <RobotOutlined /> <span>业务智能体</span>
        <span className={cx('requirement-doc-section-count')}>{agents.length}</span>
      </div>
      <div className={cx('requirement-doc-card-list')}>
        {agents.map((agent) => {
          const capabilities = Array.isArray(agent.capabilities)
            ? agent.capabilities
                .map((item) => (typeof item === 'string' ? item : textValue(asRecord(item).name)))
                .filter(Boolean)
            : []
          const boundaries = stringItems(agent.boundaries)
          const bindings = recordItems(agent.pageActionBindings)
          return (
            <article
              className={cx('requirement-doc-page-card', 'requirement-agent-card')}
              key={textValue(agent.agentId)}
            >
              <header className={cx('requirement-doc-page-title')}>
                <strong>{textValue(agent.name, '未命名智能体')}</strong>
              </header>
              <section className={cx('requirement-doc-page-zone')}>
                <span className={cx('requirement-doc-page-zone-title')}>职责</span>
                <p className={cx('requirement-doc-page-zone-body')}>
                  {textValue(agent.purpose, '暂无职责说明')}
                </p>
              </section>
              {[
                { label: '业务能力', items: capabilities },
                { label: '业务边界', items: boundaries }
              ].map(({ label, items }) => (
                <section className={cx('requirement-doc-page-zone')} key={label}>
                  <span className={cx('requirement-doc-page-zone-title')}>{label}</span>
                  {items.length ? (
                    <ul className={cx('requirement-agent-list')}>
                      {items.map((item, index) => (
                        <li key={`${label}-${index}`}>{item}</li>
                      ))}
                    </ul>
                  ) : (
                    <p className={cx('requirement-doc-page-desc')}>暂无{label}说明</p>
                  )}
                </section>
              ))}
              {bindings.length ? (
                <section className={cx('requirement-doc-page-zone')}>
                  <span className={cx('requirement-doc-page-zone-title')}>关联页面</span>
                  {bindings.map((binding) => {
                    const pageId = textValue(binding.pageId)
                    const surface = asRecord(binding.surface)
                    return (
                      <div className={cx('requirement-agent-surface-row')} key={pageId}>
                        <strong>{pageNames.get(pageId) || pageId}</strong>
                        <span>
                          {surface.type === 'standalone_page'
                            ? '独立对话页面'
                            : surface.enabled === true
                              ? '浮窗已开启'
                              : '浮窗已关闭'}
                        </span>
                      </div>
                    )
                  })}
                </section>
              ) : null}
            </article>
          )
        })}
      </div>
    </section>
  )
}
