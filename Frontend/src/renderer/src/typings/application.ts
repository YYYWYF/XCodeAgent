import type { ReactNode } from 'react'
import type { DevelopmentContract } from './developmentContract'

export type ApplicationTerminal = 'PC' | 'Mobile'
export type ApplicationLayoutType = '' | 'side' | 'top' | 'mix'

/** 标识应用支持的数据源大类。 */
export enum DatasourceEnum {
  DB = 'database',
  API = 'external_api',
  STATIC = 'static'
}

/** 标识新建应用表单中的外部数据库连接方案，不写入 application.json。 */
export type DatasourceConnectionMode = 'dbid' | 'plant'

/** 描述数据库数据源持久化所需的连接信息。 */
export interface DatabaseDatasourceDetails {
  useBuiltin: boolean
  plantMode?: {
    domain: string
    port: number
    userName: string
    pwd: string
    schema: string
  }
  dbidMode?: {
    dbid: string
    userName: string
    domain: string
    port: number
    schema: string
  }
}

/** 描述最终写入 application.json 的数据库数据源配置。 */
export interface DatabaseDatasourceConfig {
  type: DatasourceEnum.DB
  /** 创建应用时数据库连接配置非必填；实体数据源在项目规划阶段再确认。 */
  db?: DatabaseDatasourceDetails
}

/** 描述最终写入 application.json 的静态数据源配置，不能包含数据库字段。 */
export interface StaticDatasourceConfig {
  type: DatasourceEnum.STATIC
}

/** 描述暂未启用的外部 API 数据源配置。 */
export interface ExternalApiDatasourceConfig {
  type: DatasourceEnum.API
}

/** 描述正式应用配置中的判别联合数据源。 */
export type ApplicationDatasourceConfig =
  | DatabaseDatasourceConfig
  | StaticDatasourceConfig
  | ExternalApiDatasourceConfig

/** 描述数据库数据源在新建表单中的临时连接配置。 */
export interface DatabaseDatasourceDraft {
  type: DatasourceEnum.DB
  db: DatabaseDatasourceDetails & {
    connectionMode?: DatasourceConnectionMode
  }
}

/** 描述静态数据源在新建表单中的配置。 */
export interface StaticDatasourceDraft {
  type: DatasourceEnum.STATIC
  db?: never
}

/** 描述外部 API 数据源在新建表单中的配置。 */
export interface ExternalApiDatasourceDraft {
  type: DatasourceEnum.API
  db?: never
}

/** 描述新建应用表单中的判别联合数据源草稿。 */
export type ApplicationDatasourceDraft =
  | DatabaseDatasourceDraft
  | StaticDatasourceDraft
  | ExternalApiDatasourceDraft

export type ApplicationTrackMethod = string
export type DatabaseConnectionMode = 'dbid' | 'connectionString'

export interface EnvironmentVariable {
  key: string
  value: string
  encrypted: boolean
}

export type ApplicationAudience = 'operator' | 'admin' | 'user' | 'customer' | 'developer' | 'other'

export type ApplicationTheme = 'light' | 'dark' | 'enterprise-blue' | 'custom'
export type ApplicationLayout =
  | 'top-nav'
  | 'side-nav'
  | 'top-side-nav'
  | 'immersive'
  | 'login-admin'

/** 描述新建应用阶段声明的内置 RBAC 开关和首次管理员种子。 */
export interface ApplicationAuthorizationSeed {
  enabled: boolean
  initialAdministratorSubjects: string[]
}

