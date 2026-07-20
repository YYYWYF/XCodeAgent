import { app, shell, BrowserWindow, ipcMain, dialog, Menu, Tray } from 'electron'
import { join } from 'path'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import icon from '../../resources/icon.png?asset'
import { XCODE_AGENT_ENV } from './env'
import { getBackendBaseUrl, startBackendService, stopBackendService } from './backendService'
import {
  clearAuthState,
  ensureXcodeAgentDataDir,
  getAccessToken,
  getXcodeAgentDataDir,
  hasValidAuthToken,
  loginWithCmbDeviceFlow
} from './auth'

let mainWindow: BrowserWindow | null = null;
let loginWindow: BrowserWindow | null = null
let tray: Tray | null = null
let isQuitting = false
const previewWindows = new Set<BrowserWindow>();
const hasSingleInstanceLock = app.requestSingleInstanceLock()
let primaryStartupPromise: Promise<boolean> | null = null

/** 返回 Electron 用户数据目录中的应用列表文件路径。 */
function getApplicationsFile(): string {
  return path.join(app.getPath('userData'), 'applications.json');
}

/** 返回工作区元数据目录中的应用配置文件路径。 */
function getWorkspaceApplicationFile(workspaceRoot: string): string {
  return path.join(workspaceRoot, '.xcodeagent', 'application.json');
}

type WorkbenchPageOption = {
  key: string;
  pageId: string;
  label: string;
  path: string;
  purpose: string;
  designed: boolean;
  detailPlanStatus?: string;
  hasDetailPlan: boolean;
};

type WorkbenchApiContract = {
  id: string;
  label: string;
  endpoints: Array<{ id: string; method: string; path: string; summary: string }>;
};

/** 将 API 路径统一为带前导斜杠的目录形式。 */
function normalizeApiPath(value: unknown, fallback = '/api'): string {
  const text = String(value || '').trim();
  if (!text) return fallback;
  return `/${text}`.replace(/\/+/g, '/').replace(/\/$/, '') || '/';
}

/** 将页面 id 转成与后端详情文件一致的安全文件名。 */
function detailFileStem(value: string, prefix: string): string {
  const normalized = value.replace(/[^a-zA-Z0-9_-]+/g, '-').replace(/^[-_]+|[-_]+$/g, '');
  return `${prefix}${normalized || 'unknown'}`;
}

/** 检查选中页面是否已经存在外置详情 JSON。 */
async function pageDetailPlanExists(
  workspaceRoot: string,
  pageId: string,
): Promise<boolean> {
  const detailPath = path.join(
    workspaceRoot,
    '.xcodeagent',
    'plans',
    'pages',
    `${detailFileStem(pageId, 'page--')}.json`,
  );
  try {
    const content = await fs.readFile(detailPath, 'utf8');
    return Boolean(content.trim());
  } catch {
    return false;
  }
}

/** 从 ProjectPlan 中按页面标识收集应用大纲页面。 */
function projectPlanPages(value: unknown): Map<string, Record<string, unknown>> {
  const result = new Map<string, Record<string, unknown>>();
  if (!value || typeof value !== 'object' || Array.isArray(value)) return result;
  const frontendPages = Array.isArray((value as Record<string, unknown>).frontend_pages)
    ? (value as Record<string, unknown>).frontend_pages as unknown[]
    : [];
  frontendPages.forEach((page) => {
    if (!page || typeof page !== 'object' || Array.isArray(page)) return;
    const record = page as Record<string, unknown>;
    const pageId = String(record.id || record.pageId || '').trim();
    if (pageId) result.set(pageId, record);
  });
  return result;
}

/** 从 ProjectPlan 的 frontend_pages 生成工作台页面目录。 */
function projectPlanPageOptions(value: unknown): WorkbenchPageOption[] {
  return [...projectPlanPages(value).entries()].map(([pageId, record], index) => ({
    key: pageId,
    pageId,
    label: String(record.name || pageId),
    path: String(record.path || '/'),
    purpose: String(record.description || record.name || `页面 ${index + 1}`),
    designed: false,
    detailPlanStatus: '',
    hasDetailPlan: false,
  }));
}

/** 按 base_path 合并 ProjectPlan contracts，同一目录下展示所有具体 API。 */
function projectPlanApiContracts(value: unknown): WorkbenchApiContract[] {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return [];
  const contracts = Array.isArray((value as Record<string, unknown>).api_contracts)
    ? (value as Record<string, unknown>).api_contracts as unknown[]
    : [];
  const groupedContracts = new Map<string, WorkbenchApiContract>();
  contracts.forEach((contract, contractIndex) => {
    if (!contract || typeof contract !== 'object' || Array.isArray(contract)) return;
    const record = contract as Record<string, unknown>;
    const contractId = String(record.id || `api-contract-${contractIndex + 1}`).trim();
    const basePath = normalizeApiPath(record.base_path, `/${contractId}`);
    const endpoints = Array.isArray(record.endpoints) ? record.endpoints : [];
    const group = groupedContracts.get(basePath) || {
      id: basePath,
      label: basePath,
      endpoints: [],
    };
    group.endpoints.push(
      ...endpoints.flatMap((endpoint, endpointIndex) => {
        if (!endpoint || typeof endpoint !== 'object' || Array.isArray(endpoint)) return [];
        const endpointRecord = endpoint as Record<string, unknown>;
        const method = String(endpointRecord.method || 'GET').trim().toUpperCase();
        const endpointPath = String(endpointRecord.path || '').trim();
        if (!endpointPath) return [];
        return [{
          id: `${contractId}:${String(endpointRecord.id || endpointIndex + 1)}`,
          method,
          path: normalizeApiPath(endpointPath, '/'),
          summary: String(endpointRecord.summary || ''),
        }];
      })
    );
    groupedContracts.set(basePath, group);
  });
  return [...groupedContracts.values()];
}

