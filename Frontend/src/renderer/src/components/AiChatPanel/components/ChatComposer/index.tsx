import { FolderOpenOutlined, SendOutlined, StopOutlined } from '@ant-design/icons'
import { Alert, Button, Input, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { EditorMode } from '../../../../typings'
import { cx } from '../../../../utils'
import type { ChatCopy } from '../../types'
import './ChatComposer.less'

const { Text } = Typography
const { TextArea } = Input

type ChatComposerProps = {
  copy: ChatCopy[EditorMode]
  draft: string
  error?: string
  loading: boolean
  onDraftChange: (value: string) => void
  onSend: () => Promise<void>
  onStopGenerating: () => void
  stopping: boolean
  workspaceRoot: string
}

export default function ChatComposer({
  copy,
  draft,
  error,
  loading,
  onDraftChange,
  onSend,
  onStopGenerating,
  stopping,
  workspaceRoot
}: ChatComposerProps): ReactElement {
  return (
    <div className={cx('ai-chat-composer')}>
      {error && <Alert message={error} showIcon type="error" />}
      <TextArea
        aria-label={`${copy.title}输出内容`}
        autoSize={{ minRows: 3, maxRows: 6 }}
        placeholder={copy.placeholder}
        value={draft}
        onChange={(event) => onDraftChange(event.target.value)}
        onPressEnter={(event) => {
          if (!event.shiftKey) {
            event.preventDefault()
            onSend()
          }
        }}
      />
      <div className={cx('ai-chat-composer-footer')}>
        <Text className={cx('workspace-root-label')} title={workspaceRoot}>
          <FolderOpenOutlined /> 工作目录：{workspaceRoot}
        </Text>
        {loading ? (
          <Button danger disabled={stopping} icon={<StopOutlined />} onClick={onStopGenerating}>
            {stopping ? '正在停止...' : '停止生成'}
          </Button>
        ) : (
          <Button disabled={!draft.trim()} icon={<SendOutlined />} onClick={onSend} type="primary">
            发送给 Workflow
          </Button>
        )}
      </div>
    </div>
  )
}
