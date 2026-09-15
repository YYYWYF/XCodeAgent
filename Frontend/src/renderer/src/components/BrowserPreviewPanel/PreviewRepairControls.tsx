import { Button } from 'antd'
import type { ReactElement } from 'react'
import type { PreviewAction, PreviewRepair } from '../../service/previewRuntime'
import './PreviewRepairControls.less'

/** 在修复历史会话底部提供计划确认和立即停止动作。 */
export default function PreviewRepairControls({
  repair,
  busy,
  onAction,
  onRevision
}: {
  repair?: PreviewRepair
  busy: boolean
  onAction: (action: PreviewAction, feedback?: string) => Promise<void>
  onRevision: (message: string) => void
}): ReactElement {
  const waiting = repair?.status === 'awaiting_confirmation'
  const terminal = ['completed', 'stopped', 'failed', 'requires_revision'].includes(
    repair?.status || ''
  )
  const stopping = repair?.status === 'stopping'
  const requiresRevision = repair?.status === 'requires_revision'
  const canConfirm = waiting && !busy
  const canStop = !terminal && !stopping && (busy || waiting)
  return (
    <div className="preview-repair-controls">
      <div
        aria-label={requiresRevision ? '正式修订操作' : '预览修复操作'}
        className="preview-repair-controls__floating"
        role="group"
      >
        {requiresRevision ? (
          <Button
            className="preview-repair-controls__revision"
            onClick={() => onRevision(repair?.message || '调整正式方案以解决预览启动问题')}
          >
            进入正式修订
          </Button>
        ) : (
          <>
            <Button
              className="preview-repair-controls__confirm"
              disabled={!canConfirm}
              type="primary"
              onClick={() => void onAction('confirm')}
            >
              确认并修复
            </Button>
            <Button
              className="preview-repair-controls__stop"
              disabled={!canStop}
              onClick={() => void onAction('cancel')}
            >
              {stopping ? '正在停止…' : '停止修复'}
            </Button>
          </>
        )}
      </div>
    </div>
  )
}