/** 只根据外置页面详情文件补充每个页面的设计状态。 */
async function mergeWorkbenchPageStatus(
  workspaceRoot: string,
  pages: WorkbenchPageOption[],
  plannedPages: Map<string, Record<string, unknown>>
): Promise<WorkbenchPageOption[]> {
  return Promise.all(pages.map(async (page) => {
    const plannedPage = plannedPages.get(page.pageId);
    const detailDesign = plannedPage?.detail_design
      && typeof plannedPage.detail_design === 'object'
      && !Array.isArray(plannedPage.detail_design)
      ? plannedPage.detail_design as Record<string, unknown>
      : {};
    const hasDetailPlan = await pageDetailPlanExists(workspaceRoot, page.pageId);
    return {
      ...page,
      designed: hasDetailPlan,
      detailPlanStatus: String(detailDesign.status || ''),
      hasDetailPlan,
    };
  }));
}

/** 判断页面设计目录是否包含任意持久化产物。 */
async function pageDesignDirectoryHasEntries(workspaceRoot: string): Promise<boolean> {
  try {
    const entries = await fs.readdir(path.join(workspaceRoot, '.xcodeagent', 'plans', 'pages'));
    return entries.length > 0;
  } catch (error: unknown) {
    const errnoException = error as NodeJS.ErrnoException;
    if (errnoException?.code === 'ENOENT') return false;
    throw error;
  }
}

/** 校验正式规划产物，并从 ProjectPlan 投射应用大纲与页面设计状态。 */
async function inspectWorkspacePlanningArtifacts(workspaceRoot: string): Promise<{
  ready: boolean;
  hasPageDesigns: boolean;
  missing: string[];
  invalid: string[];
  pages: WorkbenchPageOption[];
  apiContracts: WorkbenchApiContract[];
}> {
  const artifactRoot = path.join(workspaceRoot, '.xcodeagent');
  const artifacts = [
    { relativePath: 'specs/requirement-spec.md', format: 'markdown' },
    { relativePath: 'specs/requirement-spec.json', format: 'json' },
    { relativePath: 'plans/project-plan.md', format: 'markdown' },
  ];
  const missing: string[] = [];
  const invalid: string[] = [];
  let plannedPages = new Map<string, Record<string, unknown>>();
  let apiContracts: WorkbenchApiContract[] = [];
  let projectPlanLoaded = false;

  for (const artifact of artifacts) {
    const artifactPath = path.join(artifactRoot, artifact.relativePath);
    try {
      const content = await fs.readFile(artifactPath, 'utf8');
      if (!content.trim()) {
        invalid.push(artifact.relativePath);
        continue;
      }
      if (artifact.format === 'json') {
        const value = JSON.parse(content);
        if (!value || typeof value !== 'object' || Array.isArray(value) || value.confirmation_status !== 'confirmed') {
          invalid.push(artifact.relativePath);
        }
      }
    } catch (error: unknown) {
      const errnoException = error as NodeJS.ErrnoException;
      if (errnoException?.code === 'ENOENT') missing.push(artifact.relativePath);
      else invalid.push(artifact.relativePath);
    }
  }

  // 新工作区以 plans/project_plan.json 为大纲唯一语义来源，并兼容既有连字符文件名。
  for (const relativePath of ['plans/project_plan.json', 'plans/project-plan.json']) {
    try {
      const content = await fs.readFile(path.join(artifactRoot, relativePath), 'utf8');
      const projectPlan = JSON.parse(content);
      if (
        !projectPlan
        || typeof projectPlan !== 'object'
        || Array.isArray(projectPlan)
        || projectPlan.confirmation_status !== 'confirmed'
      ) {
        invalid.push(relativePath);
      }
      plannedPages = projectPlanPages(projectPlan);
      apiContracts = projectPlanApiContracts(projectPlan);
      projectPlanLoaded = true;
      break;
    } catch (error: unknown) {
      const errnoException = error as NodeJS.ErrnoException;
      if (errnoException?.code === 'ENOENT') continue;
      invalid.push(relativePath);
      projectPlanLoaded = true;
      break;
    }
  }
  if (!projectPlanLoaded) missing.push('plans/project_plan.json');

  const pages = await mergeWorkbenchPageStatus(
    workspaceRoot,
    projectPlanPageOptions({ frontend_pages: [...plannedPages.values()] }),
    plannedPages,
  );
  const hasPageDesigns = await pageDesignDirectoryHasEntries(workspaceRoot);

  return {
    ready: missing.length === 0 && invalid.length === 0,
    hasPageDesigns,
    missing,
    invalid,
    pages,
    apiContracts,
  };
}

