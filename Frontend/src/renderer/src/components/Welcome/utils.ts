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

/** 将表单数据源草稿收敛为互斥且可持久化的数据库配置。 */
export function buildDatasourceConfig(
  draft: ApplicationDraft['datasource']
): ApplicationDatasourceConfig {
  const { connectionMode, useBuiltin } = draft.db
  if (useBuiltin) {
    return {
      type: DatasourceEnum.DB,
      db: { useBuiltin: true }
    }
  }
  if (connectionMode === 'dbid') {
    const dbidMode = draft.db.dbidMode
    return {
      type: DatasourceEnum.DB,
      db: {
        useBuiltin: false,
        dbidMode: {
          dbid: String(dbidMode?.dbid || '').trim(),
          userName: String(dbidMode?.userName || '').trim(),
          domain: String(dbidMode?.domain || '').trim(),
          port: Number(dbidMode?.port),
          schema: String(dbidMode?.schema || '').trim()
        }
      }
    }
  }
  if (connectionMode === 'plant') {
    const plantMode = draft.db.plantMode
    return {
      type: DatasourceEnum.DB,
      db: {
        useBuiltin: false,
        plantMode: {
          domain: String(plantMode?.domain || '').trim(),
          port: Number(plantMode?.port),
          userName: String(plantMode?.userName || '').trim(),
          pwd: String(plantMode?.pwd || '').trim(),
          schema: String(plantMode?.schema || '').trim()
        }
      }
    }
  }
  throw new Error('外部数据库必须选择连接方案。')
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
