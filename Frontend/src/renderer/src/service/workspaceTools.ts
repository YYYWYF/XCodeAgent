export type ToolRiskLevel = 'low' | 'medium' | 'high'

export type ToolRisk = {
  level: ToolRiskLevel
  reasons: string[]
}

export type ToolApproval = {
  id: string
  tool: string
  title: string
  description: string
  subject: string
  risk: ToolRisk
  details?: string | null
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
  expires_at: string
}

export type ApprovalGrant = {
  id: string
  token: string
}

export type TerminalExecRequest = {
  workspace_root?: string
  command?: string
  argv?: string[]
  cwd?: string
  timeout_seconds?: number
  max_output_chars?: number
  approval?: ApprovalGrant
}

export type TerminalExecResult = {
  tool: 'terminal.exec'
  workspace?: {
    root: string
    name: string
    writable: boolean
  }
  cwd?: string
  argv?: string[]
  risk?: ToolRisk
  requires_approval?: boolean
  approval?: ToolApproval
  executed?: boolean
  timed_out?: boolean
  returncode?: number | null
  stdout?: string
  stderr?: string
}

export type ApprovalDecision = ApprovalGrant & {
  tool: string
  status: 'approved'
  scope: 'once' | 'operation'
  expires_at: string
}

export type ReadWorkspaceFileRequest = {
  workspace_root?: string
  path: string
  start_line?: number
  max_lines?: number
  max_chars?: number
}

export type ReadWorkspaceFileResult = {
  tool: 'file.read'
  path: string
  sha256?: string
  start_line: number
  end_line: number
  total_lines: number
  truncated: boolean
  content: string
}

export type WorkspaceTreeNode = {
  path: string
  name: string
  kind: 'directory' | 'file' | 'symlink' | 'other' | 'truncated'
  size?: number | null
  modified?: string
  children?: WorkspaceTreeNode[]
}

export type WorkspaceTreeRequest = {
  workspace_root?: string
  path?: string
  max_depth?: number
  include_hidden?: boolean
  limit?: number
}

export type WorkspaceTreeResult = {
  tool: 'workspace.tree'
  workspace?: { root: string; name: string; writable: boolean }
  path: string
  tree: WorkspaceTreeNode
  truncated: boolean
}

function getAgentBaseUrl(): string {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl
  return agentBaseUrl ? agentBaseUrl.replace(/\/$/, '') : '/api/agent'
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getAgentBaseUrl()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers
    }
  })

  const payload = await response.json().catch(() => ({}))
  if (!response.ok) {
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : `HTTP ${response.status}`
    throw new Error(detail)
  }
  return payload as T
}

export function runTerminalExec(request: TerminalExecRequest): Promise<TerminalExecResult> {
  return requestJson<TerminalExecResult>('/tools/terminal/exec', {
    method: 'POST',
    body: JSON.stringify(request)
  })
}

export function readWorkspaceFile(
  request: ReadWorkspaceFileRequest
): Promise<ReadWorkspaceFileResult> {
  return requestJson<ReadWorkspaceFileResult>('/tools/file/read', {
    method: 'POST',
    body: JSON.stringify(request)
  })
}

export function readWorkspaceTree(
  request: WorkspaceTreeRequest
): Promise<WorkspaceTreeResult> {
  return requestJson<WorkspaceTreeResult>('/tools/workspace/tree', {
    method: 'POST',
    body: JSON.stringify(request)
  })
}

export function approveToolRequest(
  approvalId: string,
  scope: 'once' | 'operation' = 'once'
): Promise<ApprovalDecision> {
  return requestJson<ApprovalDecision>(`/tools/approvals/${approvalId}/approve`, {
    method: 'POST',
    body: JSON.stringify({ scope })
  })
}

export function rejectToolRequest(
  approvalId: string,
  reason?: string
): Promise<{
  id: string
  tool: string
  status: 'rejected'
  reason?: string
  expires_at: string
}> {
  return requestJson<{
    id: string
    tool: string
    status: 'rejected'
    reason?: string
    expires_at: string
  }>(`/tools/approvals/${approvalId}/reject`, {
    method: 'POST',
    body: JSON.stringify({ reason })
  })
}

export function isApprovalRequired(result: TerminalExecResult): result is TerminalExecResult & {
  approval: ToolApproval
} {
  return Boolean(result.requires_approval && result.approval)
}
