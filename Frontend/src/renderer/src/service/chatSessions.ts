import type {
  AgentApprovalRequest,
  AgentApprovalStatus,
  DevelopmentOrchestrationPayload,
  EditorMode,
  ChatMessageSkill,
  WorkflowBuildExecutionSlice,
  WorkflowFormalRevisionBranch,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet,
} from '../typings';
import type { WorkbenchPhase } from '../workbenchPhase';
import { readDagGenerationSnapshot } from './agUiAgent';
import type { ProcessStepRecord, ToolCallRecord } from './agUiAgent';

export type ChatSessionMessage = {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  skills?: ChatMessageSkill[];
  orchestration?: DevelopmentOrchestrationPayload;
  approval?: AgentApprovalRequest;
  approvalStatus?: AgentApprovalStatus;
  codeChanges?: WorkspaceCodeChangeSet;
  workflow?: WorkflowRunPayload;
  /** 持久化当前 assistant 轮次的模型或 Workflow 错误，恢复历史时仍能显示错误卡片。 */
  error?: string;
  toolCalls?: ToolCallRecord[];
  processSteps?: ProcessStepRecord[];
  revisionHandoff?: ChatSessionRevisionHandoff;
  createdAt: number;
};

export type ChatSessionRevisionHandoff = {
  kind:
    | 'formal_revision'
    | 'revision_planning'
    | 'revision_development'
    | 'revision_development_entry';
  formalBranch: WorkflowFormalRevisionBranch;
  targetSessionId: string;
  targetConversationThreadId: string;
  impactInteractionId: string;
  changeId?: string;
  request: string;
};

export type ChatSessionRevisionContext = {
  kind: 'formal_revision';
  sessionRole: 'design' | 'development';
  formalBranch: WorkflowFormalRevisionBranch;
  impactInteractionId: string;
  sourceSessionId: string;
  sourceConversationThreadId: string;
  sourceRunId: string;
  planningThreadId: string;
  changeId?: string;
  handoffFromSessionId?: string;
  handoffFromConversationThreadId?: string;
  technicalPlanSha256?: string;
};

export type AgentStage = 'DESIGN' | 'PLAN' | 'DEVELOPMENT';

export type ChatSessionTargetType = 'workflow' | 'page' | 'api' | 'entity';

export type ChatSessionRecord = {
  id: string;
  title: string;
  editorMode: EditorMode;
  workbenchPhase: WorkbenchPhase;
  workflowId: string;
  targetType: ChatSessionTargetType;
  stage?: AgentStage;
  sequence?: number;
  entryKey?: string;
  threadId: string;
  apiContractId?: string;
  endpointId?: string;
  endpointLabel?: string;
  entityId?: string;
  entityLabel?: string;
  pageId?: string;
  revisionContext?: ChatSessionRevisionContext;
  workspaceRoot: string;
  messages: ChatSessionMessage[];
  createdAt: number;
  updatedAt: number;
};

export type ChatSessionSummary = {
  id: string;
  title: string;
  editorMode: EditorMode;
  workbenchPhase: WorkbenchPhase;
  workflowId: string;
  targetType: ChatSessionTargetType;
  stage?: AgentStage;
  sequence?: number;
  entryKey?: string;
  threadId: string;
  apiContractId?: string;
  endpointId?: string;
  endpointLabel?: string;
  entityId?: string;
  entityLabel?: string;
  pageId?: string;
  revisionContext?: ChatSessionRevisionContext;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
};

export type CreateChatSessionInput = {
  workspaceRoot: string;
  workflowId: string;
  editorMode: EditorMode;
  workbenchPhase: WorkbenchPhase;
  targetType: ChatSessionTargetType;
  entryKey?: string;
  title?: string;
  apiContractId?: string;
  endpointId?: string;
  endpointLabel?: string;
  entityId?: string;
  entityLabel?: string;
  pageId?: string;
  revisionContext?: ChatSessionRevisionContext;
  /** 仅用于恢复已消费 continuation 但本地会话缺失的工作台 execution。 */
  recoveryExecutionRunId?: string;
};

export type SessionWorkspaceSummary = {
  workspaceRoot: string;
  name: string;
  sessionCount: number;
  frontendCount: number;
  backendCount: number;
  latestUpdatedAt: number;
  latestTitle: string;
};