type EditorMode = 'frontend' | 'backend'

type SessionWorkspaceSummary = {
  workspaceRoot: string
  name: string
  sessionCount: number
  frontendCount: number
  backendCount: number
  latestUpdatedAt: number
  latestTitle: string
}

type ChatSessionSummary = {
  id: string
  title: string
  editorMode: EditorMode
  threadId: string
  pageId?: string
  createdAt: number
  updatedAt: number
  messageCount: number
}

type JsonRecord = Record<string, unknown>

type NormalizedChatSession = {
  id: string
  title: string
  editorMode: EditorMode
  threadId: string
  pageId?: string
  createdAt: number
  updatedAt: number
  workspaceRoot: string
  messages: JsonRecord[]
}

const MESSAGE_APPROVAL_STATUSES = new Set([
  'pending',
  'approved_once',
  'approved_always',
  'feedback',
])

/** 返回随应用发布的初始应用列表文件路径。 */
function getSeedApplicationsFile(): string {
  return path.join(__dirname, '..', 'data', 'applications.json');
}

/** 确保应用列表文件存在，并在首次运行时写入种子数据。 */
async function ensureApplicationsFile(): Promise<string> {
  const applicationsFile = getApplicationsFile();
  await fs.mkdir(path.dirname(applicationsFile), { recursive: true });

  try {
    await fs.access(applicationsFile);
    return applicationsFile;
  } catch {
    // Continue and seed the file below.
  }

  let seedValue = '[]\n';
  try {
    seedValue = await fs.readFile(getSeedApplicationsFile(), 'utf8');
  } catch {
    // Keep an empty store when the seed file is not shipped.
  }

  await fs.writeFile(applicationsFile, seedValue, 'utf8');
  return applicationsFile;
}

/** 读取持久化应用列表，非数组内容按空列表处理。 */
async function readApplications(): Promise<unknown[]> {
  const applicationsFile = await ensureApplicationsFile();
  const rawValue = await fs.readFile(applicationsFile, 'utf8');
  const parsed: unknown = JSON.parse(rawValue || '[]');
  return Array.isArray(parsed) ? parsed : [];
}

/** 校验并持久化应用列表。 */
async function writeApplications(applications: unknown): Promise<void> {
  if (!Array.isArray(applications)) {
    throw new Error('applications must be an array');
  }

  const applicationsFile = await ensureApplicationsFile();
  await fs.writeFile(applicationsFile, `${JSON.stringify(applications, null, 2)}\n`, 'utf8');
}

/** 仅删除带有 XCodeAgent 项目标识的安全工作区目录。 */
async function deleteProjectDirectory(workspaceRoot: unknown): Promise<void> {
  const projectRoot = resolveWorkspaceRoot(workspaceRoot);
  const protectedRoots = new Set([
    path.parse(projectRoot).root,
    path.resolve(app.getPath('home')),
    path.resolve(app.getPath('userData')),
    path.resolve(getXcodeAgentDataDir()),
  ]);
  if (protectedRoots.has(projectRoot)) {
    throw new Error('不能删除系统、用户或 XCodeAgent 数据目录');
  }

  const projectMetadataFile = getWorkspaceApplicationFile(projectRoot);
  const projectStats = await fs.lstat(projectRoot);
  if (!projectStats.isDirectory() || projectStats.isSymbolicLink()) {
    throw new Error('只能删除非符号链接的项目目录');
  }

  try {
    await fs.access(projectMetadataFile);
  } catch {
    throw new Error('该目录不是由 XCodeAgent 管理的项目，不能直接删除');
  }

  await fs.rm(projectRoot, { force: false, maxRetries: 3, recursive: true, retryDelay: 150 });
}

/** 注册应用列表读取和保存所需的 IPC。 */
function setupApplicationStorageIpc(): void {
  ipcMain.handle('applications:load', async () => ({
    applications: await readApplications(),
  }));

  ipcMain.handle('applications:save', async (_event, applications) => {
    await writeApplications(applications);
    return { ok: true };
  });

  ipcMain.handle('applications:delete-project', async (_event, payload = {}) => {
    await deleteProjectDirectory(payload.workspaceRoot);
    return { ok: true };
  });
}

/** 校验并规范化允许由 Electron 打开的外部 HTTP 地址。 */
function normalizeExternalUrl(url: unknown): string {
  if (typeof url !== 'string') {
    throw new Error('url must be a string');
  }

  const parsedUrl = new URL(url);
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('Only http and https URLs can be opened');
  }

  return parsedUrl.toString();
}

