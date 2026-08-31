import type { ProcessStepRecord, ToolCallRecord } from '../../service/agUiAgent'
import type { ChatSessionRevisionHandoff } from '../../service/chatSessions'
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
  /** 当前 assistant 轮次的模型或 Workflow 错误，统一交给错误卡片渲染。 */
  error?: string
  codeChanges?: WorkspaceCodeChangeSet
  toolCalls?: ToolCallRecord[]
  processSteps?: ProcessStepRecord[]
  /** 来源会话中的正式二次修改跳转回执。 */
  revisionHandoff?: ChatSessionRevisionHandoff
  createdAt: number
  /** 设计阶段规划占位标记：用户提交操作后追加的 assistant 占位消息，
   *  流式 chunk 到达前显示 loading 态；chunk 到达后清除。 */
  planningLoading?: boolean
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
  | { type: 'test-report' }
  | { type: 'review-report' }
  | { type: 'source' }
  | { type: 'process' }
  | {
      type: 'stage-output'
      sessionKey: string
      view?: 'stage' | 'confirmation'
      stageId?: string
    }

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; placeholder: string; label: string }
>
