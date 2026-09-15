import { useCallback, useEffect, useRef, useState } from 'react'
import { randomUUID } from '@ag-ui/client'
import { runPreviewRuntime } from '../../../service/previewRuntime'
import type { PreviewAction, PreviewRuntimePayload } from '../../../service/previewRuntime'
import type { AgentChatMessage } from '../types'
import type { SessionIdentity } from './sessionRuntime'
import type { PersistSessionInput } from './useChatSessions'
import type { ServiceStatusControl } from '../../BrowserPreviewPanel/ServiceStatusDrawer'
import { useSessionRuntimeStore } from './useSessionRuntimeStore'

type Options = {
  workspace: string
  activeSession?: SessionIdentity
  localBlocked: boolean
  createSession: () => Promise<SessionIdentity>
  discardSession: (identity: SessionIdentity) => Promise<void>
  persistSession: (input: PersistSessionInput) => Promise<void>
  setMessages: (key: string, messages: AgentChatMessage[]) => void
  getMessages: (key: string) => AgentChatMessage[]
  openTask: (threadId?: string, runId?: string) => void
  onReady: (url: string) => void
}

/** 在工作台层持有服务操作，使关闭抽屉或切换历史对话不会中止修复。 */
export function usePreviewRuntime(options: Options): {
  control: ServiceStatusControl
  repairSession: boolean
  repairState?: PreviewRuntimePayload['repair']
  repairBusy: boolean
  act: (action: PreviewAction, feedback?: string) => Promise<void>
} {
  const { acquireSessionExecution, releaseSessionExecution, updateSessionExecutionStatus } =
    useSessionRuntimeStore()
  const runtimeStoreRef = useRef({
    acquireSessionExecution,
    releaseSessionExecution,
    updateSessionExecutionStatus
  })
  runtimeStoreRef.current = {
    acquireSessionExecution,
    releaseSessionExecution,
    updateSessionExecutionStatus
  }
  const [open, setOpen] = useState(false)
  const [snapshot, setSnapshot] = useState<PreviewRuntimePayload>()
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [repairs, setRepairs] = useState<Record<string, PreviewRuntimePayload['repair']>>({})
  const busyRef = useRef(false)
  const optionsRef = useRef(options)
  optionsRef.current = options
  const lastReadyUrlRef = useRef('')
  const repairSession = !!options.activeSession?.entryKey?.startsWith('preview-repair:')
  const activeThread = repairSession ? options.activeSession?.threadId : undefined
  const repairRunning =
    !!activeThread && ['running', 'stopping'].includes(repairs[activeThread]?.status || '')
  const workspaceRef = useRef(options.workspace)
  workspaceRef.current = options.workspace

  /** 合并当前工作区运行事实并按会话保存修复状态。 */
  const receive = useCallback((value: PreviewRuntimePayload, threadId?: string): void => {
    const { acquireSessionExecution, releaseSessionExecution, updateSessionExecutionStatus } =
      runtimeStoreRef.current
    setSnapshot((previous) => ({
      ...previous,
      ...value,
      runtime: value.runtime || previous?.runtime
    }))
    const readyUrl =
      value.runtime?.status === 'running'
        ? value.runtime.previewUrl || value.runtime.frontend.url || ''
        : ''
    // 服务状态订阅也可能首次发现预览已经恢复；只按 URL 变化同步一次，
    // 清除旧启动错误并避免每轮 watch 重新加载用户正在查看的页面。
    if (readyUrl && readyUrl !== lastReadyUrlRef.current) {
      lastReadyUrlRef.current = readyUrl
      optionsRef.current.onReady(readyUrl)
    } else if (value.runtime?.status === 'failed') {
      lastReadyUrlRef.current = ''
    }
    if (threadId && value.repair) {
      setRepairs((previous) => ({ ...previous, [threadId]: value.repair }))
      const identity = optionsRef.current.activeSession
      if (identity?.threadId === threadId) {
        if (['awaiting_confirmation', 'running', 'stopping'].includes(value.repair.status || '')) {
          acquireSessionExecution(identity, true)
          updateSessionExecutionStatus(
            identity.key,
            value.repair.status === 'stopping' ? 'stopping' : 'running'
          )
        } else if (value.repair.status) releaseSessionExecution(identity.key)
        // 断线后读到服务端当前计划时，将其补回同一历史会话，不创建新会话。
        const content = [value.repair.markdown, value.repair.message].filter(Boolean).join('\n\n')
        const current = optionsRef.current.getMessages(identity.key)
        if (
          !busyRef.current &&
          content &&
          !current.some(
            (message) => message.role === 'assistant' && message.content.includes(content)
          )
        ) {
          const now = Date.now()
          const messages: AgentChatMessage[] = [
            ...current,
            { id: now, createdAt: now, role: 'assistant', content }
          ]
          optionsRef.current.setMessages(identity.key, messages)
          void optionsRef.current
            .persistSession({ ...identity, messages })
            .catch((reason) => setError(String(reason)))
        }
      }
    }
  }, [])
  useEffect(() => {
    setSnapshot(undefined)
    setError('')
    setRepairs({})
    lastReadyUrlRef.current = ''
  }, [options.workspace])
  useEffect(() => {
    if (!options.workspace || (!open && !activeThread)) return
    const controller = new AbortController()
    const workspace = options.workspace
    /** 抽屉打开时连续订阅增量快照；关闭后取消读取。 */
    const watch = async (): Promise<void> => {
      try {
        do {
          await runPreviewRuntime(
            { workspace, action: open || repairRunning ? 'watch' : 'get', includeLogs: open },
            {
              threadId: activeThread,
              signal: controller.signal,
              onUpdate: (value) => {
                if (!controller.signal.aborted) receive(value, activeThread)
              }
            }
          )
        } while ((open || repairRunning) && !controller.signal.aborted)
      } catch (reason) {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : '读取服务状态失败')
      }
    }
    void watch()
    return () => controller.abort()
  }, [options.workspace, open, activeThread, repairRunning, receive])

  /** 把本轮进度和结果保存到既有历史对话记录。 */
  const execute = async (
    action: PreviewAction,
    identity?: SessionIdentity,
    feedback = ''
  ): Promise<void> => {
    if (busyRef.current && action !== 'cancel') return
    const captured = optionsRef.current
    const workspace = captured.workspace
    const cancelling = action === 'cancel'
    if (!cancelling) {
      busyRef.current = true
      setBusy(true)
    }
    setError('')
    const previous = identity ? captured.getMessages(identity.key) : []
    const now = Date.now()
    const user: AgentChatMessage = {
      id: now,
      role: 'user',
      content:
        feedback ||
        {
          diagnose: '诊断并修复预览服务启动失败。',
          confirm: '确认修复计划并执行。',
          revise: '重新诊断并生成修复计划。',
          cancel: '停止诊断修复。'
        }[action] ||
        '重启服务',
      createdAt: now
    }
    let assistant: AgentChatMessage = {
      id: now + 1,
      role: 'assistant',
      content: '正在处理…',
      createdAt: now + 1
    }
    let accepted = false
    let awaitingConfirmation = false
    let lastProgress = ''
    let changeMessages: AgentChatMessage[] = []
    let pendingSave = Promise.resolve()
    let lastSave = 0
    /** 更新当前轮消息，不覆盖另一会话的内容。 */
    const updateMessages = (): void => {
      if (identity)
        captured.setMessages(identity.key, [...previous, user, assistant, ...changeMessages])
    }
    if (!cancelling) updateMessages()
    try {
      if (identity && !cancelling) {
        const blocker = acquireSessionExecution(identity, true)
        if (blocker && blocker.identity.key !== identity.key)
          throw new Error('当前开发阶段已有执行任务。')
        updateSessionExecutionStatus(identity.key, 'running')
      }
      const value = await runPreviewRuntime(
        {
          workspace,
          action,
          feedback,
          attemptId: snapshot?.runtime?.attemptId,
          planId: identity ? repairs[identity.threadId]?.planId : undefined
        },
        {
          threadId: identity?.threadId || randomUUID(),
          onUpdate: (value) => {
            if (workspaceRef.current !== workspace) return
            receive(value, identity?.threadId)
            if (value.status !== 'failed') accepted = true
            const progress = value.progress?.message
            if (progress && progress !== lastProgress && !cancelling) {
              lastProgress = progress
              assistant = {
                ...assistant,
                content:
                  assistant.content === '正在处理…'
                    ? progress
                    : `${assistant.content}\n\n${progress}`
              }
              updateMessages()
              if (identity && Date.now() - lastSave >= 1000) {
                lastSave = Date.now()
                const messages = [...previous, user, assistant]
                pendingSave = pendingSave
                  .then(() => captured.persistSession({ ...identity, messages }))
                  .catch((reason) => setError(String(reason)))
              }
            }
          }
        }
      )
      const repair = value.repair
      awaitingConfirmation = repair?.status === 'awaiting_confirmation'
      const oldChangeIds = new Set(previous.map((message) => message.codeChanges?.id))
      const newChanges =
        repair?.codeChangeSets?.filter((change) => !oldChangeIds.has(change.id)) || []
      changeMessages = newChanges.slice(1).map((change, index) => ({
        id: now + index + 2,
        createdAt: now + index + 2,
        role: 'assistant',
        content: '本轮修复的代码变更。',
        codeChanges: change
      }))
      assistant = {
        ...assistant,
        content: [
          assistant.content,
          repair?.markdown,
          repair?.message || value.launchResult?.message
        ]
          .filter(Boolean)
          .join('\n\n'),
        codeChanges: newChanges[0]
      }
      if (
        value.runtime?.status === 'running' &&
        value.runtime.previewUrl &&
        ['restart', 'confirm'].includes(action) &&
        workspaceRef.current === workspace
      )
        captured.onReady(value.runtime.previewUrl)
    } catch (reason) {
      const message = reason instanceof Error ? reason.message : '预览服务操作失败'
      setError(message)
      assistant = { ...assistant, content: message, error: message }
      if (identity && action === 'diagnose' && !accepted) {
        releaseSessionExecution(identity.key)
        captured.setMessages(identity.key, [])
        await captured.discardSession(identity)
        return
      }
    } finally {
      if (!cancelling) {
        busyRef.current = false
        setBusy(false)
        if (identity && !awaitingConfirmation) releaseSessionExecution(identity.key)
      }
    }
    if (identity && !cancelling) {
      updateMessages()
      await pendingSave
      await captured.persistSession({
        ...identity,
        messages: [...previous, user, assistant, ...changeMessages]
      })
    }
  }
  /** 新建开发阶段修复历史会话，所有确认继续沿用该身份。 */
  const diagnose = async (): Promise<void> => {
    if (busyRef.current) return
    busyRef.current = true
    try {
      const identity = await optionsRef.current.createSession()
      busyRef.current = false
      await execute('diagnose', identity)
    } catch (reason) {
      busyRef.current = false
      setError(reason instanceof Error ? reason.message : '创建修复会话失败')
    }
  }
  const blockedReason = options.localBlocked
    ? '当前应用有任务执行中或等待确认，请完成或明确停止后再操作。'
    : snapshot?.blockedBy?.message ||
      (snapshot?.runtime?.maintenance ? '当前应用有预览维护任务，请完成或停止后再操作。' : '')
  return {
    control: {
      open,
      setOpen,
      snapshot,
      busy,
      error,
      blockedReason,
      onRestart: () => {
        void execute('restart')
      },
      onDiagnose: () => {
        void diagnose()
      },
      onOpenTask: blockedReason
        ? () =>
            options.openTask(
              snapshot?.blockedBy?.threadId || snapshot?.runtime?.maintenance?.threadId,
              snapshot?.blockedBy?.runId
            )
        : undefined
    },
    repairSession,
    repairState: activeThread ? repairs[activeThread] : undefined,
    repairBusy: busy || repairRunning,
    act: (action, feedback) => execute(action, options.activeSession, feedback)
  }
}
