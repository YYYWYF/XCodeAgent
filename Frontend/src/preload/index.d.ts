import { ElectronAPI } from '@electron-toolkit/preload'

declare global {
  interface Window {
    electron: ElectronAPI
    api: unknown
    xcodeAgent?: {
    isElectron: boolean;
    agentBaseUrl: string;
    platform: string;
    applications: {
      load: () => Promise<{ applications?: unknown }>;
      save: (applications: unknown[]) => Promise<{ ok?: boolean }>;
    };
    workspace?: {
      selectDirectory: (options?: { title?: string }) => Promise<{ canceled: boolean; path?: string }>;
      createProjectDirectory: (payload: {
        parentPath: string;
        projectName: string;
      }) => Promise<{ ok?: boolean; path: string }>;
    };
    sessions?: {
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
