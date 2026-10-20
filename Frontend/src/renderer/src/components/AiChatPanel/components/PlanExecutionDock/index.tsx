import {
  CheckCircleOutlined,
  ExclamationCircleOutlined,
  LoadingOutlined,
  LockOutlined
} from '@ant-design/icons'
import { Button, Modal, Popconfirm, Typography } from 'antd'
import { useEffect, useState } from 'react'
import type { ReactElement } from 'react'
import type { TestEntryGate, WorkbenchExecution } from '../../../../typings'
import { cx } from '../../../../utils'
import { testEntryGateReason } from '../../../../developmentArtifacts'
import type { PlanExecutionMode } from '../../planExecutionMode'
import { planExecutionPhaseLabel } from '../../planExecutionMode'
import './PlanExecutionDock.less'

const { Text } = Typography

type Props = {
  ending?: boolean
  dependencyLocked?: boolean
  execution?: WorkbenchExecution
  developmentTotals?: { completed: number; total: number }
  mode: Exclude<PlanExecutionMode, 'idle'>
  ownerPageId?: string
  testEntryGate?: TestEntryGate
  onAccept: () => Promise<boolean>
  onConfirmInteraction: (decision: 'reject' | 'once' | 'always') => void
  onEnd: () => void | Promise<void>
  onOpenPreview: () => void
}

