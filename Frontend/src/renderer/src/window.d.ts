export {}

import type { ElectronAPI } from '@electron-toolkit/preload'
import type {
  ApplicationSchemaConfig,
  DevelopmentPlanningPageTreeNode,
  TemplateDownloadResult
} from './typings'

declare global {
  interface Window {
    electron: ElectronAPI
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
      settings: {
        load: () => Promise<{
          settings?: { version?: number; appearance?: { theme?: unknown } }
        }>
        saveTheme: (payload: { theme: 'dark' | 'light' }) => Promise<{ ok?: boolean }>
        onThemeChanged: (listener: (payload: { theme?: unknown }) => void) => () => void
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
        }) => Promise<TemplateDownloadResult>
        readApplication: (payload: { workspaceRoot: string }) => Promise<{ application?: unknown }>
        inspectPlanningArtifacts: (payload: { workspaceRoot: string }) => Promise<{
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
            entityIds?: string[]
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
          entities: Array<{
            id: string
            label: string
            purpose: string
            dataSourceType: string
            detailPlanStatus?: string
            hasDetailPlan: boolean
            designed: boolean
          }>
        }>
      }
      sessions?: {
        listWorkspaces: () => Promise<{ workspaces?: unknown }>
        list: (payload: {
          workspaceRoot: string
          editorMode: 'frontend' | 'backend'
        }) => Promise<{ sessions?: unknown }>
        read: (payload: {
          workspaceRoot: string
          editorMode: 'frontend' | 'backend'
          sessionId: string
        }) => Promise<{ session?: unknown }>
        create: (payload: {
          workspaceRoot: string
          workflowId: string
          editorMode: 'frontend' | 'backend'
          workbenchPhase: 'product' | 'planning' | 'development' | 'test' | 'review' | 'acceptance'
          entryKey?: string
          title?: string
          revisionContext?: unknown
          recoveryExecutionRunId?: string
        }) => Promise<{ ok?: boolean; session?: unknown }>
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
        openReportFile?: (reportPath: string) => Promise<{ ok?: boolean }>
      }
      projectPreview?: {
        registerWorkspace: (payload: { workspaceRoot: string }) => Promise<{ ok?: boolean }>
        unregisterWorkspace: (payload: { workspaceRoot: string }) => Promise<{ ok?: boolean }>
      }
    }
  }
}
