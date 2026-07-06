import { HttpAgent, randomUUID } from '@ag-ui/client';
import type { AgentSubscriber } from '@ag-ui/client';
import type { Message } from '@ag-ui/core';
import type {
  AgentApprovalDecision,
  AgentApprovalRequest,
  ApplicationConfig,
  DevelopmentOrchestrationPayload,
  RequirementDevelopmentPlan,
} from '../typings';

type SendAgUiMessageOptions = {
  systemPrompt: string;
  workspaceRoot?: string;
  application?: ApplicationConfig;
  approvalDecision?: AgentApprovalDecision;
};

export type RequirementQuestionType = 'single' | 'multiple' | 'text' | 'confirm';

export type RequirementQuestionOption = {
  id: string;
  label: string;
  description?: string;
};

export type RequirementQuestion = {
  id: string;
  type: RequirementQuestionType;
  title: string;
  description?: string;
  required: boolean;
  options: RequirementQuestionOption[];
};

export type RequirementAnswer = {
  questionId: string;
  question: string;
  value: string | string[];
  label?: string;
};

export type RequirementPlannerState = {
  requirement: string;
  answers: RequirementAnswer[];
  iteration: number;
  lastQuestions?: RequirementQuestion[];
  status?: 'questions' | 'plan';
  plan?: RequirementDevelopmentPlan;
};

export type RequirementPlannerPayload = {
  tool: 'requirement_planner';
  status: 'questions' | 'plan';
  phase: 'discovery' | 'planning';
  iteration: number;
  message: string;
  questions: RequirementQuestion[];
  answers: RequirementAnswer[];
  plan?: RequirementDevelopmentPlan | null;
  state: RequirementPlannerState;
};

type SendPlannerMessageOptions = {
  action: 'start' | 'answer' | 'finalize';
  plannerState?: RequirementPlannerState;
  application: ApplicationConfig;
};

type SendOrchestratorMessageOptions = {
  action: 'start' | 'answer' | 'finalize' | 'dispatch' | 'verify';
  orchestratorState?: Record<string, unknown>;
  plannerState?: RequirementPlannerState;
  application: ApplicationConfig;
  workspaceRoot?: string;
};

export type AgUiChatResult = {
  threadId: string;
  answer: string;
  orchestration?: DevelopmentOrchestrationPayload;
  approval?: AgentApprovalRequest;
  assistantMessage?: Message;
};

export type RequirementPlannerResult = {
  threadId: string;
  answer: string;
  planning?: RequirementPlannerPayload;
  assistantMessage?: Message;
};

export type DevelopmentOrchestratorResult = {
  threadId: string;
  answer: string;
  orchestration?: DevelopmentOrchestrationPayload;
  assistantMessage?: Message;
};

const PLANNING_DATA_RE = /<planning-data>([\s\S]*?)<\/planning-data>/;
const ORCHESTRATION_DATA_RE = /<orchestration-data>([\s\S]*?)<\/orchestration-data>/;

function getAgUiUrl() {
  const agentBaseUrl = window.xcodeAgent?.agentBaseUrl;
  return agentBaseUrl ? `${agentBaseUrl.replace(/\/$/, '')}/ag-ui` : '/api/agent/ag-ui';
}

export class AgUiChatSession {
  readonly threadId: string;

  private readonly agent: HttpAgent;

  constructor(threadId = randomUUID()) {
    this.threadId = threadId;
    this.agent = new HttpAgent({
      url: getAgUiUrl(),
      threadId,
    });
  }

  async sendMessage(message: string, options: SendAgUiMessageOptions): Promise<AgUiChatResult> {
    this.agent.addMessage({
      id: randomUUID(),
      role: 'user',
      content: message,
    });

    let eventApproval: AgentApprovalRequest | undefined;
    const subscriber: AgentSubscriber = {
      onCustomEvent: ({ event }) => {
        if (event.name === 'tool-approval-required') {
          eventApproval = readApprovalPayload(event.value);
        }
      },
      onStateSnapshotEvent: ({ event }) => {
        const snapshotApproval = readApprovalFromState(event.snapshot);
        if (snapshotApproval) eventApproval = snapshotApproval;
      },
    };

    const result = await this.agent.runAgent({
      forwardedProps: {
        systemPrompt: options.systemPrompt,
        workspaceRoot: options.workspaceRoot,
        application: options.application,
        approvalDecision: options.approvalDecision,
      },
    }, subscriber);
    const assistantMessage = result.newMessages.find((newMessage) => newMessage.role === 'assistant');
    const parsed = extractOrchestrationData(messageContentToText(assistantMessage?.content), result.result);
    const approval = eventApproval ?? readResultApproval(result.result);

    return {
      threadId: this.threadId,
      answer: parsed.answer,
      orchestration: parsed.orchestration,
      approval,
      assistantMessage,
    };
  }
}

export class RequirementPlannerSession {
  readonly threadId: string;

  private readonly agent: HttpAgent;

  constructor(threadId = randomUUID()) {
    this.threadId = threadId;
    this.agent = new HttpAgent({
      url: getAgUiUrl(),
      threadId,
    });
  }

