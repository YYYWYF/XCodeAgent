/// <reference types="vite/client" />

declare module '*.less' {
  const classes: Record<string, string>
  export default classes
}

declare module '@xcodeagent/agent-ui-design' {
  import type { ComponentType } from 'react'

  export const AgentConversationTemplate: ComponentType<{ configJson: string }>
  export const AgentFloatingPanelTemplate: ComponentType<{ configJson: string }>
}
