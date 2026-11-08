import { useCallback, useContext, useEffect, useRef, useState } from 'react'
import { UiReadVisibility } from '../../../hooks/uiReadVisibility'
import type { ApplicationLifecycle } from '../../../typings'

type Input = {
  scopeKey: string
  runId: string
  threadId: string
  read: () => Promise<ApplicationLifecycle>
  onLifecycle: (lifecycle: ApplicationLifecycle) => void
}

type Availability = { key: string; checking: boolean; unchanged?: boolean; error?: string }

/** 仅在停止卡可见时校准当前工作区资格，继续前复验；不发起或恢复任何业务执行。 */
export function useStoppedPlanningAvailability(input: Input): {
  unchanged?: boolean
  checking: boolean
  error?: string
  check: () => Promise<boolean>
} {
  const visible = useContext(UiReadVisibility)
  const key = JSON.stringify([input.scopeKey, input.runId, input.threadId])
  const [availability, setAvailability] = useState<Availability>({ key, checking: true })
  const inputRef = useRef(input)
  inputRef.current = input
  const checkRef = useRef<(() => Promise<boolean>) | undefined>(undefined)

  useEffect(() => {
    if (!visible) return
    let cancelled = false
    let pending: Promise<boolean> | undefined
    /** 串行读取已有 AG-UI lifecycle；隐藏、换任务和卸载后的回执均不得回写。 */
    const refresh = (): Promise<boolean> => {
      if (cancelled || document.hidden) return Promise.resolve(false)
      if (pending) return pending
      // 检查失败与检查中分别投影；重读期间保留错误入口的加载态，但撤下旧继续资格。
      setAvailability((previous) => ({ key, checking: true,
        error: previous.key === key ? previous.error : undefined }))
      pending = (async (): Promise<boolean> => {
        try {
          const lifecycle = await inputRef.current.read()
          if (cancelled || document.hidden) return false
          const stopped = lifecycle.endedPlanningExecution
          const unchanged = stopped?.runId === input.runId && stopped.threadId === input.threadId &&
            stopped.workspaceUnchanged === true
          setAvailability({ key, checking: false, unchanged })
          inputRef.current.onLifecycle(lifecycle)
          return unchanged
        } catch {
          if (!cancelled && !document.hidden) {
            setAvailability({ key, checking: false, error: '暂时无法检查工作区状态，请重新检查。' })
          }
          return false
        } finally {
          pending = undefined
        }
      })()
      return pending
    }
    /** 返回窗口时立即复核，空闲期间不定时读取工作区。 */
    const handleVisibility = (): void => {
      if (!document.hidden) void refresh()
    }
    /** 点击不能复用点击前已在途的扫描；等待它退出后重新读取，仍保持串行。 */
    const checkCurrent = async (): Promise<boolean> => {
      if (pending) await pending
      return refresh()
    }
    checkRef.current = checkCurrent
    window.addEventListener('focus', handleVisibility)
    document.addEventListener('visibilitychange', handleVisibility)
    void refresh()
    return () => {
      cancelled = true
      if (checkRef.current === checkCurrent) checkRef.current = undefined
      window.removeEventListener('focus', handleVisibility)
      document.removeEventListener('visibilitychange', handleVisibility)
    }
  }, [key, visible])

  /** 点击继续只使用本次新读取，不能把界面上一次的未变化结果当作授权。 */
  const check = useCallback((): Promise<boolean> => checkRef.current?.() || Promise.resolve(false), [])
  const current = visible && availability.key === key ? availability : undefined
  return { unchanged: current?.unchanged, checking: visible && (!current || current.checking),
    error: current?.error, check }
}