/** 注册系统浏览器和独立预览窗口相关 IPC。 */
function setupBrowserIpc(): void {
  ipcMain.handle('browser:open-external', async (_event, url) => {
    await shell.openExternal(normalizeExternalUrl(url));
    return { ok: true };
  });

  ipcMain.handle('browser:open-preview-window', async (_event, url) => {
    const previewWindow = new BrowserWindow({
      fullscreen: true,
      minWidth: 960,
      minHeight: 640,
      title: '全屏预览',
      backgroundColor: '#ffffff',
      webPreferences: {
        contextIsolation: true,
        nodeIntegration: false,
        sandbox: true,
      },
    });

    previewWindows.add(previewWindow);
    previewWindow.once('closed', () => {
      previewWindows.delete(previewWindow);
    });
    previewWindow.webContents.setWindowOpenHandler(({ url: nextUrl }) => {
      shell.openExternal(nextUrl);
      return { action: 'deny' };
    });
    await previewWindow.loadURL(normalizeExternalUrl(url));
    previewWindow.show();
    return { ok: true };
  });
}

/** 校验并返回支持的编辑器模式。 */
function assertEditorMode(value: unknown): EditorMode {
  if (value !== 'frontend' && value !== 'backend') {
    throw new Error('editorMode must be frontend or backend');
  }
  return value;
}

/** 校验并返回可安全用于文件名的会话标识。 */
function assertSessionId(value: unknown): string {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error('sessionId contains invalid characters');
  }
  return value;
}

/** 校验并解析工作区绝对路径。 */
function resolveWorkspaceRoot(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('workspaceRoot must be a non-empty string');
  }
  return path.resolve(value);
}

/** 根据工作区名称和绝对路径哈希生成稳定的会话目录键。 */
function getWorkspaceSessionKey(workspaceRoot: unknown): string {
  const resolvedWorkspaceRoot = resolveWorkspaceRoot(workspaceRoot);
  const workspaceName =
    path.basename(resolvedWorkspaceRoot).replace(/[^A-Za-z0-9._-]/g, '-').slice(0, 80) || 'workspace';
  const workspaceHash = crypto
    .createHash('sha1')
    .update(resolvedWorkspaceRoot)
    .digest('hex')
    .slice(0, 12);
  return `${workspaceName}-${workspaceHash}`;
}

/** 返回指定工作区在环境数据目录中的会话根目录。 */
function getWorkspaceSessionRoot(workspaceRoot: unknown): string {
  return path.join(getXcodeAgentDataDir(), 'sessions', getWorkspaceSessionKey(workspaceRoot));
}

/** 返回当前环境全部会话工作区的存储根目录。 */
function getSessionStorageRoot(): string {
  return path.join(getXcodeAgentDataDir(), 'sessions');
}

/** 返回指定工作区和编辑器模式对应的会话目录。 */
function getSessionsDir(workspaceRoot: unknown, editorMode: unknown): string {
  return path.join(getWorkspaceSessionRoot(workspaceRoot), assertEditorMode(editorMode));
}

/** 返回校验后的单个会话文件路径。 */
function getSessionFile(workspaceRoot: unknown, editorMode: unknown, sessionId: unknown): string {
  return path.join(getSessionsDir(workspaceRoot, editorMode), `${assertSessionId(sessionId)}.json`);
}

/** 返回旧版工作区内会话目录，用于兼容迁移。 */
function getLegacyWorkspaceSessionsDir(workspaceRoot: unknown, editorMode: unknown): string {
  return path.join(
    resolveWorkspaceRoot(workspaceRoot),
    XCODE_AGENT_ENV.WORKING_DIR,
    'sessions',
    assertEditorMode(editorMode)
  )
}

/** 创建环境级会话目录并更新其工作区元数据。 */
async function ensureSessionsDir(workspaceRoot: unknown, editorMode: unknown): Promise<string> {
  const resolvedWorkspaceRoot = resolveWorkspaceRoot(workspaceRoot);
  const workspaceSessionRoot = getWorkspaceSessionRoot(resolvedWorkspaceRoot);
  const sessionsDir = path.join(workspaceSessionRoot, assertEditorMode(editorMode));
  await fs.mkdir(sessionsDir, { recursive: true });
  await fs.writeFile(
    path.join(workspaceSessionRoot, 'workspace.json'),
    `${JSON.stringify({ workspaceRoot: resolvedWorkspaceRoot, updatedAt: Date.now() }, null, 2)}\n`,
    'utf8',
  );
  return sessionsDir;
}

/** 将旧版工作区内的有效会话按需迁移到环境级存储。 */
async function migrateLegacyWorkspaceSessions(
  workspaceRoot: unknown,
  editorMode: unknown,
  sessionsDir: string
): Promise<void> {
  const legacySessionsDir = getLegacyWorkspaceSessionsDir(workspaceRoot, editorMode);
  if (path.resolve(legacySessionsDir) === path.resolve(sessionsDir)) return;

  let entries;
  try {
    entries = await fs.readdir(legacySessionsDir, { withFileTypes: true });
  } catch {
    return;
  }

  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue;

    try {
      const rawValue = await fs.readFile(path.join(legacySessionsDir, entry.name), 'utf8');
      const session = normalizeSession(JSON.parse(rawValue || '{}'));
      const targetFile = getSessionFile(workspaceRoot, editorMode, session.id);
      try {
        await fs.access(targetFile);
        continue;
      } catch {
        // Missing target files are copied into the app-owned environment store below.
      }
      await fs.writeFile(targetFile, `${JSON.stringify(session, null, 2)}\n`, 'utf8');
    } catch {
      // Ignore malformed legacy files so migration never blocks the current app store.
    }
  }
}