const CHAT_SESSION_EDITOR_MODES: EditorMode[] = ['frontend', 'backend'];
const CHAT_SESSION_WORKBENCH_PHASES: WorkbenchPhase[] = [
  'product',
  'planning',
  'development',
  'test',
  'review',
  'acceptance',
];
const CHAT_SESSION_TARGET_TYPES: ChatSessionTargetType[] = ['workflow', 'page', 'api', 'entity'];
const ACTIVE_SESSION_STORAGE_PREFIX = 'xcodeagent:active-session:';

type ElectronInvoke = (channel: string, ...args: unknown[]) => Promise<unknown>;

function storageKey(workspaceRoot: string, editorMode: EditorMode): string {
  return `xcode-agent-sessions:${workspaceRoot}:${editorMode}`;
}
/** 生成指定应用和编辑模式的当前会话恢复键。 */
function activeSessionStorageKey(
  applicationId: string,
  editorMode: EditorMode,
  phase: WorkbenchPhase,
): string {
  return `${ACTIVE_SESSION_STORAGE_PREFIX}${applicationId}:${editorMode}:${phase}`;
}

/** 读取用户退出应用前最后打开的会话标识。 */
export function getPersistedActiveSessionId(
  applicationId: string,
  editorMode: EditorMode,
  phase: WorkbenchPhase,
): string | undefined {
  const sessionId = window.localStorage.getItem(
    activeSessionStorageKey(applicationId, editorMode, phase),
  );
  return sessionId?.trim() || undefined;
}

/** 持久化用户当前打开的会话；传入空值时清除已保存选择。 */
export function setPersistedActiveSessionId(
  applicationId: string,
  editorMode: EditorMode,
  phase: WorkbenchPhase,
  sessionId: string | undefined,
): void {
  const key = activeSessionStorageKey(applicationId, editorMode, phase);
  const normalizedSessionId = sessionId?.trim() || '';
  if (normalizedSessionId) window.localStorage.setItem(key, normalizedSessionId);
  else window.localStorage.removeItem(key);
}

/** 清理指定工作区的浏览器兜底会话，桌面主进程中的正式会话由项目删除 IPC 负责。 */
export function clearWorkspaceChatSessionCache(workspaceRoot: string): void {
  CHAT_SESSION_EDITOR_MODES.forEach((editorMode) => {
    window.localStorage.removeItem(storageKey(workspaceRoot, editorMode));
  });
}

function getElectronInvoke(): ElectronInvoke | undefined {
  const electronApi = window.electron as
    | { ipcRenderer?: { invoke?: ElectronInvoke } }
    | undefined;
  return electronApi?.ipcRenderer?.invoke;
}

function normalizeMessages(value: unknown): ChatSessionMessage[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Partial<ChatSessionMessage> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      id: Number(item.id || Date.now()),
      role: item.role === 'assistant' ? 'assistant' : 'user',
      content: String(item.content || ''),
      skills: normalizeMessageSkills(item.skills),
      orchestration:
        item.orchestration && typeof item.orchestration === 'object'
          ? (item.orchestration as DevelopmentOrchestrationPayload)
          : undefined,
      approval:
        item.approval && typeof item.approval === 'object'
          ? (item.approval as AgentApprovalRequest)
          : undefined,
      codeChanges:
        item.codeChanges && typeof item.codeChanges === 'object'
          ? (item.codeChanges as WorkspaceCodeChangeSet)
          : undefined,
      workflow:
        item.workflow && typeof item.workflow === 'object'
          ? (item.workflow as WorkflowRunPayload)
          : undefined,
      error:
        typeof item.error === 'string' && item.error.trim() ? item.error.trim() : undefined,
      toolCalls: normalizeToolCalls(item.toolCalls),
      processSteps: normalizeProcessSteps(item.processSteps),
      revisionHandoff: normalizeRevisionSessionHandoff(item.revisionHandoff),
      approvalStatus:
        item.approvalStatus === 'approved_once' ||
        item.approvalStatus === 'approved_always' ||
        item.approvalStatus === 'feedback'
          ? item.approvalStatus
          : item.approval
            ? 'pending'
            : undefined,
      createdAt: Number(item.createdAt || Date.now()),
    }));
}