export interface ApplicationSchemaConfig {
  schemaVersion: 5
  appName: string
  appIcon: string
  senario: string
  terminal: ApplicationTerminal
  layout: {
    type: ApplicationLayoutType
    useHeader: boolean
    useFooter: boolean
  }
  theme: {
    primaryColor: string
  }
  datasource: ApplicationDatasourceConfig
  env: string[]
  menus: {
    enable: boolean
    rootPath: string
    homeMenuKey: string
    items: ApplicationMenuItem[]
    sharedModules?: ApplicationSharedModule[]
    developmentPlan?: {
      schemaVersion: 1
      summary: string
      executionOrder: string[]
    }
  }
  productDefinition?: ProductDefinition
  apis: ApplicationApiDefinition[]
  schemas?: Record<string, Record<string, unknown>>
  dataSources?: ApplicationDataSourceDefinition[]
  auth: {
    enable: boolean
    authnSource: string
    yht: {
      clientId: string
    }
  }
  authorization: ApplicationAuthorizationSeed
  track: {
    enable: boolean
    uploadId: string
    apiHost: string
    method: ApplicationTrackMethod
  }
  apiTrack: {
    enable: boolean
    businessId: string
    traceBaggage: string
    apiTrackHost: string
  }
  environment: {
    dev: EnvironmentVariable[]
    prod: EnvironmentVariable[]
  }
  database: {
    connectionMode: DatabaseConnectionMode
    /** DBID 密码服务 — 数据库名称（Schema 名） */
    schema: string
    /** DBID 密码服务 — 开发环境 DBID */
    devDbid: string
    /** DBID 密码服务 — 生产环境 DBID */
    prodDbid: string
    /** 连接字符串方式 — 数据库地址 */
    host: string
    /** 连接字符串方式 — 端口号 */
    port: string
    /** 连接字符串方式 — 用户名 */
    username: string
    /** 连接字符串方式 — 密码（仅限密文类型） */
    password: string
  }
}

export type MenuDataItem = {
  /** @name 子菜单 */
  children?: MenuDataItem[]
  routes?: undefined
  /** @name 在菜单中隐藏子节点 */
  hideChildrenInMenu?: boolean
  /** @name 在菜单中隐藏自己和子节点 */
  hideInMenu?: boolean
  /** @name 菜单的icon */
  icon?: ReactNode
  /** @name 自定义菜单的国际化 key */
  locale?: string | false
  /** @name 菜单的名字 */
  name?: string
  /** @name 用于标定选中的值，默认是 path */
  /** 如果渲染的是特定页面，key必须存在，且与src/page下面的page的引用地址保持一致 */
  key?: string
  /** @name disable 菜单选项 */
  disabled?: boolean
  /** @name disable menu 的 tooltip 菜单选项 */
  disabledTooltip?: boolean
  /** @name 路径,可以设定为网页链接 */
  path?: string
  /**
   * 当此节点被选中的时候也会选中 parentKeys 的节点
   *
   * @name 自定义父节点
   */
  parentKeys?: string[]
  /** @name 隐藏自己，并且将子节点提升到与自己平级 */
  flatMenu?: boolean
  /** @name 指定外链打开形式，同a标签 */
  target?: string
  /**
   * menuItem 的 tooltip 显示的路径
   */
  tooltip?: string
  [key: string]: any
}

export type ApplicationMenuItem = Omit<MenuDataItem, 'routes'> & {
  children?: ApplicationMenuItem[]
}

export interface ApplicationDevelopmentTask {
  id: string
  title: string
  description: string
  kind: 'feature' | 'integration' | 'shared'
  status: 'todo' | 'in_progress' | 'completed'
  dependsOn: string[]
  blocks: string[]
  coversFeatures: string[]
  acceptanceCriteria: string[]
}

export interface ApplicationSharedModule {
  id: string
  name: string
  responsibility: string
  usedByMenuKeys: string[]
  tasks: ApplicationDevelopmentTask[]
}

export interface ApplicationPageInteraction {
  id: string
  name: string
  trigger: string
  userAction: string
  systemResponse: string
  bindingType?: 'endpoint' | 'navigation' | 'local' | 'sequence' | 'external'
  endpointId?: string
  targetMenuKey?: string
  localEffect?: string
  externalTarget?: string
  steps?: Array<{
    type: 'endpoint' | 'navigation' | 'local' | 'external'
    endpointId?: string
    targetPageId?: string
    localEffect?: string
    externalTarget?: string
  }>
}

export interface ApplicationApiDefinition {
  id: string
  name: string
  method: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  path: string
  purpose: string
  parameters: ApplicationApiParameter[]
  requestSchemaRef?: string
  responseSchemaRef?: string
  errors: ApplicationApiError[]
  access: ApplicationApiAccess
}

