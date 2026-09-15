import { useEffect, useMemo, useState } from 'react'

/**
 * 数据来源目录 v3：对齐正式工程“数据库 + 外部 API（域名→目录→接口）”的目录结构。
 * 数据库细化为表与字段，外部服务细化为目录、接口与响应字段，供开发阶段绑定映射消费。
 */

export type DatabaseTableColumn = { name: string; comment: string }
export type DatabaseTable = { name: string; comment: string; columns: DatabaseTableColumn[] }
/** 数据库接入模式：内置数据库免配置；DBID 凭实例标识接入；本地直连接入本机自装的数据库软件。 */
export type DatabaseSourceMode = 'builtin' | 'dbid' | 'direct'

export type DatabaseDataSource = {
  type: 'database'
  id: string
  name: string
  mode: DatabaseSourceMode
  domain: string
  port?: number
  schema: string
  userName: string
  /** 仅 DBID 模式使用：数据源实例标识。 */
  dbid: string
  /** 原型不保存密码密文，只记录是否已配置，驱动“密码已配置/缺少密码”标签。 */
  hasPassword: boolean
  tables: DatabaseTable[]
}

export type ExternalApiField = { name: string; comment: string }
export type ExternalApiMethod = 'GET' | 'POST' | 'PUT' | 'DELETE'
export type ExternalApiOperation = {
  id: string
  name: string
  method: ExternalApiMethod
  path: string
  description: string
  responseFields: ExternalApiField[]
}
export type ExternalApiDirectory = {
  id: string
  name: string
  operations: ExternalApiOperation[]
}
export type ExternalApiSource = {
  type: 'external_service'
  id: string
  name: string
  baseUrl: string
  directories: ExternalApiDirectory[]
}

export type DataSource = DatabaseDataSource | ExternalApiSource

// 结构升级即换缓存键：旧目录数据直接丢弃，不做兼容迁移。
const STORAGE_KEY = 'aistudio:prototype:data-sources:v4'
const CHANGE_EVENT = 'aistudio:prototype:data-sources-changed'

export const DATABASE_MODE_LABEL: Record<DatabaseSourceMode, string> = {
  builtin: '内置数据库',
  dbid: 'DBID',
  direct: '本地直连'
}

/** 与回检演示案例配套的默认数据来源目录。 */
export const DEFAULT_DATA_SOURCES: DataSource[] = [
  {
    type: 'database',
    id: 'wuhan-recheck-db',
    name: '武汉回检数据库',
    mode: 'direct',
    domain: 'mysql.wuhan-branch.internal',
    port: 3306,
    schema: 'recheck',
    userName: 'recheck_ro',
    dbid: '',
    hasPassword: true,
    tables: [
      {
        name: 'recheck',
        comment: '需求回检记录',
        columns: [
          { name: 'id', comment: '回检编号' },
          { name: 'project_name', comment: '关联项目' },
          { name: 'status', comment: '处理状态' },
          { name: 'created_by', comment: '申请人工号' },
          { name: 'created_at', comment: '提交时间' }
        ]
      },
      {
        name: 'recheck_audit',
        comment: '审核轨迹',
        columns: [
          { name: 'id', comment: '轨迹编号' },
          { name: 'recheck_id', comment: '回检编号' },
          { name: 'result', comment: '审核结论' },
          { name: 'reviewer_id', comment: '审核人工号' },
          { name: 'created_at', comment: '审核时间' }
        ]
      },
      {
        name: 'user',
        comment: '员工',
        columns: [
          { name: 'id', comment: '员工工号' },
          { name: 'name', comment: '姓名' },
          { name: 'department', comment: '所属部门' }
        ]
      }
    ]
  },
  {
    type: 'database',
    id: 'project-db',
    name: '本项目数据库',
    mode: 'builtin',
    domain: '',
    port: undefined,
    schema: '',
    userName: '',
    dbid: '',
    hasPassword: false,
    tables: [
      {
        name: 'recheck',
        comment: '回检单',
        columns: [
          { name: 'id', comment: '回检编号' },
          { name: 'project_name', comment: '关联项目' },
          { name: 'status', comment: '处理状态' },
          { name: 'created_by', comment: '申请人工号' },
          { name: 'created_at', comment: '提交时间' }
        ]
      },
      {
        name: 'recheck_comment',
        comment: '回检备注',
        columns: [
          { name: 'id', comment: '备注编号' },
          { name: 'recheck_id', comment: '回检编号' },
          { name: 'content', comment: '备注内容' },
          { name: 'created_by', comment: '备注人工号' },
          { name: 'created_at', comment: '备注时间' }
        ]
      }
    ]
  },
  {
    type: 'external_service',
    id: 'user-center',
    name: '用户中心',
    baseUrl: 'https://user-center.wuhan-branch.example',
    directories: [
      {
        id: 'staff',
        name: '员工信息',
        operations: [
          {
            id: 'query-user',
            name: '查询用户信息',
            method: 'GET',
            path: '/users/{id}',
            description: '按员工工号返回姓名与所属部门。',
            responseFields: [
              { name: 'name', comment: '姓名' },
              { name: 'department', comment: '所属部门' }
            ]
          }
        ]
      }
    ]
  }
]

