import { QuestionCircleOutlined } from '@ant-design/icons'
import { Button, Input, Tooltip, Typography } from 'antd'
import type { ReactElement } from 'react'
import {
  buildOptimizedAgentPersonaReplyLogic,
  type AgentConfigState
} from '../../../../agentConfig'
import type { DevelopmentPlanningAgent } from '../../../../agentDevelopment'
import { cx } from '../../../../utils'
import './AgentPersonaReplyLogic.less'

const { Text } = Typography

type Props = {
  agent: Pick<DevelopmentPlanningAgent, 'label' | 'purpose' | 'tools' | 'permissions'>
  readOnly?: boolean
  value: AgentConfigState['personaReplyLogic']
  onChange: (value: string) => void
}

/** 渲染智能体的人设与回复逻辑编辑区，并保持配置修改仍由父级统一管理。 */
export default function AgentPersonaReplyLogic({
  agent,
  readOnly = false,
  value,
  onChange
}: Props): ReactElement {
  /** 根据当前智能体的已确认信息生成优化后的本地 Markdown 草稿。 */
  const handleOptimize = (): void => {
    if (readOnly) return
    onChange(
      buildOptimizedAgentPersonaReplyLogic({
        label: agent.label,
        purpose: agent.purpose,
        tools: agent.tools,
        permissions: agent.permissions
      })
    )
  }

  return (
    <section
      aria-label="人设与回复逻辑"
      className={cx('agent-persona-reply-logic', readOnly && 'read-only')}
    >
      <header className={cx('agent-persona-reply-logic-header')}>
        <div className={cx('agent-persona-reply-logic-title')}>
          <span aria-hidden="true" className={cx('agent-persona-reply-logic-marker')} />
          <Text strong>人设与回复逻辑</Text>
          <Tooltip title="定义智能体的角色、目标、技能以及回复时需要遵守的要求与限制。">
            <QuestionCircleOutlined aria-label="人设与回复逻辑说明" />
          </Tooltip>
        </div>
        <Button
          aria-label="智能优化人设与回复逻辑"
          className={cx('agent-persona-reply-logic-optimize')}
          disabled={readOnly}
          onClick={handleOptimize}
          title="根据当前智能体的职责、工具和权限整理模板"
          type="link"
        >
          智能优化
        </Button>
      </header>
      <div className={cx('agent-persona-reply-logic-editor')}>
        <Input.TextArea
          aria-label="人设与回复逻辑编辑器"
          className={cx('agent-persona-reply-logic-textarea')}
          onChange={(event) => onChange(event.target.value)}
          readOnly={readOnly}
          rows={14}
          spellCheck={false}
          value={value}
        />
      </div>
    </section>
  )
}
