import { Modal } from 'antd'
import type { ModalFuncProps } from 'antd'
import './workspaceDialogs.less'

/** 将确认弹窗挂到根节点并置于编辑弹窗上方，沿用全局桥接的浅色与深色主题变量。 */
export function confirmWorkspaceAction(options: ModalFuncProps): ReturnType<typeof Modal.confirm> {
  // 编辑弹窗挂在 body，确认框必须使用同一层级，避免被编辑弹窗的遮罩拦截点击。
  return Modal.confirm({ zIndex: 1100, ...options, className: 'workspace-action-confirm',
    getContainer: () => document.body })
}