/** 规范化来源会话中的二次修改跳转回执，避免任意本地路径或外部链接进入消息。 */
function normalizeRevisionSessionHandoff(
  value: unknown,
): ChatSessionRevisionHandoff | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const handoff = value as Partial<ChatSessionRevisionHandoff>;
  const kind = String(handoff.kind || '');
  if (
    ![
      'formal_revision',
      'revision_planning',
      'revision_development',
      'revision_development_entry',
    ].includes(kind)
  ) {
    return undefined;
  }
  const formalBranch = normalizeEndpointField(handoff.formalBranch);
  const targetSessionId = normalizeEndpointField(handoff.targetSessionId);
  const targetConversationThreadId = normalizeEndpointField(handoff.targetConversationThreadId);
  const impactInteractionId = normalizeEndpointField(handoff.impactInteractionId);
  const changeId = normalizeEndpointField(handoff.changeId);
  const request = typeof handoff.request === 'string' ? handoff.request.trim().slice(0, 16_000) : '';
  if (
    !['design_stage_revision', 'workbench_plan_revision'].includes(formalBranch || '') ||
    !targetSessionId ||
    !targetConversationThreadId ||
    !impactInteractionId ||
    !request
  ) {
    return undefined;
  }
  if (
    ['revision_planning', 'revision_development', 'revision_development_entry'].includes(kind) &&
    !changeId
  ) {
    return undefined;
  }
  return {
    kind: kind as ChatSessionRevisionHandoff['kind'],
    formalBranch: formalBranch as WorkflowFormalRevisionBranch,
    targetSessionId,
    targetConversationThreadId,
    impactInteractionId,
    changeId,
    request,
  };
}

/** 过滤并规范化会话消息中的技能展示快照。 */
export function normalizeMessageSkills(value: unknown): ChatMessageSkill[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const names = new Set<string>();
  const skills = value
    .filter((item): item is Partial<ChatMessageSkill> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      name: typeof item.name === 'string' ? item.name.trim() : '',
      description: typeof item.description === 'string' ? item.description.trim() : '',
    }))
    .filter((item) => {
      if (!item.name || names.has(item.name)) return false;
      names.add(item.name);
      return true;
    });
  return skills.length > 0 ? skills : undefined;
}

function normalizeProcessSteps(value: unknown): ProcessStepRecord[] | undefined {
  /** 恢复会话步骤时剥离运行期工具活动，避免把瞬时状态误显示为历史执行。 */

  if (!Array.isArray(value)) return undefined;
  const steps = value
    .filter((item): item is ProcessStepRecord => {
      if (!item || typeof item !== 'object') return false;
      const step = item as Partial<ProcessStepRecord>;
      return Boolean(step.id && step.title && step.kind && step.status);
    })
    .map((step) => {
      const normalizedStep = {
        ...step,
        ...(step.buildExecutionSlice
          ? { buildExecutionSlice: withoutToolActivity(step.buildExecutionSlice) }
          : {}),
      };
      if (step.dagGeneration !== undefined) {
        const dagGeneration = readDagGenerationSnapshot(step.dagGeneration);
        if (dagGeneration) normalizedStep.dagGeneration = dagGeneration;
        else delete normalizedStep.dagGeneration;
      }
      return normalizedStep;
    });
  return steps.length > 0 ? steps : undefined;
}

function withoutToolActivity(
  executionSlice: WorkflowBuildExecutionSlice,
): WorkflowBuildExecutionSlice {
  /** 复制构建切片并删除任务上的临时工具活动，不修改调用方持有的会话对象。 */

  return {
    ...executionSlice,
    tasks: executionSlice.tasks?.map((task) => {
      const persistedTask = { ...task };
      delete persistedTask.activeToolActivity;
      return persistedTask;
    }),
  };
}

function normalizeToolCalls(value: unknown): ToolCallRecord[] | undefined {
  if (!Array.isArray(value)) return undefined;
  const toolCalls = value
    .filter((item): item is Partial<ToolCallRecord> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      id: String(item.id || ''),
      name: String(item.name || 'unknown'),
      args: typeof item.args === 'string' ? item.args : '',
      result: typeof item.result === 'string' ? item.result : undefined,
      status: item.status === 'completed' ? ('completed' as const) : ('running' as const),
    }))
    .filter((item) => item.id);
  return toolCalls.length > 0 ? toolCalls : undefined;
}

