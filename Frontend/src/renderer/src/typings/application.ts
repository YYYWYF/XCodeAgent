import type { DevelopmentContract } from './developmentContract';

export type ApplicationTerminal = 'PC' | 'Mobile';
export type ApplicationLayoutType = '' | 'side' | 'top' | 'mix';
export type ApplicationDatasourceType = '' | 'DataBase' | 'API' | 'None';
export type ApplicationTrackMethod = string;
export type DatabaseConnectionMode = 'dbid' | 'connectionString';

export interface EnvironmentVariable {
  key: string;
  value: string;
  encrypted: boolean;
}

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
  apis: ApplicationApiDefinition[];
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
  environment: {
    dev: EnvironmentVariable[];
    prod: EnvironmentVariable[];
  };
  database: {
    connectionMode: DatabaseConnectionMode;
    /** DBID 密码服务 — 数据库名称（Schema 名） */
    schema: string;
    /** DBID 密码服务 — 开发环境 DBID */
    devDbid: string;
    /** DBID 密码服务 — 生产环境 DBID */
    prodDbid: string;
    /** 连接字符串方式 — 数据库地址 */
    host: string;
    /** 连接字符串方式 — 端口号 */
    port: string;
    /** 连接字符串方式 — 用户名 */
    username: string;
    /** 连接字符串方式 — 密码（仅限密文类型） */
    password: string;
  };
}

export interface ApplicationMenuItem {
  key: string;
  path: string;
  label: string;
  type: 'menu' | 'page';
  purpose: string;
  keyFeatures: string[];
  relatedPageIds?: string[];
  apiIds?: string[];
  interactions?: ApplicationPageInteraction[];
  pageKey?: string;
  children?: ApplicationMenuItem[];
}

export interface ApplicationPageInteraction {
  name: string;
  trigger: string;
  userAction: string;
  systemResponse: string;
  targetPageId?: string;
  apiIds: string[];
}

export interface ApplicationApiDefinition {
  id: string;
  name: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  path: string;
  purpose: string;
  requestDesign: string;
  responseDesign: string;
  usedByPageIds: string[];
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
  projectPath: string;
  terminal: ApplicationTerminal;
  layout: ApplicationSchemaConfig['layout'];
  theme: ApplicationSchemaConfig['theme'];
  datasource: ApplicationSchemaConfig['datasource'];
  envText: string;
  auth: ApplicationSchemaConfig['auth'];
  track: ApplicationSchemaConfig['track'];
  apiTrack: ApplicationSchemaConfig['apiTrack'];
  environment: ApplicationSchemaConfig['environment'];
  database: ApplicationSchemaConfig['database'];
}

export type RequirementDevelopmentPlan = DevelopmentContract;
