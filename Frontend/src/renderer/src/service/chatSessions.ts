import type {
  AgentApprovalRequest,
  AgentApprovalStatus,
  DevelopmentOrchestrationPayload,
  EditorMode,
  ChatMessageSkill,
  WorkflowBuildExecutionSlice,
  WorkflowRunPayload,
  WorkspaceCodeChangeSet,
} from '../typings';
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
  toolCalls?: ToolCallRecord[];
  processSteps?: ProcessStepRecord[];
  createdAt: number;
};

export type ChatSessionRecord = {
  id: string;
  title: string;
  editorMode: EditorMode;
  threadId: string;
  apiContractId?: string;
  endpointId?: string;
  endpointLabel?: string;
  pageId?: string;
  workspaceRoot: string;
  messages: ChatSessionMessage[];
  createdAt: number;
  updatedAt: number;
};

export type ChatSessionSummary = {
  id: string;
  title: string;
  editorMode: EditorMode;
  threadId: string;
  apiContractId?: string;
  endpointId?: string;
  endpointLabel?: string;
  pageId?: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
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

type ElectronInvoke = (channel: string, ...args: unknown[]) => Promise<unknown>;

function storageKey(workspaceRoot: string, editorMode: EditorMode): string {
  return `xcode-agent-sessions:${workspaceRoot}:${editorMode}`;
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
      toolCalls: normalizeToolCalls(item.toolCalls),
      processSteps: normalizeProcessSteps(item.processSteps),
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

function normalizeSession(value: unknown): ChatSessionRecord | null {
  if (!value || typeof value !== 'object') return null;
  const session = value as Partial<ChatSessionRecord>;
  if (!session.id || !session.editorMode || !session.threadId) return null;
  const endpointContext = inferEndpointContextFromMessages(session.messages);
  return {
    id: String(session.id),
    title: String(session.title || '新对话'),
    editorMode: session.editorMode,
    threadId: String(session.threadId),
    apiContractId:
      normalizeEndpointField(session.apiContractId) ||
      endpointContext?.apiContractId,
    endpointId:
      normalizeEndpointField(session.endpointId) ||
      endpointContext?.endpointId,
    endpointLabel:
      normalizeEndpointField(session.endpointLabel) ||
      endpointContext?.endpointLabel ||
      inferEndpointLabelFromTitle(session.title),
    pageId: normalizePageId(session.pageId) || inferPageIdFromMessages(session.messages),
    workspaceRoot: String(session.workspaceRoot || ''),
    messages: normalizeMessages(session.messages),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
  };
}

function toSummary(session: ChatSessionRecord): ChatSessionSummary {
  return {
    id: session.id,
    title: session.title,
    editorMode: session.editorMode,
    threadId: session.threadId,
    apiContractId: session.apiContractId,
    endpointId: session.endpointId,
    endpointLabel: session.endpointLabel,
    pageId: session.pageId,
    createdAt: session.createdAt,
    updatedAt: session.updatedAt,
    messageCount: session.messages.length,
  };
}

function normalizeSummaries(value: unknown): ChatSessionSummary[] {
  if (!Array.isArray(value)) return [];
  return value
    .filter((item): item is Partial<ChatSessionSummary> => Boolean(item && typeof item === 'object'))
    .map((item) => ({
      id: String(item.id || ''),
      title: String(item.title || '新对话'),
      editorMode: item.editorMode || 'frontend',
      threadId: String(item.threadId || item.id || ''),
      apiContractId: normalizeEndpointField(item.apiContractId),
      endpointId: normalizeEndpointField(item.endpointId),
      endpointLabel: normalizeEndpointField(item.endpointLabel),
      pageId: normalizePageId(item.pageId),
      createdAt: Number(item.createdAt || Date.now()),
      updatedAt: Number(item.updatedAt || Date.now()),
      messageCount: Number(item.messageCount || 0),
    }))
    .filter((item) => item.id);
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

/** 从旧会话保存的 Workflow 快照中恢复页面归属。 */
function inferPageIdFromMessages(value: unknown): string | undefined {
  if (!Array.isArray(value)) return undefined;
  for (let index = value.length - 1; index >= 0; index -= 1) {
    const message = value[index];
    if (!message || typeof message !== 'object') continue;
    const workflow = (message as { workflow?: unknown }).workflow;
    if (!workflow || typeof workflow !== 'object') continue;
    const payload = workflow as {
      state?: { selectedPageId?: unknown };
      result?: { selectedPageId?: unknown };
    };
    const pageId = normalizePageId(payload.state?.selectedPageId)
      || normalizePageId(payload.result?.selectedPageId);
    if (pageId) return pageId;
  }
  return undefined;
}

/** 从旧会话保存的 Workflow 快照中恢复 API endpoint 归属。 */
function inferEndpointContextFromMessages(value: unknown): {
  apiContractId?: string;
  endpointId?: string;
  endpointLabel?: string;
} | undefined {
  if (!Array.isArray(value)) return undefined;
  for (let index = value.length - 1; index >= 0; index -= 1) {
    const message = value[index];
    if (!message || typeof message !== 'object') continue;
    const workflow = (message as { workflow?: unknown }).workflow;
    if (!workflow || typeof workflow !== 'object') continue;
    const payload = workflow as {
      state?: Record<string, unknown>;
      result?: Record<string, unknown>;
      summary?: { clarification?: { review?: { summary?: Record<string, unknown> } } };
    };
    const reviewSummary = payload.summary?.clarification?.review?.summary;
    const apiContractId = normalizeEndpointField(
      payload.state?.selectedApiContractId ||
      payload.result?.selectedApiContractId ||
      reviewSummary?.selectedApiContractId
    );
    const endpointId = normalizeEndpointField(
      payload.state?.selectedEndpointId ||
      payload.result?.selectedEndpointId ||
      reviewSummary?.selectedEndpointId
    );
    if (apiContractId && endpointId) {
      return { apiContractId, endpointId };
    }
  }
  return undefined;
}

/** 从会话标题中恢复接口展示名，兼容旧的“设计接口：METHOD path”标题。 */
function inferEndpointLabelFromTitle(value: unknown): string | undefined {
  const title = typeof value === 'string' ? value.trim() : '';
  const matched = title.match(/(?:设计接口|确认接口|开始设计接口|查看已生成接口计划)：(.+)$/);
  return matched?.[1]?.trim() || undefined;
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

export async function saveChatSession(session: ChatSessionRecord): Promise<ChatSessionSummary> {
  const sessionApi = window.xcodeAgent?.sessions;
  if (sessionApi) {
    try {
      const result = await sessionApi.save({
        workspaceRoot: session.workspaceRoot,
        session,
      });
      return normalizeSummaries([result.session])[0] ?? toSummary(session);
    } catch (error) {
      console.warn(error);
    }
  }

  const sessions = readFallbackSessions(session.workspaceRoot, session.editorMode);
  const nextSessions = [
    session,
    ...sessions.filter((item) => item.id !== session.id),
  ].sort((a, b) => b.updatedAt - a.updatedAt);
  writeFallbackSessions(session.workspaceRoot, session.editorMode, nextSessions);
  return toSummary(session);
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
