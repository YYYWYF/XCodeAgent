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

export const initialApplicationDraft: ApplicationDraft = {
  appName: '',
  appIcon: defaultApplicationIcon,
  senario: '',
  projectPath: '',
  terminal: 'PC',
  layout: { type: 'side', useHeader: true, useFooter: false },
  theme: { primaryColor: '#2c68ff' },
  datasource: {
    type: '',
    db: {
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
