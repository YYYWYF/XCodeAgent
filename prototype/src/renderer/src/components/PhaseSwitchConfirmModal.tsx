import { EditOutlined, RetweetOutlined, UndoOutlined } from '@ant-design/icons'
import { Button, Modal } from 'antd'
import type { ReactElement } from 'react'
import { cx } from '../utils'
import {
  WORKBENCH_PHASE_AGENTS,
  WORKBENCH_PHASE_ORDER,
  type WorkbenchPhase
} from '../workbenchPhase'
import './PhaseSwitchConfirmModal.less'

type Props = {
  open: boolean
  /** 旅程当前所在阶段（自动推导值）。 */
  fromPhase: WorkbenchPhase
  /** 用户要强制切回的阶段；null 时不渲染。 */
  toPhase: WorkbenchPhase | null
  onCancel: () => void
  onConfirm: (phase: WorkbenchPhase) => void
}

const POINTS = [
  {
    icon: <EditOutlined />,
    title: '可编辑上游产物',
    desc: '回到该阶段后，需求文档 / 项目计划等上游产物可重新调整。'
  },
  {
    icon: <RetweetOutlined />,
    title: 'scoped 增量重建',
    desc: '重新进入下游时只重算受影响对象，已完成的部分不会被推倒重来。'
  },
]

/**
 * 强制回退切阶段（增量迭代）的二次确认弹框。
 * 仅在切到旅程上游阶段时由 WorkbenchTopBar 弹出，向前推进不弹。
 */
export default function PhaseSwitchConfirmModal({
  open,
  fromPhase,
  toPhase,
  onCancel,
  onConfirm
}: Props): ReactElement {
  const toAgent = WORKBENCH_PHASE_AGENTS[toPhase ?? 'analysis']
  const fromAgent = WORKBENCH_PHASE_AGENTS[fromPhase]
  const returningUpstream =
    WORKBENCH_PHASE_ORDER.indexOf(toPhase ?? fromPhase) < WORKBENCH_PHASE_ORDER.indexOf(fromPhase)
  return (
    <Modal
      closable={false}
      footer={null}
      maskTransitionName=""
      open={open}
      transitionName=""
      width={540}
      wrapClassName={cx('phase-switch-confirm-modal')}
      onCancel={onCancel}
    >
      <div className={cx('phase-switch-confirm')}>
        <header className={cx('phase-switch-confirm-header')}>
          <span className={cx('phase-switch-confirm-icon')} aria-hidden="true">
            <UndoOutlined />
          </span>
          <span className={cx('phase-switch-confirm-title')}>
            <strong>切换到{toAgent.label}阶段？</strong>
            <small>{returningUpstream ? '这将返回上游继续调整' : '这将改变当前执行阶段'}</small>
          </span>
        </header>

        <div className={cx('phase-switch-confirm-body')}>
          <p className={cx('phase-switch-confirm-lead')}>
            当前应用在<strong>{fromAgent.label}</strong>阶段。确认后将进入<strong>{toAgent.label}</strong>阶段；查看其它阶段的历史任务请使用左侧任务目录。
          </p>
          <ul className={cx('phase-switch-confirm-points')}>
            {POINTS.map((point) => (
              <li key={point.title}>
                <span className={cx('phase-switch-confirm-point-icon')} aria-hidden="true">
                  {point.icon}
                </span>
                <span className={cx('phase-switch-confirm-point-copy')}>
                  <strong>{point.title}</strong>
                  <span>{point.desc}</span>
                </span>
              </li>
            ))}
          </ul>
        </div>

        <footer className={cx('phase-switch-confirm-footer')}>
          <Button onClick={onCancel}>取消</Button>
          <Button danger={returningUpstream} onClick={() => toPhase && onConfirm(toPhase)} type="primary">
            确认切换
          </Button>
        </footer>
      </div>
    </Modal>
  )
}
