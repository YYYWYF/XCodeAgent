import { ElectronAPI } from '@electron-toolkit/preload'

declare global {
  interface Window {
    electron: ElectronAPI
    api: unknown
    xcodeAgent?: {
    isElectron: boolean;
    agentBaseUrl: string;
    platform: string;
    auth: {
      login: () => Promise<{ ok: true }>;
      status: () => Promise<{ authenticated: boolean }>;
      getAccessToken: () => Promise<{ accessToken: string | null }>;
      reauthenticate: () => Promise<{ ok: true }>;
    };
    applications: {
      load: () => Promise<{ applications?: unknown }>;
      save: (applications: unknown[]) => Promise<{ ok?: boolean }>;
    };
    workspace?: {
      selectDirectory: (options?: { title?: string }) => Promise<{ canceled: boolean; path?: string }>;
      createProjectDirectory: (payload: {
        workspacePath: string;
        applicationConfig: unknown;
      }) => Promise<{ ok?: boolean; path: string }>;
      readApplication: (payload: {
        workspaceRoot: string;
      }) => Promise<{ application?: unknown }>;
      inspectPlanningArtifacts: (payload: {
        workspaceRoot: string;
      }) => Promise<{
        ready: boolean;
        missing: string[];
        invalid: string[];
        pages: Array<{ key: string; label: string; path: string; purpose: string }>;
      }>;
    };
    sessions?: {
      listWorkspaces: () => Promise<{ workspaces?: unknown }>;
      list: (payload: {
        workspaceRoot: string;
        editorMode: 'frontend' | 'backend';
      }) => Promise<{ sessions?: unknown }>;
      read: (payload: {
        workspaceRoot: string;
        editorMode: 'frontend' | 'backend';
        sessionId: string;
      }) => Promise<{ session?: unknown }>;
      save: (payload: {
        workspaceRoot: string;
        session: unknown;
      }) => Promise<{ ok?: boolean; session?: unknown }>;
      delete: (payload: {
        workspaceRoot: string;
        editorMode: 'frontend' | 'backend';
        sessionId: string;
      }) => Promise<{ ok?: boolean }>;
    };
    browser?: {
      openExternal: (url: string) => Promise<{ ok?: boolean }>;
      openPreviewWindow?: (url: string) => Promise<{ ok?: boolean }>;
    };
  };
  }
}
