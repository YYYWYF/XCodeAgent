export type AgentFileDocument = {
  name: string
  relativePath: string
  content: string
  revision: string
  sizeBytes: number
  updatedAt: string
}

export type AgentFile = {
  root: string
  document: AgentFileDocument
}
