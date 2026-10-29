import { contextBridge,ipcRenderer} from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
import type { AppUpdateApi, AppUpdateState } from '../shared/appUpdate'

const appUpdate: AppUpdateApi = {
  /** 补取启动更新的当前状态。 */
  getState: () => ipcRenderer.invoke('app-update:state'),
  /** 订阅主进程进度并在窗口卸载时释放订阅。 */
  onStateChanged: (listener) => {
    /** 隔离 Electron 事件对象，仅向页面传递更新快照。 */
    const handler = (_event: Electron.IpcRendererEvent, state: AppUpdateState): void =>
      listener(state)
    ipcRenderer.on('app-update:changed', handler)
    return () => {
      ipcRenderer.removeListener('app-update:changed', handler)
    }
  },
  /** 请求安装已校验的更新，不接受下载地址参数。 */
  install: () => ipcRenderer.invoke('app-update:install'),
  /** 源码模式继续进入登录，不执行安装。 */
  continueDevelopment: () => ipcRenderer.invoke('app-update:continue'),
  /** 退出整个应用且不安装更新。 */
  quit: () => ipcRenderer.invoke('app-update:quit')
}
const AGENT_ARG_PREFIX = '--devagentstudio-base-url=';
const agentBaseUrl =
  process.argv.find((argument) => argument.startsWith(AGENT_ARG_PREFIX))?.slice(AGENT_ARG_PREFIX.length) ||
  'http://127.0.0.1:8000';

type ProjectPreviewWorkspacePayload = {
  workspaceRoot: string
}

// Custom APIs for renderer
const api = {}

const devAgentStudioApi = {
  appUpdate,
  isElectron: true,
  agentBaseUrl,
  platform: process.platform,
  clipboard: {
    /** 将日志等纯文本交给主进程写入系统剪贴板。 */
    writeText: (text: string): Promise<void> => ipcRenderer.invoke('clipboard:write-text', text)
  },
  auth: {
    login: () => ipcRenderer.invoke('auth:login'),
    status: () => ipcRenderer.invoke('auth:status'),
    getAccessToken: () => ipcRenderer.invoke('auth:get-access-token'),
    reauthenticate: () => ipcRenderer.invoke('auth:reauthenticate'),
  },
  settings: {
    load: () => ipcRenderer.invoke('settings:load'),
    saveTheme: (payload) => ipcRenderer.invoke('settings:save-theme', payload),
    onThemeChanged: (listener) => {
      const handler = (_event, payload): void => listener(payload);
      ipcRenderer.on('settings:theme-changed', handler);
      return () => ipcRenderer.removeListener('settings:theme-changed', handler);
    },
  },
  applications: {
    load: () => ipcRenderer.invoke('applications:load'),
    save: (applications) => {
      if (!Array.isArray(applications)) {
        return Promise.reject(new Error('applications must be an array'));
      }
      return ipcRenderer.invoke('applications:save', applications);
    },
    deleteProject: (payload) => ipcRenderer.invoke('applications:delete-project', payload),
  },
  workspace: {
    selectDirectory: (options = {}) => ipcRenderer.invoke('workspace:select-directory', options),
    readApplication: (payload) => ipcRenderer.invoke('workspace:read-application', payload),
    writeApplication: (payload) => ipcRenderer.invoke('workspace:write-application', payload),
    inspectPlanningArtifacts: (payload) => ipcRenderer.invoke('workspace:inspect-planning-artifacts', payload),
    readUiDesigns: (payload) => ipcRenderer.invoke('workspace:read-ui-designs', payload),
  },
  sessions: {
    listWorkspaces: () => ipcRenderer.invoke('sessions:list-workspaces'),
    list: (payload) => ipcRenderer.invoke('sessions:list', payload),
    read: (payload) => ipcRenderer.invoke('sessions:read', payload),
    create: (payload) => ipcRenderer.invoke('sessions:create', {
      workspaceRoot: payload.workspaceRoot,
      session: payload,
    }),
    save: (payload) => ipcRenderer.invoke('sessions:save', payload),
    delete: (payload) => ipcRenderer.invoke('sessions:delete', payload),
    clearWorkspace: (payload) => ipcRenderer.invoke('sessions:clear-workspace', payload),
  },
  browser: {
    openExternal: (url) => {
      if (typeof url !== 'string') {
        return Promise.reject(new Error('url must be a string'));
      }
      return ipcRenderer.invoke('browser:open-external', url);
    },
    openPreviewWindow: (url) => {
      if (typeof url !== 'string') {
        return Promise.reject(new Error('url must be a string'));
      }
      return ipcRenderer.invoke('browser:open-preview-window', url);
    },
    openReportFile: (reportPath) => {
      if (typeof reportPath !== 'string') {
        return Promise.reject(new Error('reportPath must be a string'));
      }
      return ipcRenderer.invoke('browser:open-report-file', reportPath);
    },
  },
  projectPreview: {
    registerWorkspace: (payload: ProjectPreviewWorkspacePayload) =>
      ipcRenderer.invoke('project-preview:register-workspace', payload),
    unregisterWorkspace: (payload: ProjectPreviewWorkspacePayload) =>
      ipcRenderer.invoke('project-preview:unregister-workspace', payload),
  },
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
    contextBridge.exposeInMainWorld('devAgentStudio', devAgentStudioApi);
    
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
  // @ts-ignore (define in dts)
  window.devAgentStudio = devAgentStudioApi
}
