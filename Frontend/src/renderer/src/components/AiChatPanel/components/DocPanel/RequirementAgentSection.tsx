import { MessageOutlined, RobotOutlined, SafetyCertificateOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import {
  requirementAgentRows,
  type JsonRecord,
  type RequirementAgentRow
} from './RequirementDocPanelData'

const { Text } = Typography

/** 渲染单个智能体的产品能力、入口操作、交互状态、边界和验收标准。 */
function AgentCard({ agent }: { agent: RequirementAgentRow }): ReactElement {
  return (
    <article className={cx('requirement-doc-page-card')}>
      <div className={cx('requirement-doc-page-heading')}>
        <div>
          <strong>{agent.name}</strong>
          <code>{agent.agentId}</code>
        </div>
        {agent.interactionMode ? <span>{agent.interactionMode}</span> : null}
      </div>
      {agent.purpose ? <Text type="secondary">{agent.purpose}</Text> : null}
      {agent.capabilities.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>业务能力</span>
          <div className={cx('requirement-doc-action-list')}>
            {agent.capabilities.map((capability) => (
              <div className={cx('requirement-doc-action')} key={capability.key}>
                <div className={cx('requirement-doc-action-heading')}>
                  <strong>{capability.name}</strong>
                </div>
                {capability.expectedResult ? (
                  <Text type="secondary">预期结果：{capability.expectedResult}</Text>
                ) : null}
              </div>
            ))}
          </div>
        </div>
      ) : null}
      {agent.entryPageIds.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>入口页面</span>
          <div className={cx('requirement-doc-tag-row')}>
            {agent.entryPageIds.map((pageId) => (
              <Tag key={pageId}>{pageId}</Tag>
            ))}
          </div>
        </div>
      ) : null}
      {agent.pageActionBindings.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>页面操作绑定</span>
          <ul>
            {agent.pageActionBindings.map((binding) => (
              <li key={binding.key}>
                <strong>{binding.pageId}</strong>
                <span>{binding.actionIds.join('、')}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {agent.inputDescription || agent.outputDescription || agent.stateRequirements.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>
            <MessageOutlined aria-hidden="true" /> 交互要求
          </span>
          <ul>
            {agent.inputDescription ? (
              <li>
                <strong>用户输入</strong>
                <span>{agent.inputDescription}</span>
              </li>
            ) : null}
            {agent.outputDescription ? (
              <li>
                <strong>智能体输出</strong>
                <span>{agent.outputDescription}</span>
              </li>
            ) : null}
            {agent.stateRequirements.map((state) => (
              <li key={state.key}>
                <strong className={cx('requirement-doc-state-label', `is-${state.key}`)}>
                  {state.label}
                </strong>
                <span>{state.description}</span>
              </li>
            ))}
          </ul>
          {agent.supportsMultiTurn ? <Tag>支持多轮对话</Tag> : null}
        </div>
      ) : null}
      {agent.boundaries.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>
            <SafetyCertificateOutlined aria-hidden="true" /> 业务边界
          </span>
          <ul>
            {agent.boundaries.map((boundary, index) => (
              <li key={`${agent.key}-boundary-${index}`}>
                <span>{boundary}</span>
              </li>
            ))}
          </ul>
        </div>
      ) : null}
      {agent.acceptanceCriteria.length ? (
        <div className={cx('requirement-doc-page-block')}>
          <span className={cx('requirement-doc-page-block-title')}>智能体验收标准</span>
          <ul className={cx('requirement-doc-acceptance')}>
            {agent.acceptanceCriteria.map((criterion, index) => (
              <li key={`${agent.key}-acceptance-${index}`}>{criterion}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  )
}

/** 渲染联合需求文档中的智能体产品规划章节。 */
export function RequirementAgentSection({
  productPlan,
  spec
}: {
  productPlan: JsonRecord
  spec: JsonRecord
}): ReactElement {
  const agents = requirementAgentRows(productPlan, spec)
  return (
    <section
      aria-label="智能体"
      className={cx('requirement-doc-section')}
      id="requirement-doc-panel-agents"
      role="tabpanel"
    >
      <div className={cx('requirement-doc-section-title')}>
        <RobotOutlined /> <span>智能体产品规划</span>
        <span className={cx('requirement-doc-section-count')}>{agents.length}</span>
      </div>
      <div className={cx('requirement-doc-page-list')}>
        {agents.map((agent) => (
          <AgentCard agent={agent} key={agent.key} />
        ))}
      </div>
    </section>
  )
}
