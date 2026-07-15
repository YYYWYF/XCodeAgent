export {};

import type { ElectronAPI } from '@electron-toolkit/preload';
import type { ApplicationSchemaConfig } from './typings';

declare global {
  interface Window {
    electron: ElectronAPI;
    xcodeAgent?: {
      isElectron: boolean;
      agentBaseUrl: string;
      platform: string;
      auth: {
        login: () => Promise<{ ok: boolean; token?: string }>;
        status: () => Promise<{ authenticated: boolean }>;
      };
      applications: {
        load: () => Promise<{ applications?: unknown }>;
        save: (applications: unknown[]) => Promise<{ ok?: boolean }>;
      };
      workspace?: {
        selectDirectory: (options?: { title?: string }) => Promise<{ canceled: boolean; path?: string }>;
        createProjectDirectory: (payload: {
          parentPath: string;
          projectName: string;
          applicationConfig: ApplicationSchemaConfig;
        }) => Promise<{ ok?: boolean; path: string }>;
        readApplication: (payload: {
          workspaceRoot: string;
        }) => Promise<{ application?: unknown }>;
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
        save: (payload: { workspaceRoot: string; session: unknown }) => Promise<{ ok?: boolean; session?: unknown }>;
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
