import type { ToolCallRecord } from '../../service/agUiAgent'
import type { EditorMode, WorkflowRunPayload } from '../../typings'

export type AgentChatMessage = {
  id: number
  role: 'user' | 'assistant'
  content: string
  workflow?: WorkflowRunPayload
  toolCalls?: ToolCallRecord[]
  createdAt: number
}

export type RightPanelState = { type: 'preview' }

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; empty: string; placeholder: string; label: string }
>
