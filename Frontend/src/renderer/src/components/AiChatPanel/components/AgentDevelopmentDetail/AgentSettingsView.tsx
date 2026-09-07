import { Collapse, Tag } from 'antd'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningAgentOption } from '../../../../typings'
import { cx } from '../../../../utils'

const SETTING_SECTIONS = [
  ['prompt', '人设与 System Prompt'],
  ['model', '模型配置'],
  ['memory', '记忆模块'],
  ['tools', '工具配置'],
  ['skills', 'Skills'],
  ['knowledge', '知识库配置'],
  ['context', '上下文配置']
] as const

type Props = Pick<DevelopmentPlanningAgentOption, 'agentSettings'>

/** 以只读折叠面板完整展示 Agent Contract 的七段 Settings。 */
export default function AgentSettingsView({ agentSettings }: Props): ReactElement {
  return (
    <section className={cx('agent-detail-section')}>
      <div className={cx('agent-detail-section-title')}>
        <h3>Agent Settings</h3>
        <Tag>只读 · 来源 TechnicalPlan</Tag>
      </div>
      <Collapse defaultActiveKey={['prompt', 'model']} ghost>
        {SETTING_SECTIONS.map(([key, label]) => {
          const value = agentSettings[key] || {}
          const enabled = typeof value.enabled === 'boolean' ? value.enabled : undefined
          return (
            <Collapse.Panel
              header={
                <span className={cx('agent-setting-title')}>
                  {label}
                  {enabled !== undefined ? (
                    <Tag color={enabled ? 'purple' : 'default'}>
                      {enabled ? '已启用' : '当前版本未启用'}
                    </Tag>
                  ) : null}
                </span>
              }
              key={key}
            >
              <pre className={cx('agent-json-view')}>{JSON.stringify(value, null, 2)}</pre>
            </Collapse.Panel>
          )
        })}
      </Collapse>
    </section>
  )
}
