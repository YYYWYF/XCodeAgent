import { CheckCircleOutlined, DesktopOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Button, Dropdown, Typography } from 'antd'
import type { MenuProps } from 'antd'
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
  onThemeChange: (theme: 'light' | 'dark' | 'system') => void
  theme: 'light' | 'dark'
  themePreference: 'light' | 'dark' | 'system'
  title: string
  workspaceName: string
}

const themeItems: MenuProps['items'] = [
  { key: 'system', icon: <DesktopOutlined />, label: '跟随系统' },
  { key: 'light', icon: <SunOutlined />, label: '浅色主题' },
  { key: 'dark', icon: <MoonOutlined />, label: '深色主题' }
]

export default function ChatHeader({
  actions,
  copy,
  editorMode,
  onThemeChange,
  theme,
  themePreference,
  title,
  workspaceName
}: ChatHeaderProps): ReactElement {
  const handleThemeChange: MenuProps['onClick'] = ({ key }) => {
    onThemeChange(key as 'light' | 'dark' | 'system')
  }

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
        <Dropdown
          menu={{
            items: themeItems,
            onClick: handleThemeChange,
            selectable: true,
            selectedKeys: [themePreference]
          }}
          overlayClassName={cx('workbench-theme-dropdown', theme)}
          placement="bottomRight"
          trigger={['click']}
        >
          <Button
            aria-label="切换工作台主题"
            icon={theme === 'dark' ? <MoonOutlined /> : <SunOutlined />}
            title="切换主题"
            type="text"
          />
        </Dropdown>
      </div>
    </header>
  )
}
