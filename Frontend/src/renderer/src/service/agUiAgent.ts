import { HttpAgent, randomUUID } from '@ag-ui/client';
import type { Message } from '@ag-ui/core';
import type { ApplicationConfig, RequirementDevelopmentPlan } from '../typings';

type SendAgUiMessageOptions = {
  systemPrompt: string;
  workspaceRoot?: string;
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

export type AgUiChatResult = {
  threadId: string;
  answer: string;
  assistantMessage?: Message;
};

export type RequirementPlannerResult = {
  threadId: string;
  answer: string;
  planning?: RequirementPlannerPayload;
  assistantMessage?: Message;
};

const PLANNING_DATA_RE = /<planning-data>([\s\S]*?)<\/planning-data>/;

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

    const result = await this.agent.runAgent({
      forwardedProps: {
        systemPrompt: options.systemPrompt,
        workspaceRoot: options.workspaceRoot,
      },
    });
    const assistantMessage = result.newMessages.find((newMessage) => newMessage.role === 'assistant');
    const answer = messageContentToText(assistantMessage?.content);

    return {
      threadId: this.threadId,
      answer,
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

function readResultPlanning(result: unknown) {
  if (!result || typeof result !== 'object') return undefined;
  const planning = (result as { planning?: unknown }).planning;
  if (!planning || typeof planning !== 'object') return undefined;
  return planning as RequirementPlannerPayload;
}
