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
  schemaVersion?: 2;
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
    sharedModules?: ApplicationSharedModule[];
    developmentPlan?: {
      schemaVersion: 1;
      summary: string;
      executionOrder: string[];
    };
  };
  productDefinition?: ProductDefinition;
  apis: ApplicationApiDefinition[];
  schemas?: Record<string, Record<string, unknown>>;
  dataSources?: ApplicationDataSourceDefinition[];
  planning?: ApplicationPlanningSnapshot;
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

export interface ApplicationPlanningDocument {
  format: 'markdown';
  path: string;
  sha256: string;
}

export interface ApplicationPlanningSnapshot {
  schemaVersion: 1;
  status: 'confirmed';
  confirmedAt: string;
  documents: {
    requirementSpec: ApplicationPlanningDocument;
    projectPlan: ApplicationPlanningDocument;
  };
  requirementSpec: Record<string, unknown>;
  projectPlan: Record<string, unknown>;
}

export interface ApplicationMenuItem {
  key: string;
  path: string;
  label: string;
  type: 'menu' | 'page';
  purpose: string;
  keyFeatures: string[];
  pageKey?: string;
  design?: ApplicationPageDesign;
  children?: ApplicationMenuItem[];
  developmentTasks?: ApplicationDevelopmentTask[];
}

export interface ApplicationDevelopmentTask {
  id: string;
  title: string;
  description: string;
  kind: 'feature' | 'integration' | 'shared';
  status: 'todo' | 'in_progress' | 'completed';
  dependsOn: string[];
  blocks: string[];
  coversFeatures: string[];
  acceptanceCriteria: string[];
}

export interface ApplicationSharedModule {
  id: string;
  name: string;
  responsibility: string;
  usedByMenuKeys: string[];
  tasks: ApplicationDevelopmentTask[];
}

export interface ApplicationPageInteraction {
  id: string;
  name: string;
  trigger: string;
  userAction: string;
  systemResponse: string;
  endpointId?: string;
  targetMenuKey?: string;
}

export interface ApplicationApiDefinition {
  id: string;
  name: string;
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  path: string;
  purpose: string;
  parameters: ApplicationApiParameter[];
  requestSchemaRef?: string;
  responseSchemaRef?: string;
  errors: ApplicationApiError[];
  access: ApplicationApiAccess;
}

export interface ProductDefinition {
  schemaVersion: 1;
  status: 'draft' | 'confirmed';
  revision: number;
  generatedAt: string;
  confirmedAt?: string;
  clarifications: Array<{ questionId: string; question: string; answer: string }>;
  requirements: {
    goal: string;
    summary: string;
    acceptanceCriteria: string[];
    assumptions: string[];
  };
  architecture: {
    frontend: string;
    backend: string;
    data: string;
    testing: string;
  };
  roles: Array<{ id: string; name: string; responsibilities: string[] }>;
  businessFlows: Array<{
    id: string;
    name: string;
    actorRoleIds: string[];
    steps: Array<{ order: number; menuKey: string; interactionId: string }>;
  }>;
  risks: Array<{ id: string; level: 'low' | 'medium' | 'high'; description: string }>;
}

export interface ApplicationPageDesign {
  pageGoal: string;
  layout: {
    overall: string;
    regions: Array<{
      id: string;
      name: string;
      responsibility: string;
      presentation: string;
      actions: string[];
    }>;
    responsiveStrategy: string;
    density: 'compact' | 'medium' | 'comfortable';
  };
  states: Array<{ state: string; behavior: string; feedbackComponent: string }>;
  interactions: ApplicationPageInteraction[];
  responseBindings: Array<{ endpointId: string; sourcePath: string; target: string }>;
  access: {
    roleIds: string[];
    operationRoles: Record<string, string[]>;
    unauthorizedBehavior: string;
  };
  acceptanceCriteria: string[];
}

export interface ApplicationApiParameter {
  name: string;
  in: 'path' | 'query' | 'header' | 'cookie';
  required: boolean;
  schema: Record<string, unknown>;
}

export interface ApplicationApiError {
  code: string;
  httpStatus: number;
  description: string;
}

export interface ApplicationApiAccess {
  authenticationRequired: boolean;
  roleIds: string[];
}

export interface ApplicationDataSourceDefinition {
  id: string;
  name: string;
  type: 'database' | 'external_api' | 'static' | 'none';
  entities: Array<{ name: string; schemaRef: string }>;
  relations: Array<{
    from: string;
    to: string;
    type: 'one-to-one' | 'one-to-many' | 'many-to-one' | 'many-to-many';
  }>;
  seedStrategy: string;
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
