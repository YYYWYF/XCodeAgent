import { useEffect, type Dispatch, type SetStateAction } from 'react'

type FileNavigation = {
  workspaceRoot: string
  revision?: string
  requestedFilePath?: string
  treeReady: boolean
  onSelect: (path: string) => void
  loadDirectory: (path: string) => Promise<void>
  setExpanded: Dispatch<SetStateAction<Set<string>>>
}

/** 显式文件跳转立即读取目标，目录就绪后逐层展开；工作区或目标变化时停止旧定位。 */
export function useSourceFileNavigation({ workspaceRoot, revision, requestedFilePath, treeReady,
  onSelect, loadDirectory, setExpanded }: FileNavigation): void {
  useEffect(() => {
    if (workspaceRoot && requestedFilePath) onSelect(requestedFilePath)
  }, [workspaceRoot, revision, requestedFilePath, onSelect])

  useEffect(() => {
    if (!workspaceRoot || !requestedFilePath || !treeReady) return
    let cancelled = false
    const segments = requestedFilePath.split('/').slice(0, -1)
    const ancestors = ['.', ...segments.map((_, index) => segments.slice(0, index + 1).join('/'))]
    // 仅补读目标的祖先目录，避免深层文件不在初始快照中时退回默认文档。
    void (async () => {
      setExpanded((current) => new Set([...current, ...ancestors]))
      for (const path of ancestors.slice(1)) {
        if (cancelled) return
        await loadDirectory(path)
      }
    })()
    return () => { cancelled = true }
  }, [workspaceRoot, revision, requestedFilePath, treeReady, loadDirectory, setExpanded])
}