/** 将规范化会话转换为列表展示所需的摘要。 */
function sessionSummary(session: NormalizedChatSession): ChatSessionSummary {
  const messages = Array.isArray(session.messages) ? session.messages : [];
  return {
    id: String(session.id || ''),
    title: String(session.title || '新对话'),
    editorMode: assertEditorMode(session.editorMode),
    threadId: String(session.threadId || ''),
    pageId: session.pageId,
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
    messageCount: messages.length,
  };
}

/** 深拷贝可序列化 JSON 对象，无法序列化时返回空值。 */
function cloneJsonRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined

  try {
    return JSON.parse(JSON.stringify(value)) as Record<string, unknown>
  } catch {
    return undefined
  }
}

/** 判断未知值是否为非数组 JSON 对象。 */
function isJsonRecord(value: unknown): value is JsonRecord {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

/** 规范化单条会话消息，并保留受支持的扩展字段。 */
function normalizeSessionMessage(message: JsonRecord): Record<string, unknown> {
  const normalizedMessage = {
    id: Number(message.id || Date.now()),
    role: message.role === 'assistant' ? 'assistant' : 'user',
    content: String(message.content || ''),
    createdAt: Number(message.createdAt || Date.now()),
  }
  const orchestration = cloneJsonRecord(message.orchestration)
  const approval = cloneJsonRecord(message.approval)
  const codeChanges = cloneJsonRecord(message.codeChanges)
  const workflow = cloneJsonRecord(message.workflow)
  const skills = normalizeSessionMessageSkills(message.skills)
  const approvalStatus =
    typeof message.approvalStatus === 'string' &&
    MESSAGE_APPROVAL_STATUSES.has(message.approvalStatus)
      ? message.approvalStatus
      : undefined

  return {
    ...normalizedMessage,
    ...(orchestration ? { orchestration } : {}),
    ...(approval ? { approval } : {}),
    ...(approvalStatus ? { approvalStatus } : {}),
    ...(codeChanges ? { codeChanges } : {}),
    ...(workflow ? { workflow } : {}),
    ...(skills.length > 0 ? { skills } : {}),
  }
}

/** 规范化用户消息携带的技能名称和描述快照。 */
function normalizeSessionMessageSkills(value: unknown): Array<Record<string, string>> {
  if (!Array.isArray(value)) return []
  const names = new Set<string>()
  return value
    .filter(isJsonRecord)
    .map((item) => ({
      name: typeof item.name === 'string' ? item.name.trim() : '',
      description: typeof item.description === 'string' ? item.description.trim() : '',
    }))
    .filter((item) => {
      if (!item.name || names.has(item.name)) return false
      names.add(item.name)
      return true
    })
}

/** 校验外部会话数据并转换为可持久化的统一结构。 */
function normalizeSession(session: unknown): NormalizedChatSession {
  if (!isJsonRecord(session)) {
    throw new Error('session must be an object');
  }

  const editorMode = assertEditorMode(session.editorMode);
  const id = assertSessionId(session.id);
  const messages = Array.isArray(session.messages)
    ? session.messages
        .filter(isJsonRecord)
        .map(normalizeSessionMessage)
    : [];

  const pageId = normalizeSessionPageId(session.pageId) || inferSessionPageId(messages)
  return {
    id,
    title: String(session.title || '新对话'),
    editorMode,
    threadId: String(session.threadId || id),
    ...(pageId ? { pageId } : {}),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
    workspaceRoot: typeof session.workspaceRoot === 'string' ? session.workspaceRoot : '',
    messages,
  };
}

/** 规范化页面会话标识，避免空字符串污染持久化索引。 */
function normalizeSessionPageId(value: unknown): string | undefined {
  const pageId = typeof value === 'string' ? value.trim() : ''
  return pageId || undefined
}

/** 从旧版消息中的 Workflow 状态快照推断页面会话归属。 */
function inferSessionPageId(messages: JsonRecord[]): string | undefined {
  for (let index = messages.length - 1; index >= 0; index -= 1) {
    const workflow = messages[index].workflow
    if (!isJsonRecord(workflow)) continue
    const state = isJsonRecord(workflow.state) ? workflow.state : undefined
    const result = isJsonRecord(workflow.result) ? workflow.result : undefined
    const pageId = normalizeSessionPageId(state?.selectedPageId)
      || normalizeSessionPageId(result?.selectedPageId)
    if (pageId) return pageId
  }
  return undefined
}

async function readSessionSummariesFromDir(
  sessionsDir: string,
  editorMode: EditorMode
): Promise<ChatSessionSummary[]> {
  let entries;
  try {
    entries = await fs.readdir(sessionsDir, { withFileTypes: true });
  } catch {
    return [];
  }

  const sessions: ChatSessionSummary[] = [];
  for (const entry of entries) {
    if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
    try {
      const rawValue = await fs.readFile(path.join(sessionsDir, entry.name), 'utf8');
      const session = normalizeSession(JSON.parse(rawValue || '{}'));
      if (session.editorMode !== editorMode) continue;
      sessions.push(sessionSummary(session));
    } catch {
      // Ignore malformed session files so one bad record does not hide the workspace.
    }
  }
  return sessions;
}

async function listSessionWorkspaces(): Promise<SessionWorkspaceSummary[]> {
  const sessionsRoot = getSessionStorageRoot();
  let entries;
  try {
    entries = await fs.readdir(sessionsRoot, { withFileTypes: true });
  } catch {
    return [];
  }

  const workspaces: SessionWorkspaceSummary[] = [];
  for (const entry of entries) {
    if (!entry.isDirectory()) continue;

    const workspaceSessionRoot = path.join(sessionsRoot, entry.name);
    let workspaceRoot: string;
    try {
      const rawWorkspace = await fs.readFile(path.join(workspaceSessionRoot, 'workspace.json'), 'utf8');
      const workspaceRecord = JSON.parse(rawWorkspace || '{}');
      workspaceRoot = resolveWorkspaceRoot(workspaceRecord.workspaceRoot);
    } catch {
      continue;
    }

    const frontendSessions = await readSessionSummariesFromDir(
      path.join(workspaceSessionRoot, 'frontend'),
      'frontend',
    );
    const backendSessions = await readSessionSummariesFromDir(
      path.join(workspaceSessionRoot, 'backend'),
      'backend',
    );
    const allSessions = [...frontendSessions, ...backendSessions].sort(
      (a, b) => b.updatedAt - a.updatedAt,
    );
    if (allSessions.length === 0) continue;

    const latestSession = allSessions[0];
    workspaces.push({
      workspaceRoot,
      name: path.basename(workspaceRoot) || workspaceRoot,
      sessionCount: allSessions.length,
      frontendCount: frontendSessions.length,
      backendCount: backendSessions.length,
      latestUpdatedAt: latestSession.updatedAt,
      latestTitle: latestSession.title,
    });
  }

  return workspaces.sort((a, b) => b.latestUpdatedAt - a.latestUpdatedAt);
}

/** 注册工作区读取、选择和项目创建相关 IPC。 */
function setupWorkspaceIpc(): void {
  ipcMain.handle('workspace:inspect-planning-artifacts', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot);
    return inspectWorkspacePlanningArtifacts(workspaceRoot);
  });

  ipcMain.handle('workspace:read-application', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot);
    const applicationFile = getWorkspaceApplicationFile(workspaceRoot);
    const rawValue = await fs.readFile(applicationFile, 'utf8');
    const applicationConfig = JSON.parse(rawValue || '{}');

    if (
      !applicationConfig ||
      typeof applicationConfig !== 'object' ||
      Array.isArray(applicationConfig)
    ) {
      throw new Error('application.json must be an object');
    }

    return { application: applicationConfig };
  });

  ipcMain.handle('workspace:select-directory', async (_event, options = {}) => {
    const result = await dialog.showOpenDialog(mainWindow!, {
      title: typeof options.title === 'string' ? options.title : '选择工作目录',
      properties: ['openDirectory', 'createDirectory'],
    });

    return {
      canceled: result.canceled,
      path: result.filePaths[0],
    };
  });

  ipcMain.handle('workspace:create-project-directory', async (_event, payload = {}) => {
    if (typeof payload.workspacePath !== 'string' || !payload.workspacePath.trim()) {
      throw new Error('workspacePath must be a non-empty string');
    }
    if (
      !payload.applicationConfig ||
      typeof payload.applicationConfig !== 'object' ||
      Array.isArray(payload.applicationConfig)
    ) {
      throw new Error('applicationConfig must be an object');
    }

    const projectPath = path.resolve(payload.workspacePath);

    try {
      await fs.mkdir(projectPath, { recursive: false });
    } catch (error: unknown) {
      const errnoException = error as NodeJS.ErrnoException;
      if (errnoException?.code !== 'EEXIST') throw error;
    }
    const applicationFile = getWorkspaceApplicationFile(projectPath);
    await fs.mkdir(path.dirname(applicationFile), { recursive: true });
    await fs.writeFile(
      applicationFile,
      `${JSON.stringify(payload.applicationConfig, null, 2)}\n`,
      'utf8',
    );

    return {
      ok: true,
      path: projectPath,
    };
  });
}

