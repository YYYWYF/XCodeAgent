import type { ProcessStepRecord, ToolCallRecord } from '../../service/agUiAgent'
import type { EditorMode, WorkflowRunPayload, WorkspaceCodeChangeSet } from '../../typings'

export type AgentChatMessage = {
  id: number
  role: 'user' | 'assistant'
  content: string
  workflow?: WorkflowRunPayload
  codeChanges?: WorkspaceCodeChangeSet
  toolCalls?: ToolCallRecord[]
  processSteps?: ProcessStepRecord[]
  createdAt: number
}

export type RightPanelState =
  | { type: 'preview' }
  | {
      type: 'diff'
      codeChanges: WorkspaceCodeChangeSet
      selectedPath?: string
    }

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; empty: string; placeholder: string; label: string }
>
