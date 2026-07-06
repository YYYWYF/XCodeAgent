import { app, shell, BrowserWindow, ipcMain,dialog } from 'electron'
import { join } from 'path'
import crypto from 'node:crypto'
import fs from 'node:fs/promises'
import path from 'node:path'
import { electronApp, optimizer, is } from '@electron-toolkit/utils'
import icon from '../../resources/icon.png?asset'

const AGENT_BASE_URL = process.env.XCODE_AGENT_BASE_URL || 'http://127.0.0.1:8000';

let mainWindow:BrowserWindow|null = null;
const previewWindows = new Set();


function getApplicationsFile() {
  return path.join(app.getPath('userData'), 'applications.json');
}

function getXcodeAgentDataDir() {
  return path.join(app.getPath('userData'), '.xcodeagent');
}

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
      width: 1280,
      height: 860,
      minWidth: 960,
      minHeight: 640,
      title: '网页预览',
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

function getSessionsDir(workspaceRoot, editorMode) {
  return path.join(getWorkspaceSessionRoot(workspaceRoot), assertEditorMode(editorMode));
}

function getSessionFile(workspaceRoot, editorMode, sessionId) {
  return path.join(getSessionsDir(workspaceRoot, editorMode), `${assertSessionId(sessionId)}.json`);
}

function getLegacyWorkspaceSessionsDir(workspaceRoot, editorMode) {
  return path.join(resolveWorkspaceRoot(workspaceRoot), '.xcodeagent', 'sessions', assertEditorMode(editorMode));
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
        // Missing target files are copied into the app-owned .xcodeagent store below.
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

function normalizeSession(session) {
  if (!session || typeof session !== 'object') {
    throw new Error('session must be an object');
  }

  const editorMode = assertEditorMode(session.editorMode);
  const id = assertSessionId(session.id);
  const messages = Array.isArray(session.messages)
    ? session.messages
        .filter((message) => message && typeof message === 'object')
        .map((message) => ({
          id: Number(message.id || Date.now()),
          role: message.role === 'assistant' ? 'assistant' : 'user',
          content: String(message.content || ''),
          createdAt: Number(message.createdAt || Date.now()),
        }))
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

function setupWorkspaceIpc() {
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

    const parentPath = path.resolve(payload.parentPath);
    const projectName = assertDirectoryName(payload.projectName);
    const projectPath = path.resolve(parentPath, projectName);

    if (path.dirname(projectPath) !== parentPath) {
      throw new Error('Project path escapes the selected parent directory');
    }

    await fs.mkdir(projectPath, { recursive: false });

    return {
      ok: true,
      path: projectPath,
    };
  });
}

function setupSessionStorageIpc() {
  ipcMain.handle('sessions:list', async (_event, payload = {}) => {
    const workspaceRoot = resolveWorkspaceRoot(payload.workspaceRoot);
    const editorMode = assertEditorMode(payload.editorMode);
    const sessionsDir = await ensureSessionsDir(workspaceRoot, editorMode);
    await migrateLegacyWorkspaceSessions(workspaceRoot, editorMode, sessionsDir);

    const entries = await fs.readdir(sessionsDir, { withFileTypes: true });
    const sessions:any = [];
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



function createWindow(): void {
  // Create the browser window.
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
      additionalArguments: [`--xcode-agent-base-url=${AGENT_BASE_URL}`],
    }
  })

  mainWindow.setMenuBarVisibility(false);
  mainWindow.on('ready-to-show', () => {
    mainWindow?.show()
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  // HMR for renderer base on electron-vite cli.
  // Load the remote URL for development or the local html file for production.
  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

// This method will be called when Electron has finished
// initialization and is ready to create browser windows.
// Some APIs can only be used after this event occurs.
app.whenReady().then(() => {
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
  setupApplicationStorageIpc();
  setupBrowserIpc();
  setupWorkspaceIpc();
  setupSessionStorageIpc();

  createWindow()

  app.on('activate', function () {
    // On macOS it's common to re-create a window in the app when the
    // dock icon is clicked and there are no other windows open.
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

// Quit when all windows are closed, except on macOS. There, it's common
// for applications and their menu bar to stay active until the user quits
// explicitly with Cmd + Q.
app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

// In this file you can include the rest of your app's specific main process
// code. You can also put them in separate files and require them here.
