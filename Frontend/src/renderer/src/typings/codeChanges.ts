import type { AgentApprovalRequest } from './agentApproval';

export type WorkspaceCodeChangeStatus = 'pending_approval' | 'applied' | 'rejected';

export type WorkspaceCodeChangeType = 'added' | 'modified' | 'deleted';

export type WorkspaceCodeChangeTool = 'file.write' | 'file.patch' | 'file.delete';

export type WorkspaceCodeChangeFile = {
  id: string;
  path: string;
  changeType: WorkspaceCodeChangeType;
  additions: number;
  deletions: number;
  diff: string;
  truncated?: boolean;
  binary?: boolean;
  approvalId?: string;
  tool: WorkspaceCodeChangeTool;
  executed?: boolean;
};

export type WorkspaceCodeChangeSet = {
  id: string;
  status: WorkspaceCodeChangeStatus;
  workspaceRoot: string;
  summary: {
    files: number;
    additions: number;
    deletions: number;
  };
  files: WorkspaceCodeChangeFile[];
  approvals?: AgentApprovalRequest[];
};
