import type { SessionHistoryPage } from '../../../../../shared/sessionHistory'
import { useRef, useState } from 'react'
import { readChatSession } from '../../../service/chatSessions'
import type { SessionIdentity } from './sessionRuntime'
import { useSessionRuntimeStore } from './useSessionRuntimeStore'

/** 历史展示分页只合并读盘数据，不拥有后台任务、会话身份或保存生命周期。 */
export function useSessionHistory(activeSession?: SessionIdentity): {
  history?: SessionHistoryPage
  loadingHistory: boolean
  historyError?: string
  loadOlderMessages: () => Promise<void>
} {
  const { sessionHistory, prependSessionHistory, retainSession } = useSessionRuntimeStore()
  const activeKey = activeSession?.key
  const history = activeKey ? sessionHistory(activeKey) : undefined
  const [load, setLoad] = useState<{ key?: string; loading: boolean; error?: string }>({
    loading: false
  })
  const requests = useRef(new Set<string>())
  const selected = useRef(activeKey)
  selected.current = activeKey

  /** 按会话隔离上滚请求；迟到回执不切换会话或覆盖流式新回复。 */
  const loadOlderMessages = async (): Promise<void> => {
    const identity = activeSession
    const before = history?.before
    if (!identity || before === undefined || requests.current.has(identity.key)) return
    requests.current.add(identity.key)
    const release = retainSession(identity.key)
    setLoad({ key: identity.key, loading: true })
    try {
      const page = await readChatSession(
        identity.workspaceRoot,
        identity.editorMode,
        identity.sessionId,
        before
      )
      if (selected.current === identity.key && page.history) {
        prependSessionHistory(identity.key, page.messages, page.history)
      }
      setLoad((current) =>
        current.key === identity.key ? { key: identity.key, loading: false } : current
      )
    } catch (error) {
      setLoad((current) =>
        current.key === identity.key
          ? {
              key: identity.key,
              loading: false,
              error: error instanceof Error ? error.message : '历史加载失败，请重试。'
            }
          : current
      )
    } finally {
      requests.current.delete(identity.key)
      release()
    }
  }

  return {
    history,
    loadingHistory: load.key === activeKey && load.loading,
    historyError: load.key === activeKey ? load.error : undefined,
    loadOlderMessages
  }
}
