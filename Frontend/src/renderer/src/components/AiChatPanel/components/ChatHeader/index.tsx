import { Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EditorMode } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ChatCopy } from '../../types'
import './ChatHeader.less'

const { Text, Title } = Typography

type ChatHeaderProps = {
  copy: ChatCopy[EditorMode]
  editorMode: EditorMode
}

export default function ChatHeader({ copy, editorMode }: ChatHeaderProps): ReactElement {
  return (
    <header className={cx('ai-chat-header')}>
      <div className={cx('ai-chat-title')}>
        <Text className={cx('editor-scope-tag', editorMode)}>WORKFLOW</Text>
        <Title level={4}>{copy.title}</Title>
        <Text type="secondary">{copy.description}</Text>
      </div>
    </header>
  )
}
