import { DatasourceEnum } from '../../typings'
import type {
  ApplicationDatasourceConfig,
  ApplicationDraft,
  ApplicationSchemaConfig
} from '../../typings'

/** 生成仅用于本地应用索引的新应用标识。 */
export function createApplicationId(): string {
  return `${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/** 从跨平台路径中提取最后一级目录名。 */
export function pathBasename(value: string): string {
  const normalizedValue = value.replace(/[\\/]+$/, '')
  return normalizedValue.split(/[\\/]/).pop() || normalizedValue || '未命名项目'
}

/** 从跨平台路径中提取父目录。 */
export function pathDirname(value: string): string {
  const normalizedValue = value.replace(/[\\/]+$/, '')
  const index = Math.max(normalizedValue.lastIndexOf('/'), normalizedValue.lastIndexOf('\\'))
  return index > 0 ? normalizedValue.slice(0, index) : ''
}

/** 把未知异常转换成可展示的错误文案。 */
export function formatError(error: unknown, fallback: string): string {
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

/** 把表单中的环境标签文本拆分为非空字符串列表。 */
function parseEnv(value?: string): string[] {
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
  if (!database) {
    throw new Error('请选择数据库类型。')
  }
  if (database.useBuiltin === true) {
    return {
      type: DatasourceEnum.DB,
      db: { useBuiltin: true }
    }
  }
  if (database.useBuiltin !== false) {
    throw new Error('请选择模拟数据库或外部数据库。')
  }

  if (database.connectionMode === 'dbid') {
    const dbidMode = database.dbidMode
    if (!dbidMode) throw new Error('外部数据库必须填写 DBID 连接信息。')
    return {
      type: DatasourceEnum.DB,
      db: {
        useBuiltin: false,
        dbidMode: {
          dbid: String(dbidMode.dbid || '').trim(),
          domain: String(dbidMode.domain || '').trim(),
          port: Number(dbidMode.port),
          userName: String(dbidMode.userName || '').trim(),
          schema: String(dbidMode.schema || '').trim()
        }
      }
    }
  }
  if (database.connectionMode !== 'plant') {
    throw new Error('请选择数据库连接方案。')
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
