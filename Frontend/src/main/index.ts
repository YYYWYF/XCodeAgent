import { app, shell, BrowserWindow, ipcMain, dialog, Menu, Tray } from 'electron'
import { join } from 'path'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'
import { XCODE_AGENT_ENV } from './env'
import { getBackendBaseUrl, startBackendService, stopBackendService } from './backendService'
import {
  clearAuthState,
  ensureXcodeAgentDataDir,
  getAccessToken,
  getXcodeAgentDataDir,
  hasValidAuthToken,
  initializeAuthState,
  loginWithCmbDeviceFlow
} from './auth'

let mainWindow:BrowserWindow|null = null;
let loginWindow: BrowserWindow | null = null
let tray: Tray | null = null
let isQuitting = false
const previewWindows = new Set();


function getApplicationsFile() {
  return path.join(app.getPath('userData'), 'applications.json');
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
  createdAt: number
  updatedAt: number
  messageCount: number
}

const MESSAGE_APPROVAL_STATUSES = new Set([
  'pending',
  'approved_once',
  'approved_always',
  'feedback',
])

function getSeedApplicationsFile() {
  return path.join(__dirname, '..', 'data', 'applications.json');
}

async function ensureApplicationsFile() {
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

async function readApplications() {
  const applicationsFile = await ensureApplicationsFile();
  const rawValue = await fs.readFile(applicationsFile, 'utf8');
  const parsed = JSON.parse(rawValue || '[]');
  return Array.isArray(parsed) ? parsed : [];
}

async function writeApplications(applications) {
  if (!Array.isArray(applications)) {
    throw new Error('applications must be an array');
  }

  const applicationsFile = await ensureApplicationsFile();
  await fs.writeFile(applicationsFile, `${JSON.stringify(applications, null, 2)}\n`, 'utf8');
}

function setupApplicationStorageIpc() {
  ipcMain.handle('applications:load', async () => ({
    applications: await readApplications(),
  }));

  ipcMain.handle('applications:save', async (_event, applications) => {
    await writeApplications(applications);
    return { ok: true };
  });
}

function normalizeExternalUrl(url) {
  if (typeof url !== 'string') {
    throw new Error('url must be a string');
  }

  const parsedUrl = new URL(url);
  if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
    throw new Error('Only http and https URLs can be opened');
  }

  return parsedUrl.toString();
}

