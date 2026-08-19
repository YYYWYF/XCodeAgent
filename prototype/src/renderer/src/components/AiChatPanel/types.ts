import type { ProcessStepRecord, ToolCallRecord } from '../../service/agUiAgent'
import type {
  ChatMessageSkill,
  EditorMode,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet
} from '../../typings'
import type { WorkbenchPhase } from '../../workbenchPhase'
import type { AgentDetailBlocker } from '../../agentDevelopment'

export type AgentChatMessage = {
  id: number
  role: 'user' | 'assistant'
  content: string
  /** 消息创建时所属的工作台阶段，历史消息不得跟随当前查看阶段改名。 */
  agentPhase?: WorkbenchPhase
  skills?: ChatMessageSkill[]
  workflow?: WorkflowRunPayload
  codeChanges?: WorkspaceCodeChangeSet
  toolCalls?: ToolCallRecord[]
  processSteps?: ProcessStepRecord[]
  /** 待设计目标挡板：作为对话历史消息持久化，支持页面、接口与智能体详细设计入口。 */
  detailBlocker?:
    | {
        type: 'page'
        pageId: string
        label: string
        path?: string
        purpose?: string
      }
    | {
        type: 'endpoint'
        apiContractId: string
        endpointId: string
        label: string
        path?: string
        purpose?: string
      }
    | AgentDetailBlocker
  createdAt: number
}

/** 设计阶段右侧「文档」的产物 key，作为工作区 tab 使用。 */
export type WorkspaceDocKey = 'requirement-spec' | 'project-plan'

export type RightPanelState =
  | { type: 'preview'; requestKey?: string; url?: string }
  | { type: 'agent-preview' }
  | {
      type: 'diff'
      codeChanges: WorkspaceCodeChangeSet
      selectedPath?: string
    }
  | { type: 'process' }
  | { type: 'doc'; docKey?: WorkspaceDocKey }
  | { type: 'source' }
  /** 开发阶段的交付清单；仅用于选择当前 Workflow 目标，不承载写入权限。 */
  | { type: 'development-artifacts' }
  /** 测试阶段的业务用例目录与结果内容区。 */
  | { type: 'test-cases' }

/** 右侧公共工作区的三档展示尺寸。 */
export type RightPanelLayout = 'hidden' | 'split' | 'full'

export type ChatCopy = Record<
  EditorMode,
  { title: string; description: string; placeholder: string; label: string }
>