function setupSessionStorageIpc(): void {
  ipcMain.handle('sessions:list-workspaces', async () => ({
    workspaces: await listSessionWorkspaces(),
  }));

  ipcMain.handle('sessions:list', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot);
    const editorMode = assertEditorMode(payload.editorMode);
    const sessionsDir = await ensureSessionsDir(workspaceRoot, editorMode);
    await migrateLegacyWorkspaceSessions(workspaceRoot, editorMode, sessionsDir);

    const entries = await fs.readdir(sessionsDir, { withFileTypes: true });
    const sessions: ChatSessionSummary[] = [];
    for (const entry of entries) {
      if (!entry.isFile() || !entry.name.endsWith('.json')) continue;
      try {
        const rawValue = await fs.readFile(path.join(sessionsDir, entry.name), 'utf8');
        const session = normalizeSession(JSON.parse(rawValue || '{}'));
        sessions.push(sessionSummary(session));
      } catch {
        // Ignore malformed session files so one bad record does not hide the full history.
      }
    }

    sessions.sort((a, b) => b.updatedAt - a.updatedAt);
    return { sessions };
  });

  ipcMain.handle('sessions:read', async (_event, payload = {}) => {
    const sessionFile = getSessionFile(payload.workspaceRoot, payload.editorMode, payload.sessionId);
    const rawValue = await fs.readFile(sessionFile, 'utf8');
    return { session: normalizeSession(JSON.parse(rawValue || '{}')) };
  });

  ipcMain.handle('sessions:save', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot);
    const session = normalizeSession({
      ...payload.session,
      workspaceRoot,
    });
    await ensureSessionsDir(workspaceRoot, session.editorMode);
    await fs.writeFile(
      getSessionFile(workspaceRoot, session.editorMode, session.id),
      `${JSON.stringify(session, null, 2)}\n`,
      'utf8',
    );
    return { ok: true, session: sessionSummary(session) };
  });

  ipcMain.handle('sessions:delete', async (_event, payload = {}) => {
    const sessionFile = getSessionFile(payload.workspaceRoot, payload.editorMode, payload.sessionId);
    await fs.rm(sessionFile, { force: true });
    return { ok: true };
  });
}

