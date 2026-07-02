import { CopyOutlined, SendOutlined, UploadOutlined } from '@ant-design/icons'
import { Button, Input, Tag } from 'antd'
import type { ChangeEvent } from 'react'
import { useMemo, useRef } from 'react'
import type { TaskDetail } from '../../../types/task'
import './TaskConversationPanel.less'

type ConversationMessage = {
  id: string
  role: 'assistant' | 'user'
  content: string
  createdAt: string
  toolCount?: number
}

type TaskConversationPanelProps = {
  task: TaskDetail
}

const toneColor = {
  success: 'green',
  warning: 'orange'
} as const

const getWorkspaceTitle = (task: TaskDetail): string =>
  task.id === '45' ? '生成库房管理应用' : task.title

const buildConversationMessages = (task: TaskDetail): ConversationMessage[] => [
  {
    id: `${task.id}-user-1`,
    role: 'user',
    content:
      task.id === '45'
        ? '帮我生成一个库房管理应用，包含库房物品管理明细、以日期为单位的物品统计、区分超管和用户'
        : `帮我完成「${task.title}」，并生成可预览的页面效果。`,
    createdAt: '2026-06-22 11:16:42:888'
  },
  {
    id: `${task.id}-assistant-1`,
    role: 'assistant',
    content: `我将帮你开发一个${getWorkspaceTitle(task)}。让我先调用需求分析代理生成详细的规范文档。`,
    createdAt: '2026-06-22 11:17:20:613',
    toolCount: 1
  },
  {
    id: `${task.id}-assistant-2`,
    role: 'assistant',
    content: `我将为您开发一个${getWorkspaceTitle(task)}。首先让我进行范围识别，然后输出完整的需求规格文档。`,
    createdAt: '2026-06-22 11:19:43:353'
  },
  {
    id: `${task.id}-assistant-3`,
    role: 'assistant',
    content: '需求分析完成！我已收到详细的规范文档。现在让我创建任务计划。',
    createdAt: '2026-06-22 11:19:58:059',
    toolCount: 1
  }
]

function TaskConversationPanel({ task }: TaskConversationPanelProps): React.JSX.Element {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const messages = useMemo(() => buildConversationMessages(task), [task])

  const openFilePicker = (): void => {
    fileInputRef.current?.click()
  }

  const handleFileChange = (event: ChangeEvent<HTMLInputElement>): void => {
    const file = event.currentTarget.files?.[0]

    if (file) {
      console.log(file)
    }

    event.currentTarget.value = ''
  }

  return (
    <section className="task-chat">
      <header className="task-chat__header">
        <div className="task-chat__meta">
          <h1>{getWorkspaceTitle(task)}</h1>
          <span>
            {task.id}
            <CopyOutlined className="task-chat__copy-icon" />
          </span>
        </div>
        <div className="task-chat__tags">
          <Tag color="blue">devagentweb</Tag>
          <Tag color={toneColor[task.statusTone]}>{task.status}</Tag>
        </div>
      </header>

      <div className="task-chat__messages">
        {messages.map((message) => (
          <article
            className={`task-chat__message task-chat__message--${message.role}`}
            key={message.id}
          >
            <div className="task-chat__bubble">{message.content}</div>
            {message.toolCount ? (
              <div className="task-chat__tool">{message.toolCount} tool</div>
            ) : null}
            <time>{message.createdAt}</time>
          </article>
        ))}
      </div>

      <footer className="task-chat__composer">
        <input
          className="task-chat__file-input"
          ref={fileInputRef}
          type="file"
          onChange={handleFileChange}
        />
        <Button
          aria-label="上传附件"
          className="task-chat__upload-button"
          icon={<UploadOutlined />}
          onClick={openFilePicker}
        />
        <Input.TextArea autoSize={false} maxLength={5000} placeholder="请输入..." />
        <Button
          aria-label="发送"
          className="task-chat__send"
          icon={<SendOutlined />}
          shape="circle"
          type="primary"
        />
        <span className="task-chat__counter">0/5000</span>
      </footer>
    </section>
  )
}

export default TaskConversationPanel