export interface ProductDefinition {
  schemaVersion: 1
  status: 'draft' | 'confirmed'
  revision: number
  generatedAt: string
  confirmedAt?: string
  clarifications: Array<{ questionId: string; question: string; answer: string }>
  requirements: {
    goal: string
    summary: string
    acceptanceCriteria: string[]
  }
  architecture: {
    frontend: string
    backend: string
    data: string
    testing: string
  }
  roles: Array<{ id: string; name: string; responsibilities: string[] }>
  businessFlows: Array<{
    id: string
    name: string
    actorRoleIds: string[]
    steps: Array<{ order: number; menuKey: string; interactionId: string }>
  }>
}

export interface ApplicationPageDesign {
  pageGoal: string
  layout: {
    overall: string
    regions: Array<{
      id: string
      name: string
      responsibility: string
      presentation: string
      actions: string[]
    }>
    responsiveStrategy: string
    density: 'compact' | 'medium' | 'comfortable'
  }
  states: Array<{ state: string; behavior: string; feedbackComponent: string }>
  interactions: ApplicationPageInteraction[]
  responseBindings: Array<{ endpointId: string; sourcePath: string; target: string }>
  access: {
    roleIds: string[]
    operationRoles: Record<string, string[]>
    unauthorizedBehavior: string
  }
  acceptanceCriteria: string[]
}

export interface ApplicationApiParameter {
  name: string
  in: 'path' | 'query' | 'header' | 'cookie'
  required: boolean
  schema: Record<string, unknown>
}

export interface ApplicationApiError {
  code: string
  httpStatus: number
  description: string
}

export interface ApplicationApiAccess {
  authenticationRequired: boolean
  roleIds: string[]
}

export interface ApplicationEntityFieldDefinition {
  name: string
  label: string
  type: 'text' | 'long_text' | 'number' | 'decimal' | 'date' | 'datetime' | 'enum' | 'boolean'
  required: boolean
  description: string
}

export interface ApplicationDataSourceDefinition {
  id: string
  name: string
  type: DatasourceEnum
  entities: Array<{
    name: string
    schemaRef: string
    description?: string
    fields?: ApplicationEntityFieldDefinition[]
  }>
  relations: Array<{
    from: string
    to: string
    type: 'one-to-one' | 'one-to-many' | 'many-to-one' | 'many-to-many'
  }>
  seedStrategy: string
}

export interface ApplicationConfig extends ApplicationSchemaConfig {
  id: string
  name: string
  workspaceRoot?: string
  projectParentPath?: string
  projectDirectoryName?: string
  source?: 'new' | 'existing-workspace'
  audience?: ApplicationAudience
  enableAuth: boolean
  enableTracking: boolean
  legacyTheme?: ApplicationTheme
  legacyLayout?: ApplicationLayout
  enableTabs?: boolean
  pages: string[]
  defaultPage: string
  hasDynamicRoutes?: boolean
  dynamicRouteDescription?: string
  schema: ApplicationSchemaConfig
  requirementPlan?: RequirementDevelopmentPlan
  /** 应用规划线程 id，模板生成时持久化，供从历史恢复设计阶段历史卡片使用
   *  （后端在 lifecycle=ready_for_workbench 时会清空 threadId，前端需自行保留）。 */
  planningThreadId?: string
  createdAt: number
}

export interface ApplicationDraft {
  appName: string
  appIcon: string
  senario: string
  projectPath: string
  terminal: ApplicationTerminal
  layout: ApplicationSchemaConfig['layout']
  theme: ApplicationSchemaConfig['theme']
  datasource: ApplicationDatasourceDraft
  envText: string
  menus: {
    enable: boolean
    rootPath: string
  }
  auth: ApplicationSchemaConfig['auth']
  authorization: ApplicationSchemaConfig['authorization']
  track: ApplicationSchemaConfig['track']
  apiTrack: ApplicationSchemaConfig['apiTrack']
  environment: ApplicationSchemaConfig['environment']
  database: ApplicationSchemaConfig['database']
}

export type RequirementDevelopmentPlan = DevelopmentContract
