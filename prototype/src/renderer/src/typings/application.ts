import type { ReactNode } from 'react';
import type { DevelopmentContract } from './developmentContract';
import type { ApplicationLifecycle } from './workflow';

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
      useBuiltin: boolean;
      connectionMode: 'dbid' | 'plant';
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
    enable: boolean;
    rootPath: string;
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

export type MenuDataItem = {
  /** @name 子菜单 */
  children?: MenuDataItem[];
  routes?: undefined;
  /** @name 在菜单中隐藏子节点 */
  hideChildrenInMenu?: boolean;
  /** @name 在菜单中隐藏自己和子节点 */
  hideInMenu?: boolean;
  /** @name 菜单的icon */
  icon?: ReactNode;
  /** @name 自定义菜单的国际化 key */
  locale?: string | false;
  /** @name 菜单的名字 */
  name?: string;
  /** @name 用于标定选中的值，默认是 path */
  /** 如果渲染的是特定页面，key必须存在，且与src/page下面的page的引用地址保持一致 */
  key?: string;
  /** @name disable 菜单选项 */
  disabled?: boolean;
  /** @name disable menu 的 tooltip 菜单选项 */
  disabledTooltip?: boolean;
  /** @name 路径,可以设定为网页链接 */
  path?: string;
  /**
   * 当此节点被选中的时候也会选中 parentKeys 的节点
   *
   * @name 自定义父节点
   */
  parentKeys?: string[];
  /** @name 隐藏自己，并且将子节点提升到与自己平级 */
  flatMenu?: boolean;
  /** @name 指定外链打开形式，同a标签 */
  target?: string;
  /**
   * menuItem 的 tooltip 显示的路径
   */
  tooltip?: string;
  [key: string]: any;
};

export type ApplicationMenuItem = Omit<MenuDataItem, 'routes'> & {
  children?: ApplicationMenuItem[];
};

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

/**
 * 应用版本状态:iterating=当前迭代中(可改);released=已发布里程碑(只读)。
 * 单线里程碑模型:versions 是链式只读归档,无分叉。
 */
export type ApplicationVersionStatus = 'iterating' | 'released';

/**
 * 应用版本。每个版本自带私有 lifecycle(旅程按版本隔离)。
 * 新建应用自动产生 v1.0(iterating);发布后锁定为 released;发起新迭代派生下一版本。
 */
export interface ApplicationVersion {
  id: string;
  /** 人类可读版本号,如 v1.0 / v1.1。 */
  versionLabel: string;
  major: number;
  minor: number;
  status: ApplicationVersionStatus;
  /** 派生自哪个版本(首个为 undefined)。单线链式。 */
  parentVersionId?: string;
  /** 回退版本记录其内容来源，版本链仍以前一最新版本为父节点保持单向递增。 */
  restoredFromVersionId?: string;
  /** 版本创建时间(迭代发起时刻)。 */
  createdAt: number;
  /** 发布时间(status 转 released 时写)。 */
  releasedAt?: number;
  /** 版本私有生命周期。旅程阶段由它推导。 */
  lifecycle: ApplicationLifecycle;
  /** 发布时冻结的内容快照(已发布版本回看用)。 */
  snapshot?: {
    pageIds?: string[];
    endpointIds?: string[];
    requirementSummary?: string;
  };
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
  planningConfirmedAt?: number;
  createdAt: number;
  /** 应用所有版本里程碑,按时间正序。 */
  versions?: ApplicationVersion[];
  /** 当前迭代版本指针。 */
  currentVersionId?: string;
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
  menus: {
    enable: boolean;
    rootPath: string;
  };
  auth: ApplicationSchemaConfig['auth'];
  track: ApplicationSchemaConfig['track'];
  apiTrack: ApplicationSchemaConfig['apiTrack'];
  environment: ApplicationSchemaConfig['environment'];
  database: ApplicationSchemaConfig['database'];
}

export type RequirementDevelopmentPlan = DevelopmentContract;
