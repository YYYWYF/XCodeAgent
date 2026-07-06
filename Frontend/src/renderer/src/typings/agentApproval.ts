import type { ApprovalGrant, ToolApproval } from '../service/workspaceTools';

export type AgentApprovalRequest = ToolApproval & {
  agent_tool?: string;
};

export type AgentApprovalDecisionAction = 'approve_once' | 'approve_always' | 'feedback';

export type AgentApprovalDecision = {
  action: AgentApprovalDecisionAction;
  approvalId: string;
  grant?: ApprovalGrant;
  feedback?: string;
};

export type AgentApprovalStatus = 'pending' | 'approved_once' | 'approved_always' | 'feedback';
