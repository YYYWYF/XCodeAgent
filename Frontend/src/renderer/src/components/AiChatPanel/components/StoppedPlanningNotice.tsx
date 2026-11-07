import { Typography } from 'antd'
import { useState, type ReactElement } from 'react'
import { cx } from '../../../utils'
import { getApplicationLifecycle } from '../../../service/applicationLifecycle'
import type { ApplicationConfig, ApplicationLifecycle } from '../../../typings'
import { useStoppedPlanningAvailability } from '../hooks/useStoppedPlanningAvailability'
import EndedTaskNotice from './NextDevelopmentGuide/EndedTaskNotice'

/** 复用既有停止卡与 Runtime 恢复操作；原历史消息和确认卡仍然只读。 */
export default function StoppedPlanningNotice({ application, stopped, onLifecycle, disabled, error, onContinue, onRestart }: {
  application: ApplicationConfig
  stopped: NonNullable<ApplicationLifecycle['endedPlanningExecution']>
  onLifecycle: (lifecycle: ApplicationLifecycle) => void
  disabled: boolean
  error?: string
  onContinue: () => void | Promise<void>
  onRestart: () => void
}): ReactElement {
  const [continuing, setContinuing] = useState(false)
  const availability = useStoppedPlanningAvailability({
    scopeKey: JSON.stringify([application.id, application.workspaceRoot]),
    runId: stopped.runId, threadId: stopped.threadId,
    read: () => getApplicationLifecycle(application), onLifecycle
  })
  /** 等待原恢复链路完成，防止用户连续提交同一个继续动作。 */
  const handleContinue = async (): Promise<void> => {
    if (continuing || disabled || availability.unchanged !== true) return
    setContinuing(true)
    try { if (await availability.check()) await onContinue() } finally { setContinuing(false) }
  }
  return <div className={cx('ai-chat-composer')}>
    <div className={cx('ai-chat-composer-column')}>
      <EndedTaskNotice mode="stopped_generation" unchanged={availability.unchanged}
        checkingWorkspace={availability.unchanged === undefined} continuing={continuing}
        disabled={disabled} onContinue={handleContinue} onRestart={onRestart} />
      {availability.error ? <Typography.Text type="danger">{availability.error}</Typography.Text> : null}
      {error ? <Typography.Text type="danger">{error}</Typography.Text> : null}
    </div>
  </div>
}
