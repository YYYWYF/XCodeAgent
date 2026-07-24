import { contextBridge,ipcRenderer} from 'electron'
import { electronAPI } from '@electron-toolkit/preload'
const AGENT_ARG_PREFIX = '--xcode-agent-base-url=';
const agentBaseUrl =
  process.argv.find((argument) => argument.startsWith(AGENT_ARG_PREFIX))?.slice(AGENT_ARG_PREFIX.length) ||
  'http://127.0.0.1:8000';

// Custom APIs for renderer
const api = {}

const xcodeAgentApi = {
  isElectron: true,
  agentBaseUrl,
  platform: process.platform,
  auth: {
    login: () => ipcRenderer.invoke('auth:login'),
    status: () => ipcRenderer.invoke('auth:status'),
    getAccessToken: () => ipcRenderer.invoke('auth:get-access-token'),
    reauthenticate: () => ipcRenderer.invoke('auth:reauthenticate'),
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
    deleteAgentDirectory: (payload) =>
      ipcRenderer.invoke('applications:delete-agent-directory', payload),
  },
  workspace: {
    selectDirectory: (options = {}) => ipcRenderer.invoke('workspace:select-directory', options),
    createProjectDirectory: (payload) => ipcRenderer.invoke('workspace:create-project-directory', payload),
    cloneTemplate: (payload) => ipcRenderer.invoke('workspace:clone-template', payload),
    writeTemplatePages: (payload) => ipcRenderer.invoke('workspace:write-template-pages', payload),
    readApplication: (payload) => ipcRenderer.invoke('workspace:read-application', payload),
    inspectPlanningArtifacts: (payload) => ipcRenderer.invoke('workspace:inspect-planning-artifacts', payload),
  },
  sessions: {
    listWorkspaces: () => ipcRenderer.invoke('sessions:list-workspaces'),
    list: (payload) => ipcRenderer.invoke('sessions:list', payload),
    read: (payload) => ipcRenderer.invoke('sessions:read', payload),
    save: (payload) => ipcRenderer.invoke('sessions:save', payload),
    delete: (payload) => ipcRenderer.invoke('sessions:delete', payload),
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
  },
}

// Use `contextBridge` APIs to expose Electron APIs to
// renderer only if context isolation is enabled, otherwise
// just add to the DOM global.
if (process.contextIsolated) {
  try {
    contextBridge.exposeInMainWorld('electron', electronAPI)
    contextBridge.exposeInMainWorld('api', api)
    contextBridge.exposeInMainWorld('xcodeAgent', xcodeAgentApi);
    
  } catch (error) {
    console.error(error)
  }
} else {
  // @ts-ignore (define in dts)
  window.electron = electronAPI
  // @ts-ignore (define in dts)
  window.api = api
  // @ts-ignore (define in dts)
  window.xcodeAgent = xcodeAgentApi
}
