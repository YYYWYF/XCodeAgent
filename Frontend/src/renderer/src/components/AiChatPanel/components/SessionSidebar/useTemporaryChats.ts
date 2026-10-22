import { useCallback, useEffect, useRef, useState } from 'react'
import { randomUUID } from '@ag-ui/client'
import { emitConversationCreated } from '../../../../service/telemetry'
import {
  createTemporaryChatThreadId,
  sendTemporaryChatQuestion,
  type TemporaryChatMessage
} from '../../../../service/temporaryChat'

export type TemporaryChatSession = {
  id: string
  number: number
  messages: TemporaryChatMessage[]
  isRunning: boolean
}

/** 管理仅驻留在当前 Renderer 内存中的临时对话及请求。 */
export function useTemporaryChats(workspaceRoot: string, projectId: string): {
  sessions: TemporaryChatSession[]
  createSession: () => string
  deleteSession: (sessionId: string) => void
  sendMessage: (sessionId: string, request: string) => Promise<void>
} {
  const [sessions, setSessions] = useState<TemporaryChatSession[]>([])
  const sessionsRef = useRef(sessions)
  const nextNumberRef = useRef(1)
  const controllersRef = useRef(new Map<string, AbortController>())
  const workspaceRootRef = useRef(workspaceRoot)

  /** 同步更新状态引用，供同一事件循环内后续请求读取最新消息。 */
  const commitSessions = useCallback(
    (update: (current: TemporaryChatSession[]) => TemporaryChatSession[]): void => {
      const next = update(sessionsRef.current)
      sessionsRef.current = next
      setSessions(next)
    },
    []
  )

  /** 更新一条临时会话，并保留列表中其他会话的状态。 */
  const updateSession = useCallback(
    (sessionId: string, update: (session: TemporaryChatSession) => TemporaryChatSession): void => {
      commitSessions((current) =>
        current.map((session) => (session.id === sessionId ? update(session) : session))
      )
    },
    [commitSessions]
  )

  /** 新建一个带独立 AG-UI Thread ID 的临时对话。 */
  const createSession = useCallback((): string => {
    const id = createTemporaryChatThreadId()
    const session: TemporaryChatSession = {
      id,
      number: nextNumberRef.current,
      messages: [],
      isRunning: false
    }
    nextNumberRef.current += 1
    commitSessions((current) => [session, ...current])
    // 业务功能埋点：新建临时对话
    emitConversationCreated({
      projectId,
      conversationId: id,
      threadId: id,
      conversationType: 'temporary'
    })
    return id
  }, [commitSessions, projectId])

  /** 关闭临时对话时取消其在途请求并清除全部内存消息。 */
  const deleteSession = useCallback(
    (sessionId: string): void => {
      controllersRef.current.get(sessionId)?.abort()
      controllersRef.current.delete(sessionId)
      commitSessions((current) => current.filter((session) => session.id !== sessionId))
    },
    [commitSessions]
  )

  /** 发送只读问题，并将回答或错误追加到当前临时会话。 */
  const sendMessage = useCallback(
    async (sessionId: string, request: string): Promise<void> => {
      const session = sessionsRef.current.find((candidate) => candidate.id === sessionId)
      if (!session || session.isRunning || !workspaceRoot || !request.trim()) return
      const conversationSummary = temporaryConversationSummary(session.messages)
      const controller = new AbortController()
      controllersRef.current.set(sessionId, controller)
      updateSession(sessionId, (current) => ({
        ...current,
        isRunning: true,
        messages: [
          ...current.messages,
          { id: randomUUID(), role: 'user', content: request.trim() }
        ]
      }))
      try {
        const answer = await sendTemporaryChatQuestion({
          threadId: sessionId,
          workspaceRoot,
          request: request.trim(),
          conversationSummary,
          signal: controller.signal
        })
        if (sessionsRef.current.some((candidate) => candidate.id === sessionId)) {
          updateSession(sessionId, (current) => ({
            ...current,
            messages: [...current.messages, { id: randomUUID(), role: 'assistant', content: answer }]
          }))
        }
      } catch (error) {
        if (
          error instanceof Error &&
          error.name !== 'AbortError' &&
          sessionsRef.current.some((candidate) => candidate.id === sessionId)
        ) {
          updateSession(sessionId, (current) => ({
            ...current,
            messages: [
              ...current.messages,
              {
                id: randomUUID(),
                role: 'error',
                content: error.message || '临时对话请求失败。'
              }
            ]
          }))
        }
      } finally {
        if (controllersRef.current.get(sessionId) === controller) {
          controllersRef.current.delete(sessionId)
        }
        updateSession(sessionId, (current) => ({ ...current, isRunning: false }))
      }
    },
    [updateSession, workspaceRoot]
  )

  /** 工作区切换后清除旧工程上下文，避免临时会话跨项目复用。 */
  useEffect(() => {
    if (workspaceRootRef.current === workspaceRoot) return
    controllersRef.current.forEach((controller) => controller.abort())
    controllersRef.current.clear()
    workspaceRootRef.current = workspaceRoot
    nextNumberRef.current = 1
    commitSessions(() => [])
  }, [commitSessions, workspaceRoot])

  /** Renderer 卸载时取消所有临时请求，不保留后台会话。 */
  useEffect(
    () => () => {
      controllersRef.current.forEach((controller) => controller.abort())
      controllersRef.current.clear()
    },
    []
  )

  return { sessions, createSession, deleteSession, sendMessage }
}

/** 将最近的临时对话压缩为最多 4000 字符的短期上下文。 */
function temporaryConversationSummary(messages: TemporaryChatMessage[]): string {
  return messages
    .filter((message) => message.role !== 'error')
    .map((message) => `${message.role === 'user' ? '用户' : '助手'}：${message.content}`)
    .join('\n')
    .slice(-4_000)
}
