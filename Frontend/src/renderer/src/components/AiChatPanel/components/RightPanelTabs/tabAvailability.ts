import type { WorkspaceTabKey } from '.'

/** 判断右侧工作区 tab 是否可点击；预览面板始终允许进入，由面板自行呈现地址或空态。 */
export function workspaceTabIsAvailable(key: WorkspaceTabKey, contentAvailable: boolean): boolean {
  return key === 'preview' || contentAvailable
}