function setupBrowserIpc() {
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

function assertDirectoryName(value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('projectName must be a non-empty string');
  }

  const trimmedValue = value.trim();
  if (trimmedValue !== path.basename(trimmedValue) || /[<>:"/\\|?*\x00-\x1F]/.test(trimmedValue)) {
    throw new Error('projectName contains invalid path characters');
  }

  return trimmedValue;
}

function assertEditorMode(value) {
  if (!['frontend', 'backend'].includes(value)) {
    throw new Error('editorMode must be frontend or backend');
  }
  return value;
}

function assertSessionId(value) {
  if (typeof value !== 'string' || !/^[A-Za-z0-9_-]+$/.test(value)) {
    throw new Error('sessionId contains invalid characters');
  }
  return value;
}

function resolveWorkspaceRoot(value) {
  if (typeof value !== 'string' || !value.trim()) {
    throw new Error('workspaceRoot must be a non-empty string');
  }
  return path.resolve(value);
}

function getWorkspaceSessionKey(workspaceRoot) {
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

function getWorkspaceSessionRoot(workspaceRoot) {
  return path.join(getXcodeAgentDataDir(), 'sessions', getWorkspaceSessionKey(workspaceRoot));
}

function getSessionStorageRoot(): string {
  return path.join(getXcodeAgentDataDir(), 'sessions');
}

function getSessionsDir(workspaceRoot, editorMode) {
  return path.join(getWorkspaceSessionRoot(workspaceRoot), assertEditorMode(editorMode));
}

function getSessionFile(workspaceRoot, editorMode, sessionId) {
  return path.join(getSessionsDir(workspaceRoot, editorMode), `${assertSessionId(sessionId)}.json`);
}

function getLegacyWorkspaceSessionsDir(workspaceRoot, editorMode): string {
  return path.join(
    resolveWorkspaceRoot(workspaceRoot),
    XCODE_AGENT_ENV.WORKING_DIR,
    'sessions',
    assertEditorMode(editorMode)
  )
}

async function ensureSessionsDir(workspaceRoot, editorMode) {
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

async function migrateLegacyWorkspaceSessions(workspaceRoot, editorMode, sessionsDir) {
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

function sessionSummary(session) {
  const messages = Array.isArray(session.messages) ? session.messages : [];
  return {
    id: String(session.id || ''),
    title: String(session.title || '新对话'),
    editorMode: assertEditorMode(session.editorMode),
    threadId: String(session.threadId || ''),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
    messageCount: messages.length,
  };
}

function cloneJsonRecord(value: unknown): Record<string, unknown> | undefined {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return undefined

  try {
    return JSON.parse(JSON.stringify(value)) as Record<string, unknown>
  } catch {
    return undefined
  }
}

function normalizeSessionMessage(message): Record<string, unknown> {
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

  return {
    ...normalizedMessage,
    ...(orchestration ? { orchestration } : {}),
    ...(approval ? { approval } : {}),
    ...(MESSAGE_APPROVAL_STATUSES.has(message.approvalStatus)
      ? { approvalStatus: message.approvalStatus }
      : {}),
    ...(codeChanges ? { codeChanges } : {}),
    ...(workflow ? { workflow } : {}),
  }
}

function normalizeSession(session) {
  if (!session || typeof session !== 'object') {
    throw new Error('session must be an object');
  }

  const editorMode = assertEditorMode(session.editorMode);
  const id = assertSessionId(session.id);
  const messages = Array.isArray(session.messages)
    ? session.messages
        .filter((message) => message && typeof message === 'object')
        .map(normalizeSessionMessage)
    : [];

  return {
    id,
    title: String(session.title || '新对话'),
    editorMode,
    threadId: String(session.threadId || id),
    createdAt: Number(session.createdAt || Date.now()),
    updatedAt: Number(session.updatedAt || Date.now()),
    workspaceRoot: typeof session.workspaceRoot === 'string' ? session.workspaceRoot : '',
    messages,
  };
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

function setupWorkspaceIpc() {
  ipcMain.handle('workspace:read-application', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot);
    const applicationFile = path.join(workspaceRoot, 'application.json');
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
    if (typeof payload.parentPath !== 'string' || !payload.parentPath.trim()) {
      throw new Error('parentPath must be a non-empty string');
    }
    if (
      !payload.applicationConfig ||
      typeof payload.applicationConfig !== 'object' ||
      Array.isArray(payload.applicationConfig)
    ) {
      throw new Error('applicationConfig must be an object');
    }

    const parentPath = path.resolve(payload.parentPath);
    const projectName = assertDirectoryName(payload.projectName);
    const projectPath = path.resolve(parentPath, projectName);

    if (path.dirname(projectPath) !== parentPath) {
      throw new Error('Project path escapes the selected parent directory');
    }

    await fs.mkdir(projectPath, { recursive: false });
    await fs.writeFile(
      path.join(projectPath, 'application.json'),
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
  if (is.dev && rendererUrl) {
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
    minWidth: 1100,
    minHeight: 720,
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

function createLoginWindow(): void {
  if (loginWindow && !loginWindow.isDestroyed()) {
    loginWindow.show()
    loginWindow.focus()
    return
  }

  loginWindow = new BrowserWindow({
    width: 440,
    height: 520,
    minWidth: 420,
    minHeight: 480,
    title: 'XCode Agent 登录',
    backgroundColor: '#eef2f6',
    show: false,
    autoHideMenuBar: true,
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

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(async () => {
  // Set app user model id for windows
  electronApp.setAppUserModelId('com.electron')

  // Default open or close DevTools by F12 in development
  // and ignore CommandOrControl + R in production.
  // see https://github.com/alex8088/electron-toolkit/tree/master/packages/utils
  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  // IPC test
  ipcMain.on('ping', () => console.log('pong'))
  await ensureXcodeAgentDataDir()
  await initializeAuthState()
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
}).catch((error) => {
  console.error('Failed to start XCode Agent', error)
  app.quit()
})

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

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