/** 仅替换工作区最底部输入区，承载计划锁定说明和必要控制动作。 */
export default function PlanExecutionDock({
  ending = false,
  dependencyLocked = false,
  execution,
  developmentTotals,
  mode,
  ownerPageId,
  testEntryGate,
  onAccept,
  onConfirmInteraction,
  onEnd,
  onOpenPreview
}: Props): ReactElement {
  const [acceptanceConfirmOpen, setAcceptanceConfirmOpen] = useState(false)
  const [accepting, setAccepting] = useState(false)
  const pending = execution?.pendingInteraction
  // 阶段确认属于当前 execution，进入测试的资格属于整个应用；两者需同时成立。
  const developmentIncomplete =
    developmentTotals !== undefined &&
    (developmentTotals.total === 0 || developmentTotals.completed !== developmentTotals.total)
  const developmentGateBlocked =
    (mode === 'awaiting_test_phase_confirmation' ||
      mode === 'awaiting_review_phase_confirmation' ||
      mode === 'awaiting_acceptance_phase_confirmation') &&
    (developmentIncomplete || testEntryGate?.allowed !== true)
  const developmentGateReason =
    developmentIncomplete && developmentTotals
      ? `完成全部开发产物后可进入测试，当前 ${developmentTotals.completed}/${developmentTotals.total}。`
      : testEntryGateReason(testEntryGate)

  useEffect(() => {
    setAcceptanceConfirmOpen(false)
    setAccepting(false)
  }, [mode, pending?.id])

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
      aria-busy={ending || mode === 'stopping'}
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
          <Text strong>
            {ending ? '正在结束任务…' : dependencyLocked
              ? '该页面被关联计划锁定'
              : developmentGateBlocked
                ? '继续完成开发产物'
                : planModeTitle(mode)}
          </Text>
          <Text type="secondary">
            {ending ? '正在等待后台 Agent、工具和命令退出，请稍候。' : dependencyLocked
              ? dependencyLockDescription(ownerPageId, execution?.phase)
              : developmentGateBlocked
                ? developmentGateReason
                : planModeDescription(
                    mode,
                    execution?.phase,
                    pending?.payload
                  )}
          </Text>
        </div>
        {ending ? <Button loading disabled>正在结束</Button> : !dependencyLocked && (
          <div className={cx('plan-execution-dock-actions')}>
            {mode === 'awaiting_authorization' && (
              <>
                <Button onClick={() => onConfirmInteraction('reject')}>拒绝</Button>
                <Button onClick={() => onConfirmInteraction('once')}>仅本次允许</Button>
                <Button onClick={() => onConfirmInteraction('always')} type="primary">
                  始终允许
                </Button>
              </>
            )}
            {mode === 'awaiting_repair_confirmation' && (
              <>
                <Button onClick={() => onConfirmInteraction('reject')}>拒绝</Button>
                <Button onClick={() => onConfirmInteraction('once')} type="primary">
                  确认并重试失败任务
                </Button>
              </>
            )}
            {mode === 'awaiting_unit_test_confirmation' && (
              <div className={cx('plan-execution-dock-interaction')}>
                请在上方确认卡选择执行单元测试或不执行单元测试。
              </div>
            )}
            {mode === 'awaiting_frontend_performance_confirmation' && (
              <div className={cx('plan-execution-dock-interaction')}>
                构建检查已完成，请在上方选择执行或跳过前端性能测试。
              </div>
            )}
            {developmentGateBlocked ? (
              <div className={cx('plan-execution-dock-interaction')}>
                {developmentTotals
                  ? `开发进度 ${developmentTotals.completed}/${developmentTotals.total}`
                  : testEntryGate
                    ? `开发进度 ${testEntryGate.completed}/${testEntryGate.total}`
                    : '正在读取开发产物状态'}
              </div>
            ) : mode === 'awaiting_test_phase_confirmation' ? (
              <div className={cx('plan-execution-dock-interaction')}>
                开发已完成，请在上方确认进入测试阶段。
              </div>
            ) : null}
            {!developmentGateBlocked && mode === 'awaiting_review_phase_confirmation' && (
              <div className={cx('plan-execution-dock-interaction')}>
                请在上方选择审查范围，确认进入审查阶段。
              </div>
            )}
            {!developmentGateBlocked && mode === 'awaiting_acceptance_phase_confirmation' && (
              <div className={cx('plan-execution-dock-interaction')}>
                代码审查已完成，请在上方确认进入验收阶段。
              </div>
            )}
            {mode === 'awaiting_code_review_repair_confirmation' && (
              <div className={cx('plan-execution-dock-interaction')}>
                代码审查发现问题，请在上方执行一键修复。
              </div>
            )}
            {mode === 'awaiting_acceptance' && (
              <>
                <Button onClick={onOpenPreview}>全屏预览</Button>
                <Popconfirm
                  cancelText="取消"
                  okText="结束并重新描述"
                  onConfirm={onEnd}
                  title="结束当前执行后，请在统一输入框直接描述希望修改的结果。"
                >
                  <Button>提出修改</Button>
                </Popconfirm>
                <Button
                  icon={<CheckCircleOutlined />}
                  onClick={() => setAcceptanceConfirmOpen(true)}
                  type="primary"
                >
                  验收通过并完成
                </Button>
              </>
            )}
            <Popconfirm cancelText="取消" okButtonProps={{ danger: true }} okText="结束任务"
              onConfirm={onEnd} title="结束后将停止所有相关执行；工作区未变化时可继续任务。">
              <Button danger type="text">结束任务</Button>
            </Popconfirm>
          </div>
        )}
      </div>

      {ending || mode === 'stopping' ? (
        <div
          aria-label="正在等待后端结束任务"
          aria-valuetext="正在等待所有相关执行退出"
          className={cx('plan-execution-dock-loading')}
          role="progressbar"
        >
          <span />
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
    running: '计划正在执行',
    stopping: '正在等待后台退出…',
    awaiting_authorization: '等待操作授权',
    awaiting_repair_confirmation: 'RepairPlanner 需要你的确认',
    awaiting_unit_test_confirmation: '等待单元测试选择',
    awaiting_frontend_performance_confirmation: '等待前端性能测试选择',
    awaiting_test_phase_confirmation: '等待进入测试阶段',
    awaiting_review_phase_confirmation: '等待进入审查阶段',
    awaiting_acceptance_phase_confirmation: '等待进入验收阶段',
    awaiting_code_review_repair_confirmation: '等待一键修复代码问题',
    awaiting_acceptance: '页面已准备好，等待最终验收',
    awaiting_plan_adjustment: '等待调整计划',
    failed: '计划执行失败',
    stopped: '当前执行已停止'
  }[mode]
}

/** 组合当前阶段与结构化交互摘要，避免在底部复制完整日志。 */
function planModeDescription(
  mode: Exclude<PlanExecutionMode, 'idle'>,
  phase?: string,
  payload?: Record<string, unknown>
): string {
  if (mode === 'failed') {
    return '可以在上方重试，或结束当前计划。'
  }
  if (mode === 'awaiting_repair_confirmation') {
    return String(payload?.reason || payload?.message || '修复范围发生变化，确认后继续。')
  }
  if (mode === 'awaiting_test_phase_confirmation') {
    return '开发已完成，请在上方确认进入测试阶段。'
  }
  if (mode === 'awaiting_review_phase_confirmation') {
    return String(payload?.message || '请在上方确认进入审查阶段。')
  }
  if (mode === 'awaiting_acceptance_phase_confirmation') {
    return '代码审查已完成，请在上方确认进入验收阶段。'
  }
  if (mode === 'awaiting_code_review_repair_confirmation') {
    return '代码审查发现问题，请在上方执行一键修复。'
  }
  if (mode === 'awaiting_unit_test_confirmation') {
    return '请在上方确认卡选择执行单元测试或不执行单元测试。'
  }
  if (mode === 'awaiting_frontend_performance_confirmation') {
    return '请在上方确认卡选择继续执行或跳过前端性能测试。'
  }
  if (mode === 'awaiting_authorization') {
    return String(payload?.message || 'Agent 请求执行受保护操作。')
  }
  if (mode === 'stopping') {
    return '正在等待后台工作退出；也可以结束当前任务。'
  }
  if (mode === 'awaiting_acceptance') return '请检查右侧预览，验收通过后才会完成计划。'
  if (mode === 'awaiting_plan_adjustment') return '可在上方输入计划调整意见或结束当前计划。'
  return `当前任务：${planExecutionPhaseLabel(phase)}`
}
