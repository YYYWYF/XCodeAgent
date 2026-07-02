export type ToolRiskLevel = 'low' | 'medium' | 'high';

export type ToolRisk = {
  level: ToolRiskLevel;
  reasons: string[];
};

export type ToolApproval = {
  id: string;
  tool: string;
  title: string;
  description: string;
  subject: string;
  risk: ToolRisk;
  details?: string | null;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
  expires_at: string;
};

export type ApprovalGrant = {
  id: string;
  token: string;
};

export type TerminalExecRequest = {
  workspace_root?: string;
  command?: string;
  argv?: string[];
  cwd?: string;
  timeout_seconds?: number;
  max_output_chars?: number;
  approval?: ApprovalGrant;
};

export type TerminalExecResult = {
  tool: 'terminal.exec';
  workspace?: {
    root: string;
    name: string;
    writable: boolean;
  };
  cwd?: string;
  argv?: string[];
  risk?: ToolRisk;
  requires_approval?: boolean;
  approval?: ToolApproval;
  executed?: boolean;
  timed_out?: boolean;
  returncode?: number | null;
  stdout?: string;
  stderr?: string;
};

export type ApprovalDecision = ApprovalGrant & {
  tool: string;
  status: 'approved';
  expires_at: string;
};

function getAgentBaseUrl() {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl;
  return agentBaseUrl ? agentBaseUrl.replace(/\/$/, '') : '/api/agent';
}

async function requestJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getAgentBaseUrl()}${path}`, {
    ...init,
    headers: {
      'Content-Type': 'application/json',
      ...init?.headers,
    },
  });

  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail =
      payload && typeof payload === 'object' && 'detail' in payload
        ? String((payload as { detail: unknown }).detail)
        : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return payload as T;
}

export function runTerminalExec(request: TerminalExecRequest) {
  return requestJson<TerminalExecResult>('/tools/terminal/exec', {
    method: 'POST',
    body: JSON.stringify(request),
  });
}

export function approveToolRequest(approvalId: string) {
  return requestJson<ApprovalDecision>(`/tools/approvals/${approvalId}/approve`, {
    method: 'POST',
  });
}

export function rejectToolRequest(approvalId: string) {
  return requestJson<{ id: string; tool: string; status: 'rejected'; expires_at: string }>(
    `/tools/approvals/${approvalId}/reject`,
    {
      method: 'POST',
    },
  );
}

export function isApprovalRequired(result: TerminalExecResult): result is TerminalExecResult & {
  approval: ToolApproval;
} {
  return Boolean(result.requires_approval && result.approval);
}
