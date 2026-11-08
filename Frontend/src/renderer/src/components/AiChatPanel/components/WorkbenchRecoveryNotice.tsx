import type { ReactElement, ReactNode } from 'react'
import { useContext } from 'react'
import { UiReadVisibility } from '../../../hooks/uiReadVisibility'
import { cx } from '../../../utils'
import WorkbenchErrorNotification from '../../WorkbenchErrorNotification'
import './WorkbenchRecoveryNotice.less'

type Props = {
  workspace: string
  noticeId?: string
  inline: boolean
  children: ReactNode
}

/** 当前会话的恢复入口常驻输入框上方，通知仅承载共享全局故障。 */
export default function WorkbenchRecoveryNotice({ workspace, noticeId, inline, children }: Props): ReactElement {
  const visible = useContext(UiReadVisibility)
  return <>
    <WorkbenchErrorNotification workspace={workspace} noticeId={inline ? undefined : noticeId}>
      {children}
    </WorkbenchErrorNotification>
    {inline && noticeId && visible ? (
      <div className={cx('task-recovery-notice')} aria-label="当前会话恢复操作">
        {children}
      </div>
    ) : null}
  </>
}
