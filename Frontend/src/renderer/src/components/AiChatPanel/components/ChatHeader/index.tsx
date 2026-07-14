import { FileTextOutlined, MoonOutlined, SunOutlined } from '@ant-design/icons'
import { Button, Typography } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { cx } from '../../../../utils'
import './ChatHeader.less'

const { Text } = Typography

type ChatHeaderProps = {
  actions?: ReactNode
  onThemeChange: (theme: 'light' | 'dark') => void
  pageTitle: string
  theme: 'light' | 'dark'
}

export default function ChatHeader({
  actions,
  onThemeChange,
  pageTitle,
  theme
}: ChatHeaderProps): ReactElement {
  return (
    <header className={cx('ai-chat-header')}>
      <div className={cx('ai-chat-breadcrumb')}>
        <FileTextOutlined className={cx('ai-chat-page-icon')} />
        <Text strong>Pages</Text>
        <Text className={cx('ai-chat-breadcrumb-separator')}>/</Text>
        <Text className={cx('ai-chat-page-title')} strong title={pageTitle}>{pageTitle}</Text>
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
