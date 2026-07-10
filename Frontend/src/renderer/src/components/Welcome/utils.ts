import type { ApplicationDraft, ApplicationSchemaConfig } from '../../typings'

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

export function toProjectDirectoryName(value: string) {
  return (
    value
      .trim()
      .replace(/[<>:"/\\|?*\x00-\x1F]/g, '')
      .replace(/\s+/g, '-')
      .slice(0, 80) || ''
  )
}

export function joinLocalPath(parentPath: string, directoryName: string) {
  const parent = parentPath.trim().replace(/[\\/]+$/, '')
  const directory = directoryName.trim()
  if (!parent || !directory) return ''
  const separator = parent.includes('\\') ? '\\' : '/'
  return `${parent}${separator}${directory}`
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

export function validateProjectDirectoryName(_: unknown, value?: string) {
  if (!value?.trim()) return Promise.reject(new Error('请输入项目文件夹名'))
  if (/[<>:"/\\|?*\x00-\x1F]/.test(value.trim())) {
    return Promise.reject(new Error('项目文件夹名不能包含路径分隔符或特殊字符'))
  }
  return Promise.resolve()
}

function parseEnv(value?: string) {
  return (value ?? '')
    .split(/[\n,，、]/)
    .map((item) => item.trim())
    .filter(Boolean)
}

export function buildApplicationSchema(values: ApplicationDraft): ApplicationSchemaConfig {
  return {
    appName: values.appName.trim(),
    appIcon: values.appIcon.trim(),
    senario: values.senario.trim(),
    terminal: values.terminal,
    layout: values.layout,
    theme: values.theme,
    datasource: values.datasource,
    env: parseEnv(values.envText),
    menus: { homeMenuKey: '', items: [] },
    auth: values.auth,
    track: values.track,
    apiTrack: values.apiTrack
  }
}
