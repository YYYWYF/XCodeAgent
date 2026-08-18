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
  createdAt: number
  /** 设计阶段规划占位标记：用户提交操作后追加的 assistant 占位消息，
   *  流式 chunk 到达前显示 loading 态；chunk 到达后清除。 */
  planningLoading?: boolean
  /** 待设计目标挡板：该消息以交互卡形式渲染（目标信息 + 开始详细设计），
   *  作为对话历史保留，点开始后不回退消失。 */
  detailBlocker?: {
    pageId: string
    label: string
    path?: string
    purpose?: string
  }
}

/** 设计阶段右侧「文档」的产物 key，作为工作区 tab 使用。 */
export type WorkspaceDocKey =
  | 'requirement-spec'
  | 'product-plan'
  | 'technical-plan'
  | 'build-task-plan'
  | 'ui-design'

export type RightPanelState =
  | { type: 'preview'; requestKey?: string; url?: string }
  | {
      type: 'diff'
      codeChanges: WorkspaceCodeChangeSet
      selectedPath?: string
    }
  | { type: 'doc'; docKey?: WorkspaceDocKey }
  | { type: 'source' }
  | { type: 'process' }

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; placeholder: string; label: string }
>
