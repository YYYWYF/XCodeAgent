import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { message, Modal } from 'antd'
import { inspectPendingCodeCount } from '../service/versionControl'

type ReturnScope = { applicationKey: string; workspace: string; enabled: boolean }
type ReturnRequest = { scope: ReturnScope; dialog?: ReturnType<typeof Modal.confirm> }

/** 持有返回前的轻量检查与确认，隔离重复点击及应用切换后的迟到结果。 */
export function useReturnWelcome({
  applicationKey,
  workspace,
  enabled,
  hasActiveExecution,
  onLeave
}: {
  applicationKey: string
  workspace: string
  enabled: boolean
  hasActiveExecution: boolean
  onLeave: () => void
}): { returningWelcome: boolean; handleReturnWelcome: () => Promise<void> } {
  // 每次进入都有独立身份，A → B → A 也不能采纳第一次进入时的迟到响应。
  const scope = useMemo(
    () => ({ applicationKey, workspace, enabled }),
    [applicationKey, workspace, enabled]
  )
  const scopeRef = useRef(scope)
  scopeRef.current = scope
  const requestRef = useRef<ReturnRequest>()
  const [pendingScope, setPendingScope] = useState<ReturnScope>()

  useEffect(() => {
    return () => {
      const request = requestRef.current
      if (request?.scope !== scope) return
      requestRef.current = undefined
      request.dialog?.destroy()
    }
  }, [scope])

  /** 同一导航只读一次 Git；确认框关闭前同步锁持续生效。 */
  const handleReturnWelcome = useCallback(async (): Promise<void> => {
    if (!enabled || requestRef.current?.scope === scope) return
    const request: ReturnRequest = { scope }
    requestRef.current = request
    setPendingScope(scope)
    /** 请求与可见应用身份同时匹配时才允许产生导航或弹框。 */
    const isCurrent = (): boolean => scopeRef.current === scope && requestRef.current === request
    /** 仅释放本次请求，避免旧确认框清掉新应用的导航锁。 */
    const release = (): void => {
      if (!isCurrent()) return
      requestRef.current = undefined
      setPendingScope(undefined)
    }
    /** 校验身份后收口导航锁并返回首页。 */
    const leave = (): void => {
      if (!isCurrent()) return
      release()
      onLeave()
    }
    if (!workspace || hasActiveExecution) {
      if (hasActiveExecution) message.info('当前有任务正在执行，返回首页后会在后台继续。')
      leave()
      return
    }
    try {
      const pending = await inspectPendingCodeCount(workspace)
      if (!isCurrent()) return
      if (pending === 0) {
        leave()
        return
      }
      request.dialog = Modal.confirm({
        centered: true,
        title: '仍有未提交的代码变更',
        content: `当前工作区有 ${pending} 个文件未提交。返回首页不会丢失代码，但建议先提交形成可追溯版本。`,
        okText: '仍然返回',
        cancelText: '取消',
        onOk: leave,
        onCancel: release
      })
    } catch {
      // Git 提醒读取失败时沿用原有返回行为，避免辅助检查阻断导航。
      leave()
    }
  }, [enabled, scope, workspace, hasActiveExecution, onLeave])

  return { returningWelcome: pendingScope === scope, handleReturnWelcome }
}
