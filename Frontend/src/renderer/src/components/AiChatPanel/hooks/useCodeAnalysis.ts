import { useRef, useState } from 'react'
import { startFrontendCodeAnalysis } from '../../../service/codeAnalysis'
import type { CodeAnalysisResult } from '../../../typings'
import type { AgentChatMessage } from '../types'
import type { PersistSessionInput } from './useChatSessions'
import type { SessionIdentity } from './sessionRuntime'

type Params = {
  disabled: boolean
  ensureActiveSession: () => Promise<SessionIdentity>
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  setSessionMessages: (sessionKey: string, messages: AgentChatMessage[]) => void
  workspaceRoot: string
}

type ActiveRun = {
  abort: () => void
  assistantMessageId: number
  identity: SessionIdentity
  latest?: CodeAnalysisResult
  cancelled?: boolean
}

/** 管理代码审查的消息生命周期、取消和历史持久化。 */
export function useCodeAnalysis({
  disabled,
  ensureActiveSession,
  getSessionMessages,
  persistSession,
  setSessionMessages,
  workspaceRoot
}: Params): {
  running: boolean
  stopping: boolean
  start: () => Promise<void>
  stop: () => void
} {
  const activeRunRef = useRef<ActiveRun>()
  const startingRef = useRef(false)
  const [running, setRunning] = useState(false)
  const [stopping, setStopping] = useState(false)

  /** 更新指定助手消息中的代码审查卡片状态。 */
  const updateMessage = (
    run: ActiveRun,
    codeAnalysis: CodeAnalysisResult,
    content = ''
  ): AgentChatMessage[] => {
    const nextMessages = getSessionMessages(run.identity.key).map((message) =>
      message.id === run.assistantMessageId ? { ...message, content, codeAnalysis } : message
    )
    setSessionMessages(run.identity.key, nextMessages)
    return nextMessages
  }

  /** 保存扫描结束后的轻量卡片元数据，不持久化 Markdown 正文。 */
  const persistFinalMessages = async (
    run: ActiveRun,
    messages: AgentChatMessage[]
  ): Promise<void> => {
    await persistSession({
      editorMode: run.identity.editorMode,
      messages,
      sessionId: run.identity.sessionId,
      threadId: run.identity.threadId,
      apiContractId: run.identity.apiContractId,
      endpointId: run.identity.endpointId,
      endpointLabel: run.identity.endpointLabel,
      pageId: run.identity.pageId,
      titleFrom: '扫描当前工作区前端代码'
    })
  }

  /** 创建本地消息并启动独立代码审查 AG-UI 运行。 */
  const start = async (): Promise<void> => {
    if (disabled || running || startingRef.current || activeRunRef.current || !workspaceRoot) return
    startingRef.current = true
    let identity: SessionIdentity
    try {
      identity = await ensureActiveSession()
    } catch {
      startingRef.current = false
      return
    }
    const now = Date.now()
    const initial: CodeAnalysisResult = {
      schemaVersion: 1,
      runId: '',
      threadId: '',
      status: 'in_progress',
      action: 'scan',
      progress: { stage: 'validating_workspace', message: '正在校验工作区', percent: 0 }
    }
    const userMessage: AgentChatMessage = {
      id: now,
      role: 'user',
      content: '扫描当前工作区前端代码',
      createdAt: now
    }
    const assistantMessage: AgentChatMessage = {
      id: now + 1,
      role: 'assistant',
      content: '',
      codeAnalysis: initial,
      createdAt: now + 1
    }
    const initialMessages = [...getSessionMessages(identity.key), userMessage, assistantMessage]
    setSessionMessages(identity.key, initialMessages)
    setRunning(true)
    setStopping(false)
    const request = startFrontendCodeAnalysis(workspaceRoot, {
      onUpdate: (result) => {
        const current = activeRunRef.current
        if (!current || current.assistantMessageId !== assistantMessage.id) return
        current.latest = result
        updateMessage(current, result)
      }
    })
    const run: ActiveRun = {
      abort: request.abort,
      assistantMessageId: assistantMessage.id,
      identity,
      latest: initial
    }
    activeRunRef.current = run
    startingRef.current = false

    try {
      const result = await request.promise
      const completedMessages = updateMessage(run, result)
      await persistFinalMessages(run, completedMessages)
    } catch (caughtError) {
      const cancelled = Boolean(run.cancelled)
      const latest = run.latest || initial
      const failed: CodeAnalysisResult = {
        ...latest,
        status: cancelled ? 'cancelled' : 'failed',
        error: {
          type: cancelled ? 'Cancelled' : 'CodeAnalysisError',
          message: cancelled
            ? '用户已停止扫描。'
            : caughtError instanceof Error
              ? caughtError.message
              : '前端代码扫描失败。'
        }
      }
      const failedMessages = updateMessage(run, failed)
      await persistFinalMessages(run, failedMessages).catch(() => undefined)
    } finally {
      if (activeRunRef.current === run) activeRunRef.current = undefined
      startingRef.current = false
      setRunning(false)
      setStopping(false)
    }
  }

  /** 中止当前代码扫描；后端会通过断流取消仍在运行的 Agent。 */
  const stop = (): void => {
    const run = activeRunRef.current
    if (!run || stopping) return
    setStopping(true)
    run.cancelled = true
    run.abort()
  }

  return { running, stopping, start, stop }
}
