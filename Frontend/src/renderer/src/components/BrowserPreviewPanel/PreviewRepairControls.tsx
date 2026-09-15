import { Alert, Button, Input, Space } from 'antd'
import { useState } from 'react'
import type { ReactElement } from 'react'
import type { PreviewAction, PreviewRepair } from '../../service/previewRuntime'

/** 在修复历史会话内提供明确的计划确认、修订和停止动作。 */
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
  const [feedback, setFeedback] = useState('')
  const waiting = repair?.status === 'awaiting_confirmation'
  const terminal = ['completed', 'stopped', 'failed', 'requires_revision'].includes(
    repair?.status || ''
  )
  return (
    <div className="preview-repair-controls">
      <Space direction="vertical" style={{ width: '100%' }}>
        <Alert
          type={repair?.status === 'failed' ? 'error' : 'info'}
          message={
            busy
              ? '诊断修复执行中；进度显示在当前对话。'
              : repair?.message || '正在读取当前修复会话…'
          }
        />
        {repair?.status === 'requires_revision' ? (
          <Button onClick={() => onRevision(repair.message || '调整正式方案以解决预览启动问题')}>
            进入正式修订
          </Button>
        ) : (
          <>
            <Input.TextArea
              value={feedback}
              onChange={(event) => setFeedback(event.target.value)}
              disabled={busy || terminal}
              placeholder="补充诊断信息或提出修复计划调整；提交不会自动确认计划。"
              autoSize={{ minRows: 2, maxRows: 4 }}
            />
            <Space wrap>
              <Button
                type="primary"
                disabled={busy || !waiting}
                onClick={() => void onAction('confirm')}
              >
                确认并修复
              </Button>
              <Button
                disabled={busy || !waiting}
                onClick={() => {
                  void onAction('revise', feedback)
                  setFeedback('')
                }}
              >
                重新诊断
              </Button>
              <Button disabled={!busy && !waiting} onClick={() => void onAction('cancel')}>
                停止修复
              </Button>
            </Space>
          </>
        )}
      </Space>
    </div>
  )
}