/** 注册登录、内存 token 读取和重新认证所需的 Electron IPC。 */
function setupAuthIpc(): void {
  ipcMain.handle('auth:status', async () => ({
    authenticated: hasValidAuthToken()
  }))

  ipcMain.handle('auth:get-access-token', async () => ({
    accessToken: getAccessToken()
  }))

  ipcMain.handle('auth:login', async () => {
    await loginWithCmbDeviceFlow()
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.destroy()
    }
    loginWindow = null
    createMainWindow()
    return { ok: true }
  })

  ipcMain.handle('auth:reauthenticate', async () => {
    await clearAuthState()
    if (mainWindow && !mainWindow.isDestroyed()) {
      mainWindow.hide()
    }
    createLoginWindow()
    return { ok: true }
  })
}

type RendererPage = 'index' | 'login'

function loadRendererPage(targetWindow: BrowserWindow, pageName: RendererPage): void {
  const rendererUrl = process.env['ELECTRON_RENDERER_URL']
  if (rendererUrl) {
    const pageUrl = pageName === 'index' ? rendererUrl : `${rendererUrl.replace(/\/$/, '')}/${pageName}.html`
    void targetWindow.loadURL(pageUrl)
    return
  }

  void targetWindow.loadFile(join(__dirname, `../renderer/${pageName}.html`))
}

