export type XcodeAgentMarkdownFileName = 'AGENTS.md' | 'MEMORY.md'

export type XcodeAgentMarkdownFileSummary = {
  name: XcodeAgentMarkdownFileName
  path: string
  size: number
  updatedAt: string
  enabled: boolean
}

export type XcodeAgentMarkdownFileContent = XcodeAgentMarkdownFileSummary & {
  content: string
}

export type AgentsFileContent = XcodeAgentMarkdownFileContent
