import { Button, Result, Spin } from 'antd'
import type { PointerEvent } from 'react'
import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useApi } from '../../context/ApiContext'
import type { TaskDetail } from '../../types/task'
import TaskConversationPanel from './TaskConversationPanel/TaskConversationPanel'
import TaskWorkbench from './TaskWorkbench/TaskWorkbench'
import './TaskDetailPage.less'

const MIN_CHAT_WIDTH = 300
const MIN_WORKBENCH_WIDTH = 420
const DEFAULT_CHAT_WIDTH = 390

function TaskDetailPage(): React.JSX.Element {
  const api = useApi()
  const { taskId = '' } = useParams()
  const resizeStateRef = useRef<{ left: number; width: number } | null>(null)
  const workspaceRef = useRef<HTMLDivElement>(null)
  const [chatWidth, setChatWidth] = useState(DEFAULT_CHAT_WIDTH)
  const [task, setTask] = useState<TaskDetail | null>(null)
  const [loading, setLoading] = useState(true)
  const [errorMessage, setErrorMessage] = useState('')

  useEffect(() => {
    let isActive = true

    const loadTaskDetail = async (): Promise<void> => {
      if (!taskId) {
        setTask(null)
        setErrorMessage('缺少任务 ID')
        setLoading(false)
        return
      }

      setLoading(true)
      setErrorMessage('')

      try {
        const taskDetail = await api.tasks.getDetail(taskId)

        if (isActive) {
          setTask(taskDetail)
        }
      } catch (error) {
        console.error(error)

        if (isActive) {
          setTask(null)
          setErrorMessage('任务加载失败')
        }
      } finally {
        if (isActive) {
          setLoading(false)
        }
      }
    }

    void loadTaskDetail()

    return () => {
      isActive = false
    }
  }, [api, taskId])

  if (loading) {
    return (
      <div className="task-page__not-found">
        <Spin tip="加载任务..." />
      </div>
    )
  }

  if (!task) {
    return (
      <div className="task-page__not-found">
        <Result
          extra={
            <Link to="/skill">
              <Button type="primary">返回Skill管理</Button>
            </Link>
          }
          status={errorMessage ? 'warning' : '404'}
          subTitle={errorMessage || `未找到 ID 为 ${taskId || '-'} 的任务数据`}
          title={errorMessage ? '任务加载失败' : '任务不存在'}
        />
      </div>
    )
  }

  const handleResizeStart = (event: PointerEvent<HTMLButtonElement>): void => {
    const workspace = workspaceRef.current

    if (!workspace) {
      return
    }

    const rect = workspace.getBoundingClientRect()
    resizeStateRef.current = {
      left: rect.left,
      width: rect.width
    }
    event.currentTarget.setPointerCapture(event.pointerId)
    event.preventDefault()
  }

  const handleResizeMove = (event: PointerEvent<HTMLButtonElement>): void => {
    const resizeState = resizeStateRef.current

    if (!resizeState) {
      return
    }

    const maxChatWidth = Math.max(MIN_CHAT_WIDTH, resizeState.width - MIN_WORKBENCH_WIDTH)
    const nextWidth = Math.min(
      Math.max(event.clientX - resizeState.left, MIN_CHAT_WIDTH),
      maxChatWidth
    )
    setChatWidth(nextWidth)
  }

  const handleResizeEnd = (event: PointerEvent<HTMLButtonElement>): void => {
    if (resizeStateRef.current) {
      resizeStateRef.current = null
      event.currentTarget.releasePointerCapture(event.pointerId)
    }
  }

  return (
    <section className="task-page" ref={workspaceRef}>
      <div className="task-page__chat" style={{ width: chatWidth }}>
        <TaskConversationPanel task={task} />
      </div>
      <button
        aria-label="拖拽调整对话区宽度"
        className="task-page__resizer"
        style={{ left: chatWidth }}
        type="button"
        onPointerCancel={handleResizeEnd}
        onPointerDown={handleResizeStart}
        onPointerMove={handleResizeMove}
        onPointerUp={handleResizeEnd}
      >
        <span />
      </button>
      <TaskWorkbench />
    </section>
  )
}

export default TaskDetailPage
