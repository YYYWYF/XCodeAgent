import { useEffect, useRef, useState } from 'react'
import { cancelUiDesignGeneration } from '../service/uiDesignCancellation'

/** 停止期间保持占用，只有精确生成代次的后台退出回执才触发本地收尾。 */
export function useUiDesignCancellation(workspaceRoot: string | undefined, onStopped: (pageId: string) => void,
  stopGeneration = cancelUiDesignGeneration) {
  const [stopping, setStopping] = useState<string[]>([])
  const [error, setError] = useState('')
  const pending = useRef(new Set<string>())
  const epoch = useRef(0)
  const callback = useRef(onStopped)
  callback.current = onStopped
  useEffect(() => {
    epoch.current += 1
    pending.current.clear()
    setStopping([])
    setError('')
    return () => { epoch.current += 1 }
  }, [workspaceRoot])

  /** 从当前磁盘投影读取任务身份，网络失败或业务拒绝均保留原生成状态。 */
  const cancel = async (pageId: string): Promise<void> => {
    if (!workspaceRoot || pending.current.has(pageId)) return
    const token = epoch.current
    pending.current.add(pageId)
    setStopping([...pending.current])
    setError('')
    try {
      const latest = await window.devAgentStudio?.workspace?.readUiDesigns({ workspaceRoot })
      const pages = (latest?.uiDesigns as { pages?: Array<{ pageId?: string; generation_id?: string }> } | undefined)?.pages
      const generationId = pages?.find((page) => page.pageId === pageId)?.generation_id
      if (!generationId) throw new Error('尚未同步当前生成任务，请刷新后重试停止。')
      if (token !== epoch.current) return
      await stopGeneration({ workspaceRoot, pageId, generationId })
      if (token === epoch.current) callback.current(pageId)
    } catch (failure) {
      if (token === epoch.current) setError(failure instanceof Error ? failure.message : '停止设计稿生成失败。')
    } finally {
      if (token === epoch.current) {
        pending.current.delete(pageId)
        setStopping([...pending.current])
      }
    }
  }
  return { stopping, error, cancel }
}
