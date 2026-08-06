import type { ProcessStepRecord, ToolCallRecord } from '../../service/agUiAgent'
import type {
  ChatMessageSkill,
  EditorMode,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'

export type AgentChatMessage = {
  id: number
  role: 'user' | 'assistant'
  content: string
  skills?: ChatMessageSkill[]
  workflow?: WorkflowRunPayload
  codeChanges?: WorkspaceCodeChangeSet
  toolCalls?: ToolCallRecord[]
  processSteps?: ProcessStepRecord[]
  /** 待设计目标挡板：该消息以交互卡形式渲染（目标信息 + 模板选择 + 开始详细设计），
   * 作为对话历史保留，点开始后不回退消失。 */
  detailBlocker?: {
    pageId: string
    label: string
    path?: string
    purpose?: string
  }
  createdAt: number
}

/** 设计阶段右侧「文档」的产物 key，作为工作区 tab 使用。 */
export type WorkspaceDocKey = 'requirement-spec' | 'project-plan' | 'build-task-plan'

export type RightPanelState =
  | { type: 'preview'; requestKey?: string; url?: string }
  | {
      type: 'diff'
      codeChanges: WorkspaceCodeChangeSet
      selectedPath?: string
    }
  | { type: 'process' }
  | { type: 'doc'; docKey?: WorkspaceDocKey }
  | { type: 'source' }

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; empty: string; placeholder: string; label: string }
>
