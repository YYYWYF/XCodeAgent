import type { ReactNode } from 'react'
import { useUncommittedChangesStore } from '../hooks/useUncommittedChangesStore'
import { UncommittedChangesContext } from './uncommittedChangesState'

/**
 * 在工作台根部持有唯一的未提交变更状态。
 *
 * 挂在 WorkbenchPage 而不是更高层：角标（顶部栏）与各档提交提醒（对话区）都在它下面，
 * 一处覆盖全部；离开工作台的场景（返回首页、显式退出）需要的是**当场重新读取**，
 * 不该复用这份常驻快照，所以它们各自现读、不走这里。
 */
export function UncommittedChangesProvider({
  workspaceRoot,
  refreshKey,
  children
}: {
  workspaceRoot: string
  /** 见 useUncommittedChangesStore：内容变化信号，用于构建推进时重读。 */
  refreshKey?: string
  children: ReactNode
}): JSX.Element {
  const store = useUncommittedChangesStore(workspaceRoot, refreshKey)
  return (
    <UncommittedChangesContext.Provider value={store}>
      {children}
    </UncommittedChangesContext.Provider>
  )
}