  async sendMessage(
    message: string,
    options: SendPlannerMessageOptions,
  ): Promise<RequirementPlannerResult> {
    this.agent.addMessage({
      id: randomUUID(),
      role: 'user',
      content: message,
    });

    const result = await this.agent.runAgent({
      forwardedProps: {
        agentMode: 'requirement-planner',
        plannerAction: options.action,
        plannerState: options.plannerState,
        application: options.application,
      },
    });
    const assistantMessage = result.newMessages.find((newMessage) => newMessage.role === 'assistant');
    const parsed = extractPlanningData(messageContentToText(assistantMessage?.content), result.result);

    return {
      threadId: this.threadId,
      answer: parsed.answer,
      planning: parsed.planning,
      assistantMessage,
    };
  }
}

export class DevelopmentOrchestratorSession {
  readonly threadId: string;

  private readonly agent: HttpAgent;

  constructor(threadId = randomUUID()) {
    this.threadId = threadId;
    this.agent = new HttpAgent({
      url: getAgUiUrl(),
      threadId,
    });
  }

  async sendMessage(
    message: string,
    options: SendOrchestratorMessageOptions,
  ): Promise<DevelopmentOrchestratorResult> {
    this.agent.addMessage({
      id: randomUUID(),
      role: 'user',
      content: message,
    });

    const result = await this.agent.runAgent({
      forwardedProps: {
        agentMode: 'development-orchestrator',
        orchestratorAction: options.action,
        orchestratorState: options.orchestratorState,
        plannerState: options.plannerState,
        application: options.application,
        workspaceRoot: options.workspaceRoot,
      },
    });
    const assistantMessage = result.newMessages.find((newMessage) => newMessage.role === 'assistant');
    const parsed = extractOrchestrationData(messageContentToText(assistantMessage?.content), result.result);

    return {
      threadId: this.threadId,
      answer: parsed.answer,
      orchestration: parsed.orchestration,
      assistantMessage,
    };
  }
}

function messageContentToText(content: Message['content'] | undefined) {
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .map((item) => {
        if (typeof item === 'string') return item;
        if ('text' in item && typeof item.text === 'string') return item.text;
        return '';
      })
      .filter(Boolean)
      .join('\n');
  }
  return '';
}

function extractPlanningData(answer: string, result: unknown) {
  const resultPlanning = readResultPlanning(result);
  if (resultPlanning) {
    return {
      answer: answer.replace(PLANNING_DATA_RE, '').trim(),
      planning: resultPlanning,
    };
  }

  const match = PLANNING_DATA_RE.exec(answer);
  if (!match) {
    return { answer: answer.trim(), planning: undefined };
  }

  try {
    return {
      answer: answer.replace(PLANNING_DATA_RE, '').trim(),
      planning: JSON.parse(match[1]) as RequirementPlannerPayload,
    };
  } catch {
    return { answer: answer.replace(PLANNING_DATA_RE, '').trim(), planning: undefined };
  }
}

function extractOrchestrationData(answer: string, result: unknown) {
  const resultOrchestration = readResultOrchestration(result);
  if (resultOrchestration) {
    return {
      answer: answer.replace(ORCHESTRATION_DATA_RE, '').trim(),
      orchestration: resultOrchestration,
    };
  }

  const match = ORCHESTRATION_DATA_RE.exec(answer);
  if (!match) {
    return { answer: answer.trim(), orchestration: undefined };
  }

  try {
    return {
      answer: answer.replace(ORCHESTRATION_DATA_RE, '').trim(),
      orchestration: JSON.parse(match[1]) as DevelopmentOrchestrationPayload,
    };
  } catch {
    return { answer: answer.replace(ORCHESTRATION_DATA_RE, '').trim(), orchestration: undefined };
  }
}

function readApprovalFromState(snapshot: unknown) {
  if (!snapshot || typeof snapshot !== 'object') return undefined;
  return readApprovalPayload((snapshot as { approval?: unknown }).approval);
}

function readResultApproval(result: unknown) {
  if (!result || typeof result !== 'object') return undefined;
  return readApprovalPayload((result as { approval?: unknown }).approval);
}

function readApprovalPayload(value: unknown): AgentApprovalRequest | undefined {
  if (!value || typeof value !== 'object') return undefined;
  const approval = value as Partial<AgentApprovalRequest>;
  if (
    typeof approval.id !== 'string' ||
    typeof approval.tool !== 'string' ||
    typeof approval.title !== 'string' ||
    typeof approval.subject !== 'string'
  ) {
    return undefined;
  }

  return {
    id: approval.id,
    tool: approval.tool,
    title: approval.title,
    description: String(approval.description || ''),
    subject: approval.subject,
    risk: {
      level:
        approval.risk?.level === 'high' || approval.risk?.level === 'medium'
          ? approval.risk.level
          : 'low',
      reasons: Array.isArray(approval.risk?.reasons)
        ? approval.risk.reasons.map(String)
        : [],
    },
    details: typeof approval.details === 'string' ? approval.details : null,
    status: approval.status === 'approved' || approval.status === 'rejected' ? approval.status : 'pending',
    created_at: String(approval.created_at || ''),
    expires_at: String(approval.expires_at || ''),
    agent_tool: typeof approval.agent_tool === 'string' ? approval.agent_tool : undefined,
  };
}

function readResultPlanning(result: unknown) {
  if (!result || typeof result !== 'object') return undefined;
  const planning = (result as { planning?: unknown }).planning;
  if (!planning || typeof planning !== 'object') return undefined;
  return planning as RequirementPlannerPayload;
}

function readResultOrchestration(result: unknown) {
  if (!result || typeof result !== 'object') return undefined;
  const orchestration = (result as { orchestration?: unknown }).orchestration;
  if (!orchestration || typeof orchestration !== 'object') return undefined;
  return orchestration as DevelopmentOrchestrationPayload;
}
