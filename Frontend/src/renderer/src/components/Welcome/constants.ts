import { DatasourceEnum } from '../../typings'
import type { ApplicationDraft, ApplicationTerminal } from '../../typings'

/** 可选应用图标。value 为图标组件名，用于持久化与还原；label 为中文说明。 */
export const applicationIconOptions: ReadonlyArray<{ value: string; label: string }> = [
  { value: 'AppstoreOutlined', label: '应用商店' },
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

/** 新建应用可展示的数据源选项；外部 API 暂时只展示为禁用项。 */
export const datasourceTypeOptions: ReadonlyArray<{
  value: DatasourceEnum
  label: string
  description: string
  disabled?: boolean
}> = [
  {
    value: DatasourceEnum.DB,
    label: '数据库',
    description: '连接数据库进行增删改查。'
  },
  {
    value: DatasourceEnum.API,
    label: '外部 API',
    description: '直接对接外部 API，禁用。',
    disabled: true
  },
  {
    value: DatasourceEnum.STATIC,
    label: '静态数据',
    description: '前端直接生成静态模拟数据，仅测试用。'
  }
]

export const initialApplicationDraft: ApplicationDraft = {
  appName: '',
  appIcon: defaultApplicationIcon,
  senario: '',
  projectPath: '',
  terminal: 'PC',
  layout: { type: 'side', useHeader: true, useFooter: false },
  theme: { primaryColor: '#6b3cf0' },
  datasource: {
    type: DatasourceEnum.DB,
    db: {
      useBuiltin: false,
      connectionMode: 'plant'
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
  }
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
