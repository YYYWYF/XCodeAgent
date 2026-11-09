import { useEffect, useRef, useState } from 'react'
import { cancelUiDesignGeneration, getUiDesignGeneration } from '../service/uiDesignCancellation'

/** 停止期间保持占用，只有精确生成代次的后台退出回执才触发本地收尾。 */
export function useUiDesignCancellation(workspaceRoot: string | undefined, onStopped: (pageId: string) => void,
  stopGeneration = cancelUiDesignGeneration, getGeneration = getUiDesignGeneration) {
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

  /** 查询后台真实任务后停止，提交中的任务只等待登记，不误认上一轮终态。 */
  const cancel = async (pageId: string): Promise<void> => {
    if (!workspaceRoot || pending.current.has(pageId)) return
    const token = epoch.current
    pending.current.add(pageId)
    setStopping([...pending.current])
    setError('')
    try {
      const deadline = Date.now() + 10_000
      while (token === epoch.current) {
        const current = await getGeneration({ workspaceRoot, pageId })
        if (token !== epoch.current) return
        if (current.taskState !== 'preparing') {
          await stopGeneration({ workspaceRoot, pageId, stopToken: current.stopToken })
          if (token === epoch.current) callback.current(pageId)
          return
        }
        if (Date.now() >= deadline) {
          throw new Error('生成动作仍在提交，尚未停止，请稍后重试。')
        }
        await new Promise<void>((resolve) => setTimeout(resolve, 200))
      }
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