/** 从浏览器缓存读取演示数据来源；损坏数据自动回落到与回检案例配套的默认目录。 */
export function readDataSources(): DataSource[] {
  if (typeof window === 'undefined') return DEFAULT_DATA_SOURCES
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    return saved ? (JSON.parse(saved) as DataSource[]) : DEFAULT_DATA_SOURCES
  } catch {
    return DEFAULT_DATA_SOURCES
  }
}

/** 保存完整数据来源目录并通知其它工作区同步刷新。 */
export function writeDataSources(sources: DataSource[]): void {
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(sources))
  window.dispatchEvent(new CustomEvent(CHANGE_EVENT))
}

/** 在多个页面间共享同一份演示数据来源目录。 */
export function useDataSources(): [DataSource[], (sources: DataSource[]) => void] {
  const [sources, setSources] = useState<DataSource[]>(readDataSources)
  useEffect(() => {
    const refresh = (): void => setSources(readDataSources())
    window.addEventListener(CHANGE_EVENT, refresh)
    window.addEventListener('storage', refresh)
    return () => {
      window.removeEventListener(CHANGE_EVENT, refresh)
      window.removeEventListener('storage', refresh)
    }
  }, [])
  /** 同时更新当前组件和共享缓存。 */
  const save = (next: DataSource[]): void => {
    setSources(next)
    writeDataSources(next)
  }
  return [sources, save]
}

/** 统计外部服务下的接口总数，用于目录行与删除提示。 */
export function externalOperationCount(source: ExternalApiSource): number {
  return source.directories.reduce((total, directory) => total + directory.operations.length, 0)
}

/** 生成来源的稳定资源键：库表/服务接口共用同一命名空间，供绑定引用对齐。 */
export function bindingKey(sourceId: string, targetName: string): string {
  return `${sourceId}::${targetName}`
}

/** 一个可绑定目标的完整信息：库表或服务接口，含字段候选。 */
export type BindableTarget = {
  key: string
  sourceId: string
  sourceName: string
  sourceKind: 'database' | 'external_service'
  /** 表名或“GET /users/{id}”式接口签名。 */
  targetName: string
  /** 表说明或接口名称。 */
  targetComment: string
  fields: Array<{ name: string; comment: string }>
}

/** 把整个目录展开为可绑定目标列表；绑定映射与引用检测都基于这份扁平视图。 */
export function flattenTargets(sources: DataSource[]): BindableTarget[] {
  return sources.flatMap((source): BindableTarget[] => {
    if (source.type === 'database') {
      return source.tables.map((table) => ({
        key: bindingKey(source.id, table.name),
        sourceId: source.id,
        sourceName: source.name,
        sourceKind: 'database',
        targetName: table.name,
        targetComment: table.comment,
        fields: table.columns.map((column) => ({ name: column.name, comment: column.comment }))
      }))
    }
    return source.directories.flatMap((directory) =>
      directory.operations.map((operation) => ({
        key: bindingKey(source.id, `${operation.method} ${operation.path}`),
        sourceId: source.id,
        sourceName: source.name,
        sourceKind: 'external_service',
        targetName: `${operation.method} ${operation.path}`,
        targetComment: `${directory.name} · ${operation.name}`,
        fields: operation.responseFields
      }))
    )
  })
}

/** 跨面板共享的目录索引：按来源分组的目标列表 + 来源查找表。 */
export function useDataSourceIndex(): {
  sources: DataSource[]
  databases: DatabaseDataSource[]
  externalServices: ExternalApiSource[]
  targets: BindableTarget[]
  targetByKey: Map<string, BindableTarget>
  sourceById: Map<string, DataSource>
} {
  const [sources] = useDataSources()
  return useMemo(() => {
    const databases = sources.filter((item): item is DatabaseDataSource => item.type === 'database')
    const externalServices = sources.filter(
      (item): item is ExternalApiSource => item.type === 'external_service'
    )
    const targets = flattenTargets(sources)
    return {
      sources,
      databases,
      externalServices,
      targets,
      targetByKey: new Map(targets.map((target) => [target.key, target])),
      sourceById: new Map(sources.map((source) => [source.id, source]))
    }
  }, [sources])
}
