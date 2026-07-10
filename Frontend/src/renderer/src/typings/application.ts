import type { DevelopmentContract } from './developmentContract';

export type ApplicationTerminal = 'PC' | 'Mobile';
export type ApplicationLayoutType = '' | 'LEFT_RIGHT_ONE' | 'TOP_BOTTOM_ONE' | 'TOP_LEFT_RIGHT' | 'FULL_SCREEN';
export type ApplicationDatasourceType = '' | 'DataBase' | 'API' | 'None';
export type ApplicationTrackMethod = 'post' | 'get';

export type ApplicationAudience =
  | 'operator'
  | 'admin'
  | 'user'
  | 'customer'
  | 'developer'
  | 'other';

export type ApplicationTheme = 'light' | 'dark' | 'enterprise-blue' | 'custom';
export type ApplicationLayout =
  | 'top-nav'
  | 'side-nav'
  | 'top-side-nav'
  | 'immersive'
  | 'login-admin';

export interface ApplicationSchemaConfig {
  appName: string;
  appIcon: string;
  senario: string;
  terminal: ApplicationTerminal;
  layout: {
    type: ApplicationLayoutType;
    useHeader: boolean;
    useFooter: boolean;
  };
  theme: {
    primaryColor: string;
  };
  datasource: {
    type: ApplicationDatasourceType;
    db: {
      plantMode: {
        domain: string;
        port: number | string;
        userName: string;
        pwd: string;
        schema: string;
      };
    };
  };
  env: string[];
  menus: {
    homeMenuKey: string;
    items: ApplicationMenuItem[];
  };
  auth: {
    enable: boolean;
    authnSource: string;
    yht: {
      clientId: string;
    };
  };
  track: {
    enable: boolean;
    uploadId: string;
    apiHost: string;
    method: ApplicationTrackMethod;
  };
  apiTrack: {
    enable: boolean;
    businessId: string;
    traceBaggage: string;
    apiTrackHost: string;
  };
}

export interface ApplicationMenuItem {
  key: string;
  path: string;
  label: string;
  type: 'menu' | 'page';
  pageKey?: string;
  children?: ApplicationMenuItem[];
}

export interface ApplicationConfig extends ApplicationSchemaConfig {
  id: string;
  name: string;
  workspaceRoot?: string;
  projectParentPath?: string;
  projectDirectoryName?: string;
  source?: 'new' | 'existing-workspace';
  audience?: ApplicationAudience;
  enableAuth: boolean;
  enableTracking: boolean;
  legacyTheme?: ApplicationTheme;
  legacyLayout?: ApplicationLayout;
  enableTabs?: boolean;
  pages: string[];
  defaultPage: string;
  hasDynamicRoutes?: boolean;
  dynamicRouteDescription?: string;
  schema: ApplicationSchemaConfig;
  requirementPlan?: RequirementDevelopmentPlan;
  createdAt: number;
}

export interface ApplicationDraft {
  appName: string;
  appIcon: string;
  senario: string;
  projectParentPath: string;
  projectDirectoryName: string;
  terminal: ApplicationTerminal;
  layout: ApplicationSchemaConfig['layout'];
  theme: ApplicationSchemaConfig['theme'];
  datasource: ApplicationSchemaConfig['datasource'];
  envText: string;
  auth: ApplicationSchemaConfig['auth'];
  track: ApplicationSchemaConfig['track'];
  apiTrack: ApplicationSchemaConfig['apiTrack'];
}

export type RequirementDevelopmentPlan = DevelopmentContract;