/** 校验并规范化完整会话，阶段字段缺失或非法时拒绝进入当前存储契约。 */
function normalizeSession(value: unknown): ChatSessionRecord | null {
  if (!value || typeof value !== 'object') return null
  const session = value as Partial<ChatSessionRecord>
  if (
    !session.id ||
    !session.editorMode ||
    !session.workflowId ||
    !CHAT_SESSION_TARGET_TYPES.includes(session.targetType as ChatSessionTargetType) ||
    !session.threadId ||
    !isWorkbenchPhase(session.workbenchPhase)
  )
    return null
  const stage = stageForWorkbenchPhase(session.workbenchPhase)
  if (
    stage &&
    (session.stage !== stage ||
      !Number.isInteger(session.sequence) ||
      Number(session.sequence) < 1 ||
      !normalizeEndpointField(session.entryKey))
  )
    return null
  if (!stage && (session.stage || session.sequence || session.entryKey)) return null
  const pageId = normalizePageId(session.pageId)
  const apiContractId = normalizeEndpointField(session.apiContractId)
  const endpointId = normalizeEndpointField(session.endpointId)
  const entityId = normalizeEndpointField(session.entityId)
  if (
    (session.targetType === 'workflow' && (pageId || apiContractId || endpointId || entityId)) ||
    (session.targetType === 'page' && (!pageId || apiContractId || endpointId || entityId)) ||
    (session.targetType === 'api' && (!apiContractId || !endpointId || pageId || entityId)) ||
    (session.targetType === 'entity' && (!entityId || pageId || apiContractId || endpointId))
  )
    return null
  return {
    id: String(session.id),
    title: String(session.title || '新对话'),
    editorMode: session.editorMode,
    workbenchPhase: session.workbenchPhase,
    workflowId: String(session.workflowId),
    targetType: session.targetType as ChatSessionTargetType,
    ...(stage
      ? {
          stage,
          sequence: Number(session.sequence),
          entryKey: String(session.entryKey)
        }
      : {}),
    threadId: String(session.threadId),
    apiContractId,
    endpointId,
    endpointLabel: normalizeEndpointField(session.endpointLabel),
    entityId,
    entityLabel: normalizeEndpointField(session.entityLabel),
    pageId,
    revisionContext: normalizeRevisionSessionContext(session.revisionContext),
    workspaceRoot: String(session.workspaceRoot || ''),
    messages: normalizeMessages(session.messages),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now())
  }
}

/** 将完整会话投影为包含阶段归属的列表摘要。 */
function toSummary(session: ChatSessionRecord): ChatSessionSummary {
  return {
    id: session.id,
    title: session.title,
    editorMode: session.editorMode,
    workbenchPhase: session.workbenchPhase,
    workflowId: session.workflowId,
    targetType: session.targetType,
    stage: session.stage,
    sequence: session.sequence,
    entryKey: session.entryKey,
    threadId: session.threadId,
    apiContractId: session.apiContractId,
    endpointId: session.endpointId,
    endpointLabel: session.endpointLabel,
    entityId: session.entityId,
    entityLabel: session.entityLabel,
    pageId: session.pageId,
    revisionContext: session.revisionContext,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: session.messages.length
  }
}

/** 规范化会话摘要列表，并剔除不属于当前阶段契约的记录。 */
function normalizeSummaries(value: unknown): ChatSessionSummary[] {
  if (!Array.isArray(value)) return []
  return value
    .filter((item): item is Partial<ChatSessionSummary> =>
      Boolean(item && typeof item === 'object')
    )
    .map((item) => normalizeSummary(item))
    .filter((item): item is ChatSessionSummary => Boolean(item))
}

