import { cx } from '../../../../utils'
import '../AgentDevelopmentDetail/AgentDevelopmentDetail.less'
import { Card, Descriptions, Tabs } from 'antd'
import type { ReactElement } from 'react'
import { MODULES, FIELDS, settingText } from '../AgentDevelopmentDetail/agentSettingsDisplay'
import { recordItems, textValue, asRecord, type JsonRecord } from './TechnicalPlanDocPanelData'

/** 在原技术规划审阅面板展示当前 Runtime 的全部七模块，配置随技术规划共同确认。 */
export default function TechnicalPlanAgentSection({ plan }: { plan: JsonRecord }): ReactElement {
  return (
    <section
      className={cx('agent-review-section')}
      role="tabpanel"
      id="technical-plan-panel-agents"
      aria-labelledby="technical-plan-tab-agents"
    >
      {recordItems(plan.agent_contracts).map((agent) => (
        <Card key={textValue(agent.agentId)} title={textValue(agent.name)}>
          <p>{textValue(agent.purpose)}</p>
          <p>入口页面：{Array.isArray(agent.pageIds) ? agent.pageIds.join('、') : ''}</p>
          <Tabs
            items={Object.entries(MODULES).map(([key, label]) => ({
              key,
              label,
              children: (
                <Descriptions bordered column={1}>
                  {Object.entries(asRecord(asRecord(agent.agentSettings)[key])).map(
                    ([field, value]) => (
                      <Descriptions.Item label={FIELDS[field] || field} key={field}>
                        {settingText(value)}
                      </Descriptions.Item>
                    )
                  )}
                </Descriptions>
              )
            }))}
          />
        </Card>
      ))}
    </section>
  )
}