function createMainWindow(): void {
  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.show()
    mainWindow.focus()
    return
  }

  mainWindow = new BrowserWindow({
    width: 1440,
    height: 920,
    minWidth: 720,
    minHeight: 600,
    title: 'XCode Agent',
    backgroundColor: '#f5f7fb',
    show: false,
    autoHideMenuBar: true,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--xcode-agent-base-url=${getBackendBaseUrl()}`],
    }
  })

  mainWindow.setMenuBarVisibility(false);
  mainWindow.on('close', (event) => {
    if (isQuitting) return
    event.preventDefault()
    mainWindow?.hide()
  })
  mainWindow.on('closed', () => {
    mainWindow = null
  })
  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  loadRendererPage(mainWindow, 'index')
}

/** 创建或聚焦带有自定义关闭控件的无边框登录窗口。 */
function createLoginWindow(): void {
  if (loginWindow && !loginWindow.isDestroyed()) {
    loginWindow.show()
    loginWindow.focus()
    return
  }

  loginWindow = new BrowserWindow({
    width: 880,
    height: 620,
    minWidth: 840,
    minHeight: 580,
    title: 'XCode Agent 登录',
    backgroundColor: '#2f1d49',
    frame: false,
    hasShadow: false,
    show: false,
    autoHideMenuBar: true,
    minimizable: false,
    maximizable: false,
    resizable: false,
    ...(process.platform === 'linux' ? { icon } : {}),
    webPreferences: {
      preload: join(__dirname, '../preload/index.js'),
      sandbox: false,
      contextIsolation: true,
      nodeIntegration: false,
      additionalArguments: [`--xcode-agent-base-url=${getBackendBaseUrl()}`]
    }
  })

  loginWindow.setMenuBarVisibility(false)
  loginWindow.on('close', (event) => {
    if (isQuitting) return
    event.preventDefault()
    loginWindow?.hide()
  })
  loginWindow.on('closed', () => {
    loginWindow = null
  })
  loginWindow.on('ready-to-show', () => {
    loginWindow?.show()
  })

  loadRendererPage(loginWindow, 'login')
}

/** 根据主进程内存中的登录态打开主窗口或登录窗口。 */
async function openAuthenticatedWindow(): Promise<void> {
  if (hasValidAuthToken()) {
    if (loginWindow && !loginWindow.isDestroyed()) {
      loginWindow.hide()
    }
    createMainWindow()
    return
  }

  if (mainWindow && !mainWindow.isDestroyed()) {
    mainWindow.hide()
  }
  createLoginWindow()
}

/** 从托盘发起统一的应用退出流程。 */
function quitFromTray(): void {
  isQuitting = true
  app.quit()
}

function setupTray(): void {
  if (tray) return

  tray = new Tray(icon)
  tray.setToolTip('XCode Agent')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      {
        label: '打开主窗口',
        click: () => {
          void openAuthenticatedWindow()
        }
      },
      { type: 'separator' },
      {
        label: '退出',
        click: () => {
          quitFromTray()
        }
      }
    ])
  )
  tray.on('click', () => {
    void openAuthenticatedWindow()
  })
}

/** 在冷启动阶段清除残留认证；失败时提示用户并阻止应用继续初始化。 */
async function clearAuthStateBeforeStartup(): Promise<boolean> {
  try {
    await ensureXcodeAgentDataDir()
    await clearAuthState()
    return true
  } catch (error) {
    const authFile = path.join(getXcodeAgentDataDir(), 'auth.json')
    console.error('Failed to clear auth token during startup', error)
    dialog.showErrorBox(
      '认证状态清理失败',
      `无法清理本地登录凭证，应用将退出。\n请检查文件权限后重试：\n${authFile}`
    )
    app.quit()
    return false
  }
}

/** 初始化获得单实例锁的主进程，成功后才允许窗口恢复。 */
async function initializePrimaryApplication(): Promise<boolean> {
  // Set app user model id for windows
  if (process.platform === 'win32') {
    app.setAppUserModelId(process.execPath)
  }

  if (!(await clearAuthStateBeforeStartup())) return false

  // Allow F12 to toggle DevTools in development
  app.on('browser-window-created', (_, window) => {
    window.webContents.on('before-input-event', (_event, input) => {
      if (input.key === 'F12') {
        window.webContents.toggleDevTools()
      }
    })
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))
  const backendBaseUrl = await startBackendService()
  console.log(`XCode Agent backend URL: ${backendBaseUrl}`)
  setupApplicationStorageIpc();
  setupAuthIpc()
  setupBrowserIpc();
  setupWorkspaceIpc();
  setupSessionStorageIpc();
  setupTray()

  await openAuthenticatedWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    void openAuthenticatedWindow()
  })

  return true
}

/** 处理主实例初始化中的非认证清理异常。 */
function handlePrimaryStartupFailure(error: unknown): boolean {
  console.error('Failed to start XCode Agent', error)
  app.quit()
  return false
}

/** 第二实例启动时等待主实例完成初始化，然后聚焦当前登录窗口或主窗口。 */
async function focusPrimaryWindowAfterStartup(): Promise<void> {
  const startupPromise = primaryStartupPromise
  if (!startupPromise || !(await startupPromise)) return
  await openAuthenticatedWindow()
}

/** 接收第二实例通知，避免第二进程触碰当前实例的认证文件。 */
function handleSecondInstance(): void {
  void focusPrimaryWindowAfterStartup().catch((error) => {
    console.error('Failed to focus the primary XCode Agent window', error)
  })
}

if (!hasSingleInstanceLock) {
  app.quit()
} else {
  app.on('second-instance', handleSecondInstance)
  primaryStartupPromise = app
    .whenReady()
    .then(initializePrimaryApplication)
    .catch(handlePrimaryStartupFailure)
}

let quitCleanupCompleted = false
let quitCleanupStarted = false

/** 在应用退出前清除认证状态并停止本地后端服务。 */
async function cleanupBeforeQuit(): Promise<void> {
  try {
    await clearAuthState()
  } catch (error) {
    console.error('Failed to clear auth token', error)
  }

  try {
    await stopBackendService()
  } catch (error) {
    console.error('Failed to stop backend service', error)
  }
}

if (hasSingleInstanceLock) {
  app.on('before-quit', (event) => {
    isQuitting = true
    if (quitCleanupCompleted) return

    event.preventDefault()
    if (quitCleanupStarted) return
    quitCleanupStarted = true

    void cleanupBeforeQuit().finally(() => {
      quitCleanupCompleted = true
      app.quit()
    })
  })

  // 普通关闭窗口时保持托盘运行，显式退出由 before-quit 统一清理。
  app.on('window-all-closed', () => {
    // 不在此处退出应用。
  })
}

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