/** 校验主进程返回的会话摘要，拒绝缺少当前阶段身份或目标绑定的记录。 */
function normalizeSummary(item: Partial<ChatSessionSummary>): ChatSessionSummary | null {
  if (
    !item.id ||
    (item.editorMode !== 'frontend' && item.editorMode !== 'backend') ||
    !item.workflowId ||
    !item.threadId ||
    !isWorkbenchPhase(item.workbenchPhase) ||
    !CHAT_SESSION_TARGET_TYPES.includes(item.targetType as ChatSessionTargetType)
  )
    return null
  const stage = stageForWorkbenchPhase(item.workbenchPhase)
  if (
    (stage &&
      (item.stage !== stage ||
        !Number.isInteger(item.sequence) ||
        Number(item.sequence) < 1 ||
        !normalizeEndpointField(item.entryKey))) ||
    (!stage && (item.stage || item.sequence || item.entryKey))
  )
    return null
  const pageId = normalizePageId(item.pageId)
  const apiContractId = normalizeEndpointField(item.apiContractId)
  const endpointId = normalizeEndpointField(item.endpointId)
  const entityId = normalizeEndpointField(item.entityId)
  if (
    (item.targetType === 'workflow' && (pageId || apiContractId || endpointId || entityId)) ||
    (item.targetType === 'page' && (!pageId || apiContractId || endpointId || entityId)) ||
    (item.targetType === 'api' && (!apiContractId || !endpointId || pageId || entityId)) ||
    (item.targetType === 'entity' && (!entityId || pageId || apiContractId || endpointId))
  )
    return null
  return {
    id: String(item.id),
    title: String(item.title || '新对话'),
    editorMode: item.editorMode,
    workbenchPhase: item.workbenchPhase,
    workflowId: String(item.workflowId),
    targetType: item.targetType as ChatSessionTargetType,
    ...(stage ? { stage, sequence: Number(item.sequence), entryKey: String(item.entryKey) } : {}),
    threadId: String(item.threadId),
    apiContractId,
    endpointId,
    endpointLabel: normalizeEndpointField(item.endpointLabel),
    entityId,
    entityLabel: normalizeEndpointField(item.entityLabel),
    pageId,
    revisionContext: normalizeRevisionSessionContext(item.revisionContext),
    createdAt: Number(item.createdAt || Date.now()),
    updatedAt: Number(item.updatedAt || Date.now()),
    messageCount: Number(item.messageCount || 0)
  }
}

/** 判断持久化会话是否声明了当前支持的工作台阶段。 */
function isWorkbenchPhase(value: unknown): value is WorkbenchPhase {
  return CHAT_SESSION_WORKBENCH_PHASES.includes(value as WorkbenchPhase)
}

/** 将前三个工作台阶段映射为阶段会话契约，后三阶段不创建 StageSession。 */
export function stageForWorkbenchPhase(value: WorkbenchPhase): AgentStage | undefined {
  if (value === 'product') return 'DESIGN'
  if (value === 'planning') return 'PLAN'
  if (value === 'development') return 'DEVELOPMENT'
  return undefined
}

/** 规范化页面会话标识，空值不写入本地会话契约。 */
function normalizePageId(value: unknown): string | undefined {
  const pageId = typeof value === 'string' ? value.trim() : '';
  return pageId || undefined;
}

/** 规范化 API endpoint 会话字段，空值不写入本地会话契约。 */
function normalizeEndpointField(value: unknown): string | undefined {
  const text = typeof value === 'string' ? value.trim() : '';
  return text || undefined;
}

/** 规范化正式二次修改的最小会话身份，拒绝缺少分支、来源或原规划线程的记录。 */
export function normalizeRevisionSessionContext(
  value: unknown,
): ChatSessionRevisionContext | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined;
  const context = value as Partial<ChatSessionRevisionContext>;
  if (context.kind !== 'formal_revision') return undefined;
  const sessionRole = normalizeEndpointField(context.sessionRole);
  const formalBranch = normalizeEndpointField(context.formalBranch);
  const impactInteractionId = normalizeEndpointField(context.impactInteractionId);
  const sourceSessionId = normalizeEndpointField(context.sourceSessionId);
  const sourceConversationThreadId = normalizeEndpointField(context.sourceConversationThreadId);
  const sourceRunId = normalizeEndpointField(context.sourceRunId);
  const planningThreadId = normalizeEndpointField(context.planningThreadId);
  const changeId = normalizeEndpointField(context.changeId);
  const handoffFromSessionId = normalizeEndpointField(context.handoffFromSessionId);
  const handoffFromConversationThreadId = normalizeEndpointField(
    context.handoffFromConversationThreadId,
  );
  const technicalPlanSha256 = normalizeEndpointField(context.technicalPlanSha256);
  if (
    !['design', 'development'].includes(sessionRole || '') ||
    !['design_stage_revision', 'workbench_plan_revision'].includes(formalBranch || '') ||
    !impactInteractionId ||
    !sourceSessionId ||
    !sourceConversationThreadId ||
    !sourceRunId ||
    !planningThreadId
  ) {
    return undefined;
  }
  if (
    sessionRole === 'development' &&
    (!changeId ||
      !handoffFromSessionId ||
      !handoffFromConversationThreadId ||
      !technicalPlanSha256 ||
      !/^[0-9a-f]{64}$/.test(technicalPlanSha256))
  ) {
    return undefined;
  }
  return {
    kind: 'formal_revision',
    sessionRole: sessionRole as ChatSessionRevisionContext['sessionRole'],
    formalBranch: formalBranch as WorkflowFormalRevisionBranch,
    impactInteractionId,
    sourceSessionId,
    sourceConversationThreadId,
    sourceRunId,
    planningThreadId,
    ...(changeId ? { changeId } : {}),
    ...(handoffFromSessionId ? { handoffFromSessionId } : {}),
    ...(handoffFromConversationThreadId ? { handoffFromConversationThreadId } : {}),
    ...(technicalPlanSha256 ? { technicalPlanSha256 } : {}),
  };
}

