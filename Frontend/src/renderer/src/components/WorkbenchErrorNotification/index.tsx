import { notification } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useEffect, useId, useRef } from 'react'
import { cx } from '../../utils'
import './index.less'

type WorkbenchErrorNotificationProps = {
  workspace: string
  noticeId?: string
  children: ReactNode
}

/** 用 Ant Design 承载工作台右下角统一提示，关闭只影响展示，不改变任务与恢复状态。 */
export default function WorkbenchErrorNotification({
  workspace,
  noticeId,
  children
}: WorkbenchErrorNotificationProps): ReactElement {
  const [api, contextHolder] = notification.useNotification()
  const instanceId = useId()
  const key = `workbench-error-${instanceId}`
  const dismissed = useRef({ workspace, notices: new Set<string>() })

  useEffect(() => {
    // 工作区内记住已关闭的错误，切换会话、阶段或刷新进度都不会重新弹出同一提示。
    if (dismissed.current.workspace !== workspace) {
      dismissed.current = { workspace, notices: new Set() }
    }
    if (!noticeId || dismissed.current.notices.has(noticeId)) {
      notification.close(key)
      return
    }
    api.open({
      key,
      message: null,
      description: children,
      placement: 'bottomRight',
      bottom: 24,
      duration: 0,
      className: cx('workbench-error-notification'),
      closeIcon: <span aria-label="关闭工作台提示">×</span>,
      // 仅由用户点击关闭时记住本条错误；业务恢复时的自动移除不写入关闭记录。
      onClose: () => {
        if (dismissed.current.workspace === workspace) dismissed.current.notices.add(noticeId)
      }
    })
  }, [api, children, key, noticeId, workspace])

  // 离开工作台时只移除本组件的通知，不销毁其他功能的 Ant Design 通知。
  useEffect(() => () => notification.close(key), [key])

  return contextHolder
}
