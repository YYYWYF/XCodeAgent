import { CheckCircleOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import type { EditorMode } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ChatCopy } from '../../types'
import './ChatHeader.less'

const { Text, Title } = Typography

type ChatHeaderProps = {
  actions?: ReactNode
  copy: ChatCopy[EditorMode]
  editorMode: EditorMode
  onThemeChange: (theme: 'light' | 'dark') => void
  theme: 'light' | 'dark'
  title: string
  workspaceName: string
}

export default function ChatHeader({
  actions,
  copy,
  editorMode,
  onThemeChange,
  theme,
  title,
  workspaceName
}: ChatHeaderProps): ReactElement {
  return (
    <header className={cx('ai-chat-header')}>
      <div className={cx('ai-chat-title')}>
        <div className={cx('ai-chat-title-line')}>
          <Title level={4}>{title}</Title>
          <Text className={cx('ai-chat-saved')}>
            <CheckCircleOutlined /> 已保存
          </Text>
        </div>
        <Text className={cx('ai-chat-workspace-path')} title={copy.description}>
          {workspaceName} <span>/</span> {editorMode === 'frontend' ? '前端工作流' : '后端工作流'}
        </Text>
      </div>
      <div className={cx('ai-chat-header-actions')}>
        {actions}
        <Button
          aria-label={`切换为${theme === 'dark' ? '浅色' : '深色'}主题`}
          icon={theme === 'dark' ? <MoonOutlined /> : <SunOutlined />}
          onClick={() => onThemeChange(theme === 'dark' ? 'light' : 'dark')}
          title={`切换为${theme === 'dark' ? '浅色' : '深色'}主题`}
          type="text"
        />
      </div>
    </header>
  )
}
