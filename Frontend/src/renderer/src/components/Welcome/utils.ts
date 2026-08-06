import { DatasourceEnum } from '../../typings'
import type {
  ApplicationDatasourceConfig,
  ApplicationDraft,
  ApplicationSchemaConfig
} from '../../typings'

export function createApplicationId() {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

export function pathBasename(value: string) {
  const normalizedValue = value.replace(/[\\/]+$/, '')
  return normalizedValue.split(/[\\/]/).pop() || normalizedValue || '未命名项目'
}

export function pathDirname(value: string) {
  const normalizedValue = value.replace(/[\\/]+$/, '')
  const index = Math.max(normalizedValue.lastIndexOf('/'), normalizedValue.lastIndexOf('\\'))
  return index > 0 ? normalizedValue.slice(0, index) : ''
}

export function formatError(error: unknown, fallback: string) {
  return error instanceof Error ? error.message : fallback
}

export function formatHistoryTime(value: number): string {
  if (!value) return '未知时间'
  return new Intl.DateTimeFormat('zh-CN', {
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit'
  }).format(value)
}

function parseEnv(value?: string) {
  return (value ?? '')
    .split(/[\n,，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

/** 将表单数据源草稿收敛为统一的小写正式数据源配置。 */
export function buildDatasourceConfig(
  draft: ApplicationDraft['datasource']
): ApplicationDatasourceConfig {
  const datasourceType = draft?.type ?? DatasourceEnum.DB
  if (datasourceType === DatasourceEnum.STATIC) {
    return { type: DatasourceEnum.STATIC }
  }
  if (datasourceType === DatasourceEnum.API) {
    throw new Error('外部 API 数据源暂未启用。')
  }

  const databaseDraft = draft as Extract<
    ApplicationDraft['datasource'],
    { type: DatasourceEnum.DB }
  >
  const database = databaseDraft.db
  if (!database || database.useBuiltin !== false || database.connectionMode !== 'plant') {
    throw new Error('当前仅支持通过账号密码连接外部数据库。')
  }

  const plantMode = database.plantMode
  if (!plantMode) throw new Error('外部数据库必须填写账号密码连接信息。')
  return {
    type: DatasourceEnum.DB,
    db: {
      useBuiltin: false,
      plantMode: {
        domain: String(plantMode.domain || '').trim(),
        port: Number(plantMode.port),
        userName: String(plantMode.userName || '').trim(),
        pwd: String(plantMode.pwd || '').trim(),
        schema: String(plantMode.schema || '').trim()
      }
    }
  }
}

// 把新建应用表单转换为可写入 application.json 的初始配置。
export function buildApplicationSchema(values: ApplicationDraft): ApplicationSchemaConfig {
  return {
    appName: values.appName.trim(),
    appIcon: values.appIcon.trim(),
    senario: values.senario.trim(),
    terminal: values.terminal,
    layout: values.layout,
    theme: values.theme,
    datasource: buildDatasourceConfig(values.datasource),
    env: parseEnv(values.envText),
    menus: { ...values.menus, homeMenuKey: '', items: [] },
    apis: [],
    auth: values.auth,
    track: values.track,
    apiTrack: values.apiTrack,
    environment: values.environment,
    database: values.database
  }
}