function normalizeSessionWorkspaces(value: unknown): SessionWorkspaceSummary[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Partial<SessionWorkspaceSummary> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      workspaceRoot: String(item.workspaceRoot || ''),
      name: String(item.name || item.workspaceRoot || '未命名工作目录'),
      sessionCount: Number(item.sessionCount || 0),
      frontendCount: Number(item.frontendCount || 0),
      backendCount: Number(item.backendCount || 0),
      latestUpdatedAt: Number(item.latestUpdatedAt || 0),
      latestTitle: String(item.latestTitle || '新对话'),
    }))
    .filter((item) => item.workspaceRoot && item.sessionCount > 0)
    .sort((a, b) => b.latestUpdatedAt - a.latestUpdatedAt);
}

function readFallbackSessions(
  workspaceRoot: string,
  editorMode: EditorMode,
): ChatSessionRecord[] {
  try {
    const rawValue = window.localStorage.getItem(storageKey(workspaceRoot, editorMode));
    if (!rawValue) return [];
    const sessions = JSON.parse(rawValue);
    return Array.isArray(sessions)
      ? sessions.map(normalizeSession).filter((session): session is ChatSessionRecord => Boolean(session))
      : [];
  } catch {
    return [];
  }
}

function writeFallbackSessions(
  workspaceRoot: string,
  editorMode: EditorMode,
  sessions: ChatSessionRecord[],
): void {
  window.localStorage.setItem(storageKey(workspaceRoot, editorMode), JSON.stringify(sessions));
}

