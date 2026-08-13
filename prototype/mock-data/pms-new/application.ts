// 武汉分行需求回检系统 · 新建应用参考配置（两个页面与一个查询接口）。
import type { ApplicationConfig, ApplicationSchemaConfig } from '../../src/renderer/src/typings'
import { makeCompleteLifecycle } from './lifecycle'

export const PMS_MENU_ITEMS = [
  {
    key: 'rechecks',
    type: 'menu',
    label: '需求回检',
    children: [
      {
        key: 'recheck-introduction',
        type: 'page',
        label: '回检介绍',
        path: '/recheck-introduction'
      },
      { key: 'my-rechecks', type: 'page', label: '我的回检', path: '/my-rechecks' }
    ]
  }
] as unknown as ApplicationSchemaConfig['menus']['items']

export function makeSchema(
  overrides: Partial<ApplicationSchemaConfig> = {}
): ApplicationSchemaConfig {
  return {
    appName: '武汉分行需求回检系统',
    appIcon: 'ProjectOutlined',
    senario: '需求回检填报与审核',
    terminal: 'PC',
    layout: { type: 'side', useHeader: true, useFooter: false },
    theme: { primaryColor: '#6b3cf0' },
    datasource: {
      type: 'DataBase',
      db: {
        useBuiltin: false,
        connectionMode: 'plant',
        plantMode: { domain: '', port: '', userName: '', pwd: '', schema: '' }
      }
    },
    env: ['dev'],
    menus: { enable: true, rootPath: '/page', homeMenuKey: 'my-rechecks', items: PMS_MENU_ITEMS },
    apis: [],
    auth: { enable: false, authnSource: '', yht: { clientId: '' } },
    track: { enable: false, uploadId: '', apiHost: '', method: 'post' },
    apiTrack: { enable: false, businessId: '', traceBaggage: '', apiTrackHost: '' },
    environment: { dev: [], prod: [] },
    database: {
      connectionMode: 'dbid',
      schema: '',
      devDbid: '',
      prodDbid: '',
      host: '',
      port: '',
      username: '',
      password: ''
    },
    ...overrides
  } as ApplicationSchemaConfig
}

// 新建应用工作区目录（目录选择器默认值；CreateApplicationAction 实际用用户所选路径）。
export const WORKSPACE_ROOT = 'C:\\Users\\WX\\Documents\\ExampleWorkspace\\wh-branch-pms-new'

export const pmsNewApplication: ApplicationConfig = {
  ...makeSchema(),
  id: 'app-pms-new',
  name: '武汉分行需求回检系统',
  workspaceRoot: WORKSPACE_ROOT,
  source: 'new',
  enableAuth: false,
  enableTracking: false,
  pages: ['/recheck-introduction', '/my-rechecks'],
  defaultPage: '/recheck-introduction',
  schema: makeSchema(),
  planningConfirmedAt: Date.now(),
  createdAt: Date.now(),
  // 版本演示:v1.3 为当前已发布版本，v1.0-v1.2 保留为可切换的只读历史。
  versions: [
    {
      id: 'app-pms-new-v1-0',
      versionLabel: 'v1.0',
      major: 1,
      minor: 0,
      status: 'released',
      createdAt: Date.now() - 7 * 86400000,
      releasedAt: Date.now() - 2 * 86400000,
      lifecycle: makeCompleteLifecycle('app-pms-new', '武汉分行需求回检系统'),
      description: '首版上线,支持个人回检记录查询。',
      snapshot: {
        pageIds: ['/recheck-introduction', '/my-rechecks'],
        requirementSummary: '首版上线个人回检记录查询。'
      }
    },
    {
      id: 'app-pms-new-v1-1',
      versionLabel: 'v1.1',
      major: 1,
      minor: 1,
      status: 'released',
      parentVersionId: 'app-pms-new-v1-0',
      createdAt: Date.now() - 5 * 86400000,
      releasedAt: Date.now() - 4 * 86400000,
      lifecycle: makeCompleteLifecycle('app-pms-new', '武汉分行需求回检系统'),
      description: '新增状态筛选和待办统计。',
      snapshot: {
        pageIds: ['/recheck-introduction', '/my-rechecks'],
        requirementSummary: '新增状态筛选和待办统计。'
      }
    },
    {
      id: 'app-pms-new-v1-2',
      versionLabel: 'v1.2',
      major: 1,
      minor: 2,
      status: 'released',
      parentVersionId: 'app-pms-new-v1-1',
      createdAt: Date.now() - 3 * 86400000,
      releasedAt: Date.now() - 2 * 86400000,
      lifecycle: makeCompleteLifecycle('app-pms-new', '武汉分行需求回检系统'),
      description: '新增在线提交回检单。',
      snapshot: {
        pageIds: ['/recheck-introduction', '/my-rechecks'],
        requirementSummary: '新增在线提交回检单。'
      }
    },
    {
      id: 'app-pms-new-v1-3',
      versionLabel: 'v1.3',
      major: 1,
      minor: 3,
      status: 'released',
      parentVersionId: 'app-pms-new-v1-2',
      createdAt: Date.now() - 86400000,
      releasedAt: Date.now() - 3600000,
      lifecycle: makeCompleteLifecycle('app-pms-new', '武汉分行需求回检系统'),
      description: '新增关键词检索、紧急程度、更新时间列和台账导出。',
      snapshot: {
        pageIds: ['/recheck-introduction', '/my-rechecks'],
        requirementSummary: '新增关键词检索、紧急程度、更新时间列和台账导出。'
      }
    }
  ],
  currentVersionId: 'app-pms-new-v1-3'
}

export const pmsNewWorkspaceApplication = { application: makeSchema() }
