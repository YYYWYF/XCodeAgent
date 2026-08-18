import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  LockOutlined,
  PauseCircleOutlined,
  RedoOutlined
} from '@ant-design/icons'
import { Button, Input, Modal, Popconfirm, Select, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkbenchExecution,
  WorkflowAcceptanceAdjustmentType
} from '../../../../typings'
import { cx } from '../../../../utils'
import type { PlanExecutionMode } from '../../planExecutionMode'
import { planExecutionPhaseLabel } from '../../planExecutionMode'
import './PlanExecutionDock.less'

const { Text } = Typography
const { TextArea } = Input

type Props = {
  canRetryFailedTasks?: boolean
  dependencyLocked?: boolean
  error?: string
  execution?: WorkbenchExecution
  mode: Exclude<PlanExecutionMode, 'idle'>
  ownerPageId?: string
  onAccept: () => Promise<boolean>
  onAdjust: (
    feedback: string,
    adjustmentType: WorkflowAcceptanceAdjustmentType
  ) => Promise<boolean>
  onConfirmInteraction: (decision: 'reject' | 'once' | 'always') => void
  onEnd: () => void
  onOpenPreview: () => void
  onRetry: () => void
  onStop: () => void
  onViewPlan: () => void
}

const ACCEPTANCE_ADJUSTMENT_OPTIONS: Array<{
  label: string
  value: WorkflowAcceptanceAdjustmentType
}> = [
  { label: '局部修复（不改变产品语义）', value: 'local_fix' },
  { label: '接口行为或字段调整', value: 'endpoint_change' },
  { label: '数据来源或数据库调整', value: 'data_source_change' },
  { label: '项目计划或架构调整', value: 'project_plan_change' }
]

/** 仅替换工作区最底部输入区，承载计划锁定说明和必要控制动作。 */
export default function PlanExecutionDock({
  canRetryFailedTasks = false,
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
  const [submittingAdjustment, setSubmittingAdjustment] = useState(false)
  const [feedback, setFeedback] = useState('')
  const [adjustmentType, setAdjustmentType] =
    useState<WorkflowAcceptanceAdjustmentType>('local_fix')
  const pending = execution?.pendingInteraction

  useEffect(() => {
    setAdjusting(false)
    setAcceptanceConfirmOpen(false)
    setAccepting(false)
    setSubmittingAdjustment(false)
    setFeedback('')
    setAdjustmentType('local_fix')
  }, [mode, pending?.id])

  /** 提交受控计划调整意见，成功后关闭编辑框，失败时保留内容供重试。 */
  const submitAdjustment = async (): Promise<void> => {
    const normalized = feedback.trim()
    if (!normalized || submittingAdjustment) return
    setSubmittingAdjustment(true)
    try {
      const succeeded = await onAdjust(normalized, adjustmentType)
      if (succeeded) setAdjusting(false)
    } finally {
      setSubmittingAdjustment(false)
    }
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
              : planModeDescription(
                  mode,
                  execution?.phase,
                  pending?.payload,
                  error,
                  canRetryFailedTasks
                )}
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
                {(mode === 'stopped' || (mode === 'failed' && canRetryFailedTasks)) && (
                  <Button icon={<RedoOutlined />} onClick={onRetry} type="primary">
                    {mode === 'failed' ? '重试失败任务' : '继续执行'}
                  </Button>
                )}
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

      <Modal
        cancelButtonProps={{ disabled: submittingAdjustment }}
        cancelText="取消"
        centered
        closable={!submittingAdjustment}
        confirmLoading={submittingAdjustment}
        destroyOnClose
        keyboard={!submittingAdjustment}
        maskClosable={!submittingAdjustment}
        okButtonProps={{ disabled: !feedback.trim() }}
        okText="提交修改"
        onCancel={() => setAdjusting(false)}
        onOk={() => void submitAdjustment()}
        open={!dependencyLocked && adjusting}
        title="提出验收修改"
        wrapClassName={cx('plan-execution-modal')}
      >
        <div className={cx('plan-execution-adjustment-form')}>
          <Text type="secondary">
            选择修改范围并说明具体问题。提交后会回到对应工作流节点，修改完成后需要重新验收。
          </Text>
          <div className={cx('plan-execution-adjustment-type')}>
            <Text strong>修改类型</Text>
            <Select<WorkflowAcceptanceAdjustmentType>
              aria-label="验收调整类型"
              disabled={submittingAdjustment}
              onChange={setAdjustmentType}
              options={ACCEPTANCE_ADJUSTMENT_OPTIONS}
              value={adjustmentType}
            />
          </div>
          <TextArea
            aria-label="验收修改意见"
            autoFocus
            autoSize={{ minRows: 4, maxRows: 8 }}
            disabled={submittingAdjustment}
            maxLength={4000}
            placeholder="请说明哪里不符合预期、希望改成什么样，以及必要的验收条件…"
            showCount
            value={feedback}
            onChange={(event) => setFeedback(event.target.value)}
          />
        </div>
      </Modal>

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
        wrapClassName={cx('plan-execution-modal')}
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
  error?: string,
  canRetryFailedTasks = false
): string {
  if (mode === 'failed') {
    return (
      error ||
      (canRetryFailedTasks
        ? '存在可恢复的失败任务或待执行修复任务。'
        : '当前失败需要调整计划、确认修复范围或结束。')
    )
  }
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
