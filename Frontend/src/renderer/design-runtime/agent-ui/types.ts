export const AGENT_UI_TEMPLATE_MODULE = '@xcodeagent/agent-ui-design' as const
export const AGENT_UI_TEMPLATE_VERSION = 'agent-ui.v1' as const

export type AgentUiSurfaceType = 'standalone_page' | 'floating_panel'

export type AgentPreviewState =
  | 'normal'
  | 'empty'
  | 'loading'
  | 'running'
  | 'stopped'
  | 'error'
  | 'tool'
  | 'approval'
  | 'success'

export interface AgentUiContextItem {
  id: string
  label: string
  value: string
}

export interface AgentUiCapability {
  id: string
  label: string
}

export interface AgentUiMockContent {
  userMessage: string
  assistantMessage: string
  toolTitle: string
  toolDetail: string
  approvalTitle: string
  approvalDetail: string
  successMessage: string
  errorMessage: string
}

export interface AgentUiFeatureFlags {
  attachments: boolean
  approvals: boolean
  tools: boolean
  maximize: boolean
}

export interface AgentUiTemplateConfig {
  templateVersion: typeof AGENT_UI_TEMPLATE_VERSION
  agentId: string
  surface: AgentUiSurfaceType
  name: string
  responsibility: string
  actionId: string
  contextItems: AgentUiContextItem[]
  capabilities: AgentUiCapability[]
  suggestedQuestions: string[]
  features: AgentUiFeatureFlags
  mock: AgentUiMockContent
}

export interface AgentSurfaceEvidenceInput {
  agentId: string
  actionId: string
  surface: AgentUiSurfaceType
}

export interface AgentSurfaceEvidence extends AgentSurfaceEvidenceInput {
  templateModule: typeof AGENT_UI_TEMPLATE_MODULE
  templateVersion: typeof AGENT_UI_TEMPLATE_VERSION
}

export interface FloatingPoint {
  x: number
  y: number
}

export interface FloatingSize {
  width: number
  height: number
}
