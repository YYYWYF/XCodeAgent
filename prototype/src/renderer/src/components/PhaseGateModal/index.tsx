import { Button, Modal } from 'antd'
import type { ReactNode } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../utils'
import './index.less'

type PhaseGateModalProps = {
  open: boolean
  /** 头部渐变区图标，例如各门禁的语义图标。 */
  icon: ReactNode
  /** 主标题，例如「进入开发阶段？」。 */
  title: string
  /** 标题下的一行副标题，概括本次门禁动作。 */
  subtitle?: string
  /** 正文导语；正文还通过 children 追加门禁专属内容（选项组、要点列表等）。 */
  lead?: ReactNode
  children?: ReactNode
  /** 主确认按钮文案；向前阶段门禁统一为「进入下一阶段」，阶段信息由标题承载。 */
  confirmText: string
  /** 条件未满足时禁用主确认，例如任务类型未选择。 */
  confirmDisabled?: boolean
  /** 回退上游等有风险动作使用红色确认按钮。 */
  confirmDanger?: boolean
  /** 取消按钮文案；向前门禁统一用「暂不进入」，缺省为「取消」。 */
  cancelText?: string
  onCancel: () => void
  onConfirm: () => void
}

/**
 * 阶段门禁弹框的唯一外壳：所有进入/切换阶段的确认弹框复用同一结构
 * （渐变头部 + 导语正文 + 右对齐双按钮），保证门禁交互风格一致。
 * 弹框本身无内置动画依赖，文案与正文完全由调用方提供。
 */
export default function PhaseGateModal({
  open,
  icon,
  title,
  subtitle,
  lead,
  children,
  confirmText,
  confirmDisabled = false,
  confirmDanger = false,
  cancelText = '取消',
  onCancel,
  onConfirm
}: PhaseGateModalProps): ReactElement {
  return (
    <Modal
      centered
      closable={false}
      footer={null}
      getContainer={false}
      maskClosable={false}
      maskTransitionName=""
      onCancel={onCancel}
      open={open}
      transitionName=""
      width={540}
      wrapClassName={cx('phase-gate-modal')}
    >
      <div className={cx('phase-gate')}>
        <header className={cx('phase-gate-header')}>
          <span className={cx('phase-gate-icon')} aria-hidden="true">
            {icon}
          </span>
          <span className={cx('phase-gate-title')}>
            <strong>{title}</strong>
            {subtitle ? <small>{subtitle}</small> : null}
          </span>
        </header>

        <div className={cx('phase-gate-body')}>
          {lead ? <p className={cx('phase-gate-lead')}>{lead}</p> : null}
          {children}
        </div>

        <footer className={cx('phase-gate-footer')}>
          <Button onClick={onCancel}>{cancelText}</Button>
          <Button
            danger={confirmDanger}
            disabled={confirmDisabled}
            onClick={onConfirm}
            type="primary"
          >
            {confirmText}
          </Button>
        </footer>
      </div>
    </Modal>
  )
}
