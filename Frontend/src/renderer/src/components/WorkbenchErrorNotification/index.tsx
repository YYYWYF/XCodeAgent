import { notification } from 'antd'
import type { ReactElement, ReactNode } from 'react'
import { useContext, useEffect, useId, useRef } from 'react'
import { cx } from '../../utils'
import { UiReadVisibility } from '../../hooks/uiReadVisibility'
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
  const visible = useContext(UiReadVisibility)
  const visibleRef = useRef(visible)
  visibleRef.current = visible
  const instanceId = useId()
  const key = `workbench-error-${instanceId}`
  const dismissed = useRef({ workspace, notices: new Set<string>() })

  useEffect(() => {
    // 工作区内记住已关闭的错误，切换会话、阶段或刷新进度都不会重新弹出同一提示。
    if (dismissed.current.workspace !== workspace) {
      dismissed.current = { workspace, notices: new Set() }
    }
    // 工作台被隐藏时组件仍挂载；全局 Portal 必须显式关闭，不能留在首页或其他页面。
    if (!visible || !noticeId || dismissed.current.notices.has(noticeId)) {
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
        if (visibleRef.current && dismissed.current.workspace === workspace) dismissed.current.notices.add(noticeId)
      }
    })
  }, [api, children, key, noticeId, visible, workspace])

  // 离开工作台时只移除本组件的通知，不销毁其他功能的 Ant Design 通知。
  useEffect(() => () => notification.close(key), [key])

  return contextHolder
}