export function createChatSessionId(): string {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function createChatSessionTitle(content: string): string {
  const title = content.trim().replace(/\s+/g, ' ');
  if (!title) return '新对话';
  return title.length > 28 ? `${title.slice(0, 28)}...` : title;
}

export function chatSessionToSummary(session: ChatSessionRecord): ChatSessionSummary {
  return toSummary(session);
}

export function canListSessionWorkspaces(): boolean {
  return Boolean(window.xcodeAgent?.sessions?.listWorkspaces || getElectronInvoke());
}

export async function listSessionWorkspaces(): Promise<SessionWorkspaceSummary[]> {
  const sessionApi = window.xcodeAgent?.sessions;
  const legacyInvoke = getElectronInvoke();
  if (!sessionApi?.listWorkspaces && !legacyInvoke) return [];

  try {
    const result = sessionApi?.listWorkspaces
      ? await sessionApi.listWorkspaces()
      : await legacyInvoke?.('sessions:list-workspaces');
    const workspaces =
      result && typeof result === 'object' && 'workspaces' in result
        ? (result as { workspaces?: unknown }).workspaces
        : [];
    return normalizeSessionWorkspaces(workspaces);
  } catch (error) {
    console.warn(error);
    throw error;
  }
}

export async function listChatSessions(
  workspaceRoot: string,
  editorMode: EditorMode,
): Promise<ChatSessionSummary[]> {
  const sessionApi = window.xcodeAgent?.sessions;
  if (sessionApi) {
    try {
      const result = await sessionApi.list({ workspaceRoot, editorMode });
      return normalizeSummaries(result.sessions).sort((a, b) => b.updatedAt - a.updatedAt);
    } catch (error) {
      console.warn(error);
    }
  }

  return readFallbackSessions(workspaceRoot, editorMode)
    .map(toSummary)
    .sort((a, b) => b.updatedAt - a.updatedAt);
}

export async function readChatSession(
  workspaceRoot: string,
  editorMode: EditorMode,
  sessionId: string,
): Promise<ChatSessionRecord> {
  const sessionApi = window.xcodeAgent?.sessions;
  if (sessionApi) {
    try {
      const result = await sessionApi.read({ workspaceRoot, editorMode, sessionId });
      const session = normalizeSession(result.session);
      if (!session) throw new Error('会话文件格式不正确。');
      return session;
    } catch (error) {
      console.warn(error);
    }
  }

  const session = readFallbackSessions(workspaceRoot, editorMode).find((item) => item.id === sessionId);
  if (!session) throw new Error('会话不存在。');
  return session;
}

/** 通过主进程统一创建实际聊天会话，并为前三阶段分配不可变的 Thread 与 sequence。 */
export async function createChatSession(input: CreateChatSessionInput): Promise<ChatSessionRecord> {
  const sessionApi = window.xcodeAgent?.sessions
  if (sessionApi?.create) {
    const result = await sessionApi.create(input)
    const session = normalizeSession(result.session)
    if (!session) throw new Error('主进程返回的会话不符合当前契约。')
    return session
  }

  const now = Date.now()
  const id = createChatSessionId()
  const stage = stageForWorkbenchPhase(input.workbenchPhase)
  const entryKey = stage ? input.entryKey?.trim() || `session:${id}` : undefined
  const existingSessions = [
    ...readFallbackSessions(input.workspaceRoot, 'frontend'),
    ...readFallbackSessions(input.workspaceRoot, 'backend')
  ]
  const existing = stage
    ? existingSessions.find(
        (session) =>
          session.workflowId === input.workflowId &&
          session.stage === stage &&
          session.entryKey === entryKey
      )
    : undefined
  if (existing) {
    const identityMatches =
      existing.editorMode === input.editorMode &&
      existing.workbenchPhase === input.workbenchPhase &&
      existing.targetType === input.targetType &&
      existing.pageId === normalizePageId(input.pageId) &&
      existing.apiContractId === normalizeEndpointField(input.apiContractId) &&
      existing.endpointId === normalizeEndpointField(input.endpointId) &&
      existing.entityId === normalizeEndpointField(input.entityId)
    if (!identityMatches) throw new Error('entryKey 已绑定到另一个会话目标。')
    return existing
  }
  const sequence = stage
    ? Math.max(
        0,
        ...existingSessions
          .filter((session) => session.workflowId === input.workflowId && session.stage === stage)
          .map((session) => Number(session.sequence || 0))
      ) + 1
    : undefined
  const candidate = normalizeSession({
    ...input,
    id,
    threadId: createChatSessionId(),
    ...(stage ? { stage, sequence, entryKey } : {}),
    title: input.title || '新对话',
    messages: [],
    createdAt: now,
    updatedAt: now
  })
  if (!candidate) throw new Error('会话目标与阶段身份不符合当前契约。')
  const sessions = readFallbackSessions(input.workspaceRoot, input.editorMode)
  writeFallbackSessions(input.workspaceRoot, input.editorMode, [candidate, ...sessions])
  return candidate
}

export async function saveChatSession(session: ChatSessionRecord): Promise<ChatSessionSummary> {
  const sessionApi = window.xcodeAgent?.sessions
  if (sessionApi) {
    const result = await sessionApi.save({
      workspaceRoot: session.workspaceRoot,
      session
    })
    const summary = normalizeSummaries([result.session])[0]
    if (!summary) throw new Error('主进程返回的会话摘要不符合当前契约。')
    return summary
  }

  const sessions = readFallbackSessions(session.workspaceRoot, session.editorMode)
  const nextSessions = [session, ...sessions.filter((item) => item.id !== session.id)].sort(
    (a, b) => b.updatedAt - a.updatedAt
  )
  writeFallbackSessions(session.workspaceRoot, session.editorMode, nextSessions)
  return toSummary(session)
}

export async function deleteChatSession(
  workspaceRoot: string,
  editorMode: EditorMode,
  sessionId: string,
): Promise<void> {
  const sessionApi = window.xcodeAgent?.sessions;
  if (sessionApi) {
    await sessionApi.delete({ workspaceRoot, editorMode, sessionId });
    return;
  }

  const sessions = readFallbackSessions(workspaceRoot, editorMode);
  writeFallbackSessions(
    workspaceRoot,
    editorMode,
    sessions.filter((item) => item.id !== sessionId),
  );
}
