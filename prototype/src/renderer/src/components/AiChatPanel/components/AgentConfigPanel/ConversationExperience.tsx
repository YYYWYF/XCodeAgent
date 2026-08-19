import { Switch, Typography } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'
import type { AgentConfigConversationSettings } from './types'

const { Text } = Typography

type Props = {
  readOnly?: boolean
  settings: AgentConfigConversationSettings
  onChange: (settings: AgentConfigConversationSettings) => void
}

type ConversationSettingProps = {
  description: string
  label: string
  onChange: (checked: boolean) => void
  readOnly?: boolean
  value: boolean
}

/** 渲染对话体验中的单项开关和辅助说明。 */
function ConversationSetting({
  description,
  label,
  onChange,
  readOnly = false,
  value
}: ConversationSettingProps): ReactElement {
  return (
    <div className={cx('agent-config-conversation-setting')}>
      <div className={cx('agent-config-conversation-copy')}>
        <Text>{label}</Text>
        <Text type="secondary">{description}</Text>
      </div>
      <Switch
        aria-label={label}
        checked={value}
        disabled={readOnly}
        onChange={onChange}
        size="small"
      />
    </div>
  )
}

/** 渲染配置页中的对话体验开关集合。 */
export default function ConversationExperience({
  readOnly = false,
  settings,
  onChange
}: Props): ReactElement {
  return (
    <div className={cx('agent-config-conversation-list')}>
      <ConversationSetting
        description="保留当前试运行中的连续消息上下文。"
        label="连续多轮对话"
        onChange={(multiTurn) => onChange({ ...settings, multiTurn })}
        readOnly={readOnly}
        value={settings.multiTurn}
      />
      <ConversationSetting
        description="回复完成后展示工具调用和可核验证据。"
        label="展示工具证据"
        onChange={(toolEvidence) => onChange({ ...settings, toolEvidence })}
        readOnly={readOnly}
        value={settings.toolEvidence}
      />
      <ConversationSetting
        description="工具失败时保留重试入口，不执行未确认的写操作。"
        label="失败后允许重试"
        onChange={(retryOnFailure) => onChange({ ...settings, retryOnFailure })}
        readOnly={readOnly}
        value={settings.retryOnFailure}
      />
    </div>
  )
}
