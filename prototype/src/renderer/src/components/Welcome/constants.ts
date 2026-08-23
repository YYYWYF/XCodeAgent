import type { ApplicationDraft, ApplicationTerminal } from '../../typings'
import { applicationFormPrefill } from '@mock-data/application-form'

/** 可选应用图标。value 为图标组件名，用于持久化与还原；label 为中文说明。 */
export const applicationIconOptions: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'AppstoreOutlined', label: '应用商店' },
  { value: 'ProjectOutlined', label: '项目' },
  { value: 'DesktopOutlined', label: '桌面' },
  { value: 'DashboardOutlined', label: '仪表盘' },
  { value: 'ShopOutlined', label: '商城' },
  { value: 'ShoppingOutlined', label: '购物' },
  { value: 'TeamOutlined', label: '团队' },
  { value: 'UserOutlined', label: '用户' },
  { value: 'ToolOutlined', label: '工具' },
  { value: 'CloudOutlined', label: '云服务' },
  { value: 'MessageOutlined', label: '消息' },
  { value: 'BankOutlined', label: '机构' },
  { value: 'FundOutlined', label: '数据' }
]

/** 默认应用图标：列表中的第一个。 */
export const defaultApplicationIcon = applicationIconOptions[0].value

export const initialApplicationDraft: ApplicationDraft = {
  appName: '',
  appIcon: defaultApplicationIcon,
  senario: '',
  projectPath: '',
  terminal: 'PC',
  layout: { type: 'side', useHeader: true, useFooter: false },
  theme: { primaryColor: '#6b3cf0' },
  datasource: {
    type: 'None',
    db: {
      useBuiltin: false,
      connectionMode: 'plant',
      plantMode: { domain: '', port: '', userName: '', pwd: '', schema: '' }
    }
  },
  envText: '',
  auth: { enable: false, authnSource: '', yht: { clientId: '' } },
  track: { enable: false, uploadId: '', apiHost: '', method: 'post' },
  apiTrack: { enable: false, businessId: '', traceBaggage: '', apiTrackHost: '' },
  menus: { enable: true, rootPath: '/page' },
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
  // 演示案例预填（appName/场景/项目路径/主题色等）集中在 mock-data/application-form.ts
  ...applicationFormPrefill
}

export const terminalLabels: Record<ApplicationTerminal, string> = {
  PC: 'PC 端',
  Mobile: '移动端'
}

/** 请求方式下拉建议项（用户也可自由输入其他值）。 */
export const trackMethodOptions: Array<{ value: string; label: string }> = [
  { value: 'post', label: 'post' },
  { value: 'get', label: 'get' },
  { value: 'put', label: 'put' }
]

/** 数据源类型选项（对齐真实产品：外部 API 暂未开放，静态数据仅测试用）。 */
export const datasourceTypeOptions: Array<{
  value: 'DataBase' | 'API' | 'None'
  label: string
  description: string
  disabled?: boolean
}> = [
  { value: 'DataBase', label: '数据库', description: '连接数据库进行增删改查。' },
  {
    value: 'API',
    label: '外部 API',
    description: '直接对接外部 API（暂未开放）。',
    disabled: true
  },
  { value: 'None', label: '静态数据', description: '前端直接生成静态模拟数据，仅测试用。' }
]
