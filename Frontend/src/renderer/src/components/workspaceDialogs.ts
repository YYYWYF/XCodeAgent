import { Modal } from 'antd'
import type { ModalFuncProps } from 'antd'
import { cx } from '../utils'
import './workspaceDialogs.less'

/** 将确认弹窗挂到工作台主题容器，保证浅色与深色背景、正文和按钮一致。 */
export function confirmWorkspaceAction(options: ModalFuncProps): ReturnType<typeof Modal.confirm> {
  return Modal.confirm({ ...options, className: 'workspace-action-confirm',
    getContainer: () => document.querySelector<HTMLElement>(`.${cx('workbench-shell')}`) || document.body })
}
