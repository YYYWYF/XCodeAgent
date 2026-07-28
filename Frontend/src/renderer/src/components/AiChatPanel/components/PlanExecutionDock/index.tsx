import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  LockOutlined,
  PauseCircleOutlined,
  RedoOutlined
} from '@ant-design/icons'
import { Button, Input, Modal, Popconfirm, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { WorkbenchExecution } from '../../../../typings'
import { cx } from '../../../../utils'
import type { PlanExecutionMode } from '../../planExecutionMode'
import { planExecutionPhaseLabel } from '../../planExecutionMode'
import './PlanExecutionDock.less'

const { Text } = Typography
const { TextArea } = Input

type Props = {
  dependencyLocked?: boolean
  error?: string
  execution?: WorkbenchExecution
  mode: Exclude<PlanExecutionMode, 'idle'>
  ownerPageId?: string
  onAccept: () => Promise<boolean>
  onAdjust: (feedback: string) => void
  onConfirmInteraction: (decision: 'reject' | 'once' | 'always') => void
  onEnd: () => void
  onOpenPreview: () => void
  onRetry: () => void
  onStop: () => void
  onViewPlan: () => void
}

/** 仅替换工作区最底部输入区，承载计划锁定说明和必要控制动作。 */
export default function PlanExecutionDock({
  dependencyLocked = false,
  error,
  execution,
  mode,
  ownerPageId,
  onAccept,
  onAdjust,
  onConfirmInteraction,
  onEnd,
  onOpenPreview,
  onRetry,
  onStop,
  onViewPlan
}: Props): ReactElement {
  const [adjusting, setAdjusting] = useState(false)
  const [acceptanceConfirmOpen, setAcceptanceConfirmOpen] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const [feedback, setFeedback] = useState('')
  const pending = execution?.pendingInteraction

  useEffect(() => {
    setAdjusting(false)
    setAcceptanceConfirmOpen(false)
    setAccepting(false)
    setFeedback('')
  }, [mode, pending?.id])

  /** 提交受控计划调整意见，并保持空内容不可发送。 */
  const submitAdjustment = (): void => {
    const normalized = feedback.trim()
    if (!normalized) return
    onAdjust(normalized)
  }

  /** 确认最终验收并等待 AG-UI 成功接收，失败时保留对话框供用户重试。 */
  const confirmAcceptance = async (): Promise<void> => {
    if (accepting) return
    setAccepting(true)
    try {
      const succeeded = await onAccept()
      if (succeeded) setAcceptanceConfirmOpen(false)
    } finally {
      setAccepting(false)
    }
  }

  return (
    <section
      aria-busy={mode === 'stopping'}
      aria-live="polite"
      className={cx('plan-execution-dock', mode)}
    >
      <div className={cx('plan-execution-dock-main')}>
        <span className={cx('plan-execution-dock-icon')} aria-hidden="true">
          {mode === 'failed' ? (
            <ExclamationCircleOutlined />
          ) : mode === 'stopping' ? (
            <LoadingOutlined spin />
          ) : (
            <LockOutlined />
          )}
        </span>
        <div className={cx('plan-execution-dock-copy')}>
          <Text strong>{dependencyLocked ? '该页面被关联计划锁定' : planModeTitle(mode)}</Text>
          <Text type="secondary">
            {dependencyLocked
              ? dependencyLockDescription(ownerPageId, execution?.phase)
              : planModeDescription(mode, execution?.phase, pending?.payload, error)}
          </Text>
        </div>
        {!dependencyLocked && (
          <div className={cx('plan-execution-dock-actions')}>
            {(mode === 'running' || mode === 'stopping') && (
              <>
                <Button onClick={onViewPlan}>查看计划</Button>
                <Button
                  danger
                  icon={<PauseCircleOutlined />}
                  loading={mode === 'stopping'}
                  onClick={onStop}
                >
                  {mode === 'stopping' ? '正在暂停' : '暂停执行'}
                </Button>
              </>
            )}
            {mode === 'awaiting_authorization' && (
              <>
                <Button onClick={() => onConfirmInteraction('reject')}>拒绝</Button>
                <Button onClick={() => onConfirmInteraction('once')}>仅本次允许</Button>
                <Button onClick={() => onConfirmInteraction('always')} type="primary">
                  始终允许
                </Button>
                <Button danger onClick={onStop}>
                  暂停执行
                </Button>
              </>
            )}
            {mode === 'awaiting_repair_confirmation' && (
              <>
                <Button onClick={() => onConfirmInteraction('reject')}>拒绝</Button>
                <Button onClick={() => onConfirmInteraction('once')} type="primary">
                  确认修复范围
                </Button>
                <Button danger onClick={onStop}>
                  暂停执行
                </Button>
              </>
            )}
            {mode === 'awaiting_acceptance' && (
              <>
                <Button onClick={onOpenPreview}>全屏预览</Button>
                <Button onClick={() => setAdjusting(true)}>提出修改</Button>
                <Button
                  icon={<CheckCircleOutlined />}
                  onClick={() => setAcceptanceConfirmOpen(true)}
                  type="primary"
                >
                  验收通过并完成
                </Button>
              </>
            )}
            {(mode === 'failed' || mode === 'stopped') && (
              <>
                <Button icon={<RedoOutlined />} onClick={onRetry} type="primary">
                  {mode === 'failed' ? '重试失败任务' : '继续执行'}
                </Button>
                <Button onClick={() => setAdjusting(true)}>调整计划</Button>
                <Popconfirm
                  cancelText="取消"
                  okButtonProps={{ danger: true }}
                  okText="结束计划"
                  onConfirm={onEnd}
                  title="结束后将释放工作区并恢复自由输入，确定结束吗？"
                >
                  <Button danger type="text">
                    结束
                  </Button>
                </Popconfirm>
              </>
            )}
            {mode === 'awaiting_plan_adjustment' && (
              <Popconfirm
                cancelText="取消"
                okButtonProps={{ danger: true }}
                okText="结束计划"
                onConfirm={onEnd}
                title="确定结束当前计划吗？"
              >
                <Button danger type="text">
                  结束
                </Button>
              </Popconfirm>
            )}
          </div>
        )}
      </div>

      {mode === 'stopping' ? (
        <div
          aria-label="正在等待后端暂停计划执行"
          aria-valuetext="正在安全暂停并保存进度"
          className={cx('plan-execution-dock-loading')}
          role="progressbar"
        >
          <span />
        </div>
      ) : null}

      {!dependencyLocked && adjusting ? (
        <div className={cx('plan-execution-adjustment')}>
          <TextArea
            aria-label="计划调整意见"
            autoSize={{ minRows: 2, maxRows: 4 }}
            placeholder="说明需要修改的内容，提交后会重新生成执行任务…"
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
          <div>
            <Button onClick={() => setAdjusting(false)}>取消</Button>
            <Button disabled={!feedback.trim()} onClick={submitAdjustment} type="primary">
              提交调整
            </Button>
          </div>
        </div>
      ) : null}

      <Modal
        cancelButtonProps={{ disabled: accepting }}
        cancelText="取消"
        centered
        closable={!accepting}
        confirmLoading={accepting}
        keyboard={!accepting}
        maskClosable={!accepting}
        okText="确认验收并完成"
        onCancel={() => setAcceptanceConfirmOpen(false)}
        onOk={() => void confirmAcceptance()}
        open={acceptanceConfirmOpen}
        title="确认验收并完成？"
      >
        <Text>确认后当前计划将完成并恢复自由输入，请确保已经检查页面预览。</Text>
      </Modal>
    </section>
  )
}

/** 说明关联页面为何只读，并指出真正拥有控制权的页面执行。 */
function dependencyLockDescription(ownerPageId?: string, phase?: string): string {
  const owner = ownerPageId ? `页面 ${ownerPageId}` : '应用级计划'
  return `${owner} 正在使用该页面的共享资源；当前任务：${planExecutionPhaseLabel(phase)}`
}

/** 返回计划控制栏当前状态的主标题。 */
function planModeTitle(mode: Exclude<PlanExecutionMode, 'idle'>): string {
  return {
    running: '计划执行期间已暂停自由输入',
    stopping: '正在暂停计划执行…',
    awaiting_authorization: '执行已暂停，等待授权',
    awaiting_repair_confirmation: 'RepairPlanner 需要你的确认',
    awaiting_acceptance: '页面已准备好，等待最终验收',
    awaiting_plan_adjustment: '执行已暂停',
    failed: '计划执行失败',
    stopped: '计划执行已暂停'
  }[mode]
}

/** 组合当前阶段与结构化交互摘要，避免在底部复制完整日志。 */
function planModeDescription(
  mode: Exclude<PlanExecutionMode, 'idle'>,
  phase?: string,
  payload?: Record<string, unknown>,
  error?: string
): string {
  if (mode === 'failed') return error || '可以重试失败任务、调整计划或结束。'
  if (mode === 'awaiting_repair_confirmation') {
    return String(payload?.reason || payload?.message || '修复范围发生变化，确认后继续。')
  }
  if (mode === 'awaiting_authorization') {
    return String(payload?.message || 'Agent 请求执行受保护操作。')
  }
  if (mode === 'stopping') {
    return '后端正在安全结束当前任务并保存可恢复进度，请稍候；完成后会显示继续执行、调整计划和结束操作。'
  }
  if (mode === 'awaiting_acceptance') return '请检查右侧预览，验收通过后才会完成计划。'
  if (mode === 'awaiting_plan_adjustment') return '可在上方输入计划调整意见或结束当前计划。'
  return `当前任务：${planExecutionPhaseLabel(phase)}`
}
