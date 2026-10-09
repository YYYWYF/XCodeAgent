import type { ProcessStepRecord, ToolCallRecord } from '../../service/agUiAgent'
import type {
  ChatSessionDevelopmentContinuation,
  ChatSessionRevisionHandoff
} from '../../service/chatSessions'
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
  /** 实体设计完成后在同一会话中恢复原页面或 Endpoint 正式任务的操作卡。 */
  developmentContinuation?: ChatSessionDevelopmentContinuation
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
  | { type: 'field-mapping' }
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
  | { type: 'outline' }
  | { type: 'process' }
  | {
      type: 'stage-output'
      sessionKey: string
      view?: 'stage' | 'confirmation'
    }

/**
 * 右侧工作区的三档布局：隐藏、分栏、全宽。
 *
 * 取代了原先布尔式的"右侧面板是否打开"——那只能表达开/关，无法表达"全宽覆盖"，
 * 也无法让分隔线上的三档控件有稳定的状态源。
 */
export type RightPanelLayout = 'hidden' | 'split' | 'full'

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; placeholder: string; label: string }
>
