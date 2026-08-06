// 武汉分行项目管理系统 · 开发镜像应用配置。
import type { ApplicationConfig, ApplicationSchemaConfig } from '../../src/renderer/src/typings'

export const PMS_MENU_ITEMS = [
  {
    key: 'projects',
    type: 'menu',
    label: '项目管理',
    children: [{ key: 'my-projects', type: 'page', label: '我的项目', path: '/my-projects' }]
  },
  {
    key: 'rechecks',
    type: 'menu',
    label: '需求回检',
    children: [
      { key: 'my-rechecks', type: 'page', label: '我的回检', path: '/my-rechecks' },
      { key: 'recheck-review', type: 'page', label: '回检审核', path: '/recheck-review' }
    ]
  }
] as unknown as ApplicationSchemaConfig['menus']['items']

export function makeSchema(overrides: Partial<ApplicationSchemaConfig> = {}): ApplicationSchemaConfig {
  return {
    appName: '武汉分行项目管理系统',
    appIcon: 'ProjectOutlined',
    senario: '武汉分行项目管理与需求回检协同',
    terminal: 'PC',
    layout: { type: 'side', useHeader: true, useFooter: false },
    theme: { primaryColor: '#6b3cf0' },
    datasource: { type: 'mysql', db: { plantMode: { domain: '', port: '', userName: '', pwd: '', schema: '' } } },
    env: ['dev'],
    menus: { enable: true, rootPath: '/page', homeMenuKey: 'my-projects', items: PMS_MENU_ITEMS },
    apis: [],
    auth: { enable: false, authnSource: '', yht: { clientId: '' } },
    track: { enable: false, uploadId: '', apiHost: '', method: 'post' },
    apiTrack: { enable: false, businessId: '', traceBaggage: '', apiTrackHost: '' },
    environment: { dev: [], prod: [] },
    database: { connectionMode: 'dbid', schema: '', devDbid: '', prodDbid: '', host: '', port: '', username: '', password: '' },
    ...overrides
  } as ApplicationSchemaConfig
}

export const WORKSPACE_ROOT = 'C:\\Users\\WX\\Documents\\ExampleWorkspace\\wh-branch-pms'

export const pmsDevApplication: ApplicationConfig = {
  ...makeSchema(),
  id: 'app-pms-dev',
  name: '武汉分行项目管理系统',
  workspaceRoot: WORKSPACE_ROOT,
  source: 'new',
  enableAuth: false,
  enableTracking: false,
  pages: ['/my-projects', '/my-rechecks', '/recheck-review'],
  defaultPage: '/my-projects',
  schema: makeSchema(),
  planningConfirmedAt: Date.now() - 86400000 * 2,
  createdAt: Date.now() - 86400000 * 10
}

export const pmsDevWorkspaceApplication = { application: makeSchema() }
