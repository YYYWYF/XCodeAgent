export {}

import type {
  ApplicationMenuItem,
  ApplicationSchemaConfig,
  DevelopmentPlanningEntity,
  DevelopmentPlanningPageTreeNode
} from './typings'
import type { DevelopmentPlanningAgent } from './agentDevelopment'

declare global {
  interface Window {
    // 浏览器原型环境不跑 Electron；window.xcodeAgent 由 mock 数据层注入，
    // electron 类型保留为可选，供读取真实工程的 IPC 兼容代码容错。
    electron?: {
      ipcRenderer?: {
        invoke?: (channel: string, ...args: unknown[]) => Promise<unknown>
      }
    }
    xcodeAgent?: {
      isElectron: boolean
      agentBaseUrl: string
      platform: string
      auth: {
        login: () => Promise<{ ok: true }>
        status: () => Promise<{ authenticated: boolean }>
        getAccessToken: () => Promise<{ accessToken: string | null }>
        reauthenticate: () => Promise<{ ok: true }>
      }
      applications: {
        load: () => Promise<{ applications?: unknown }>
        save: (applications: unknown[]) => Promise<{ ok?: boolean }>
        deleteProject: (payload: { workspaceRoot: string }) => Promise<{ ok?: boolean }>
        deleteAgentDirectory: (payload: { workspaceRoot: string }) => Promise<{ ok?: boolean }>
      }
      workspace?: {
        selectDirectory: (options?: {
          title?: string
        }) => Promise<{ canceled: boolean; path?: string }>
        createProjectDirectory: (payload: {
          workspacePath: string
          applicationConfig: ApplicationSchemaConfig
        }) => Promise<{ ok?: boolean; path: string }>
        cloneTemplate: (payload: {
          projectPath: string
          appName: string
          frontendTemplateUrl?: string
          backendTemplateUrl?: string
        }) => Promise<{ ok?: boolean }>
        writeTemplatePages: (payload: {
          projectPath: string
          appName: string
          pages: Array<{ pageKey: string; name?: string }>
          menuItems: ApplicationMenuItem[]
        }) => Promise<{
          ok?: boolean
          pagesDir: string
          written: Array<{ pageKey: string; path: string }>
        }>
        readApplication: (payload: { workspaceRoot: string }) => Promise<{ application?: unknown }>
        inspectPlanningArtifacts: (payload: {
          workspaceRoot: string
          applicationId?: string
          versionId?: string
        }) => Promise<{
          ready: boolean
          hasPageDesigns: boolean
          missing: string[]
          invalid: string[]
          pages: Array<{
            key: string
            pageId: string
            label: string
            path: string
            purpose: string
            detailPlanStatus?: string
            hasDetailPlan: boolean
            designed: boolean
            taskSummary?: {
              total: number
              pending: number
              running: number
              completed: number
              failed: number
            }
          }>
          pageTree: DevelopmentPlanningPageTreeNode[]
          apiContracts: Array<{
            id: string
            label: string
            dataSourceIds?: string[]
            endpoints: Array<{
              apiContractId?: string
              id: string
              method: string
              path: string
              summary: string
              detailPlanStatus?: string
              hasDetailPlan?: boolean
              designed?: boolean
            }>
          }>
          entities: DevelopmentPlanningEntity[]
          agents: DevelopmentPlanningAgent[]
        }>
      }
      sessions?: {
        listWorkspaces: () => Promise<{ workspaces?: unknown }>
        list: (payload: {
          workspaceRoot: string
          editorMode: 'frontend' | 'backend'
          applicationId?: string
        }) => Promise<{ sessions?: unknown }>
        read: (payload: {
          workspaceRoot: string
          editorMode: 'frontend' | 'backend'
          sessionId: string
        }) => Promise<{ session?: unknown }>
        save: (payload: {
          workspaceRoot: string
          session: unknown
        }) => Promise<{ ok?: boolean; session?: unknown }>
        delete: (payload: {
          workspaceRoot: string
          editorMode: 'frontend' | 'backend'
          sessionId: string
        }) => Promise<{ ok?: boolean }>
      }
      browser?: {
        openExternal: (url: string) => Promise<{ ok?: boolean }>
        openPreviewWindow?: (url: string) => Promise<{ ok?: boolean }>
      }
      projectPreview?: {
        registerWorkspace: (payload: { workspaceRoot: string }) => Promise<{ ok?: boolean }>
        unregisterWorkspace: (payload: { workspaceRoot: string }) => Promise<{ ok?: boolean }>
      }
    }
  }
}
