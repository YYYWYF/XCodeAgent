import { createContext, useContext } from 'react'
import type { UncommittedChangesStore } from '../hooks/useUncommittedChangesStore'

export const UncommittedChangesContext = createContext<UncommittedChangesStore | null>(null)

/** 读取常驻的未提交变更状态；缺失 Provider 时立即报告接线错误。 */
export function useUncommittedChanges(): UncommittedChangesStore {
  const context = useContext(UncommittedChangesContext)
  if (!context) {
    throw new Error('useUncommittedChanges must be used within UncommittedChangesProvider')
  }
  return context
}
