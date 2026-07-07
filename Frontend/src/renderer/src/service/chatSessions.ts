import type {
  AgentApprovalRequest,
  AgentApprovalStatus,
  DevelopmentOrchestrationPayload,
  EditorMode,
  WorkspaceCodeChangeSet,
} from '../typings';

export type ChatSessionMessage = {
  id: number;
  role: 'user' | 'assistant';
  content: string;
  orchestration?: DevelopmentOrchestrationPayload;
  approval?: AgentApprovalRequest;
  approvalStatus?: AgentApprovalStatus;
  codeChanges?: WorkspaceCodeChangeSet;
  createdAt: number;
};

export type ChatSessionRecord = {
  id: string;
  title: string;
  editorMode: EditorMode;
  threadId: string;
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

function storageKey(workspaceRoot: string, editorMode: EditorMode) {
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

function normalizeSession(value: unknown): ChatSessionRecord | null {
  if (!value || typeof value !== 'object') return null;
  const session = value as Partial<ChatSessionRecord>;
  if (!session.id || !session.editorMode || !session.threadId) return null;
  return {
    id: String(session.id),
    title: String(session.title || '新对话'),
    editorMode: session.editorMode,
    threadId: String(session.threadId),
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
      createdAt: Number(item.createdAt || Date.now()),
      updatedAt: Number(item.updatedAt || Date.now()),
      messageCount: Number(item.messageCount || 0),
    }))
    .filter((item) => item.id);
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

function readFallbackSessions(workspaceRoot: string, editorMode: EditorMode) {
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
) {
  window.localStorage.setItem(storageKey(workspaceRoot, editorMode), JSON.stringify(sessions));
}

export function createChatSessionId() {
  if (window.crypto?.randomUUID) return window.crypto.randomUUID();
  return `${Date.now()}-${Math.random().toString(36).slice(2, 10)}`;
}

export function createChatSessionTitle(content: string) {
  const title = content.trim().replace(/\s+/g, ' ');
  if (!title) return '新对话';
  return title.length > 28 ? `${title.slice(0, 28)}...` : title;
}

export function chatSessionToSummary(session: ChatSessionRecord) {
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

export async function listChatSessions(workspaceRoot: string, editorMode: EditorMode) {
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
) {
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

export async function saveChatSession(session: ChatSessionRecord) {
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
) {
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
