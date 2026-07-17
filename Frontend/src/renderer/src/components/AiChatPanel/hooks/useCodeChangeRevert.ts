import { message as antdMessage, Modal } from 'antd'
import { useState } from 'react'
import { revertWorkspaceCodeChanges } from '../../../service/codeChanges'
import type { WorkspaceCodeChangeSet } from '../../../typings'
import { cx } from '../../../utils'
import type { AgentChatMessage, RightPanelState } from '../types'
import type { PersistSessionInput } from './useChatSessions'
import type { SessionIdentity } from './sessionRuntime'
import './useCodeChangeRevert.less'

type UseCodeChangeRevertParams = {
  activeSession?: SessionIdentity
  disabled: boolean
  getSessionMessages: (sessionKey: string) => AgentChatMessage[]
  persistSession: (input: PersistSessionInput) => Promise<void>
  rightPanel?: RightPanelState
  setRightPanel: (panel?: RightPanelState) => void
  setSessionMessages: (sessionKey: string, messages: AgentChatMessage[]) => void
}

type UseCodeChangeRevertResult = {
  requestCodeChangeRevert: (messageId: number, codeChanges: WorkspaceCodeChangeSet) => void
  revertingCodeChangeIds: ReadonlySet<string>
}

/** 管理历史代码变更的确认、AG-UI 撤销和会话状态持久化。 */
export function useCodeChangeRevert({
  activeSession,
  disabled,
  getSessionMessages,
  persistSession,
  rightPanel,
  setRightPanel,
  setSessionMessages
}: UseCodeChangeRevertParams): UseCodeChangeRevertResult {
  const [revertingCodeChangeIds, setRevertingCodeChangeIds] = useState<Set<string>>(() => new Set())

  /** 执行已确认的代码变更撤销，并把撤销状态写回当前历史会话。 */
  const executeCodeChangeRevert = async (
    messageId: number,
    codeChanges: WorkspaceCodeChangeSet
  ): Promise<void> => {
    if (!activeSession || disabled || revertingCodeChangeIds.has(codeChanges.id)) return
    setRevertingCodeChangeIds((current) => new Set(current).add(codeChanges.id))

    try {
      const result = await revertWorkspaceCodeChanges(codeChanges)
      const revertedCodeChanges: WorkspaceCodeChangeSet = {
        ...codeChanges,
        status: 'reverted',
        revertedAt: result.revertedAt
      }
      const nextMessages = getSessionMessages(activeSession.key).map((message) =>
        message.id === messageId ? { ...message, codeChanges: revertedCodeChanges } : message
      )
      setSessionMessages(activeSession.key, nextMessages)
      if (rightPanel?.type === 'diff' && rightPanel.codeChanges.id === codeChanges.id) {
        setRightPanel({ ...rightPanel, codeChanges: revertedCodeChanges })
      }

      try {
        await persistSession({
          editorMode: activeSession.editorMode,
          messages: nextMessages,
          sessionId: activeSession.sessionId,
          threadId: activeSession.threadId
        })
        antdMessage.success(`已撤销本次对 ${result.revertedPaths.length} 个文件的修改`)
      } catch (caughtError) {
        antdMessage.warning(
          caughtError instanceof Error
            ? `文件已撤销，但历史状态保存失败：${caughtError.message}`
            : '文件已撤销，但历史状态保存失败。'
        )
      }
    } catch (caughtError) {
      antdMessage.error(caughtError instanceof Error ? caughtError.message : '撤销代码变更失败。')
    } finally {
      setRevertingCodeChangeIds((current) => {
        const next = new Set(current)
        next.delete(codeChanges.id)
        return next
      })
    }
  }

  /** 弹出安全确认框，确认后仅撤销指定历史变更集。 */
  const requestCodeChangeRevert = (
    messageId: number,
    codeChanges: WorkspaceCodeChangeSet
  ): void => {
    if (disabled || revertingCodeChangeIds.has(codeChanges.id)) return
    Modal.confirm({
      centered: true,
      className: cx('code-change-revert-confirm'),
      title: '确认撤销本次修改？',
      content: `将通过 Git 仅撤销本次涉及的 ${codeChanges.summary.files} 个文件修改；发生冲突时不会修改任何文件。`,
      okText: '确认撤销',
      cancelText: '取消',
      onOk: () => executeCodeChangeRevert(messageId, codeChanges)
    })
  }

  return { requestCodeChangeRevert, revertingCodeChangeIds }
}
