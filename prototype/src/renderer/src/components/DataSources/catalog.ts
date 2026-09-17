import { useEffect, useMemo, useState } from 'react'

/**
 * 数据来源目录 v6：表维度 + 接口维度两个平铺清单。
 * 数据库按「连接（配置）→ 已添加的表（清单主体）」组织，添加是有步骤的：
 * 先绑定数据库连接，再勾选表添加；外部 API 不再有站点/目录层级，每条来源就是一个
 * 接口，参数按 Postman 语义维护（Headers / Query / 入参 / 出参 Response），
 * 供开发阶段绑定映射消费。
 */

export type DatabaseTableColumn = { name: string; comment: string }
export type DatabaseTable = {
  name: string
  comment: string
  /** 是否已被应用添加；只有已添加的表才进入数据来源清单并可被绑定映射引用。 */
  imported?: boolean
  columns: DatabaseTableColumn[]
}
/** 数据库接入模式：模拟数据库免配置（开发环境的本地模拟库）；DBID 凭实例标识接入；本地直连接入本机自装的数据库软件。 */
// 注意概念边界：AIStudio 的产物是代码与脚本，生产部署由用户使用行内资源完成，
// 平台只承担开发任务——因此这里是“模拟数据库”，不是低代码平台自带运行时的“内置数据库”。
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
  /** 连接下系统发现的全部表；imported 标记哪些已被应用添加。 */
  tables: DatabaseTable[]
}

export type ExternalApiField = { name: string; comment: string }
/** 外部接口参数的请求部位：路径参数拼 URL、查询参数拼查询串、请求体进 JSON（POST/PUT 才有）。 */
export type ExternalApiParamLocation = 'path' | 'query' | 'body'
/** 外部接口入参：出入参粒度绑定时的映射来源侧，location 声明值在请求中的部位。 */
export type ExternalApiParam = {
  name: string
  comment: string
  required?: boolean
  location: ExternalApiParamLocation
}
/** 请求头：名称 + 取值说明。请求头不参与映射，由平台统一注入或在来源处固定配置。 */
export type ExternalApiHeader = { name: string; value: string }
export type ExternalApiMethod = 'GET' | 'POST' | 'PUT' | 'DELETE'

/** 参数部位的中文标签：映射分组、下拉选项与适配视图共用同一口径。 */
export const EXTERNAL_PARAM_LOCATION_LABEL: Record<ExternalApiParamLocation, string> = {
  path: '路径参数',
  query: '查询参数',
  body: '请求体'
}

/**
 * 外部 API 来源：每条就是一个接口（不再有站点/目录层级）。
 * url 为完整地址，路径参数用 {id} 占位；requestParams 收录全部入参并逐条声明部位。
 */
export type ExternalApiSource = {
  type: 'external_service'
  id: string
  name: string
  description: string
  method: ExternalApiMethod
  url: string
  headers: ExternalApiHeader[]
  requestParams: ExternalApiParam[]
  responseFields: ExternalApiField[]
}

/** 外部接口入参按部位分组：映射表单的「路径参数 / 查询参数 / 请求体」分组依据。 */
export function groupExternalRequestParams(params: ExternalApiParam[]): {
  path: ExternalApiParam[]
  query: ExternalApiParam[]
  body: ExternalApiParam[]
} {
  return {
    path: params.filter((param) => param.location === 'path'),
    query: params.filter((param) => param.location === 'query'),
    body: params.filter((param) => param.location === 'body')
  }
}

export type DataSource = DatabaseDataSource | ExternalApiSource

// 结构升级即换缓存键：旧目录数据直接丢弃，不做兼容迁移。
// v11：外部接口入参合并为单一 requestParams 并逐条声明部位（path/query/body），旧 Query/入参两清单作废。
const STORAGE_KEY = 'aistudio:prototype:data-sources:v11'
const CHANGE_EVENT = 'aistudio:prototype:data-sources-changed'

export const DATABASE_MODE_LABEL: Record<DatabaseSourceMode, string> = {
  builtin: '模拟数据库',
  dbid: 'DBID',
  direct: '本地直连'
}

/** 构造目录中的一张表：默认“已发现、未添加”；imported 为 true 表示已添加进清单。 */
function discoveredTable(
  name: string,
  comment: string,
  columns: Array<[string, string]>,
  imported = false
): DatabaseTable {
  return {
    name,
    comment,
    ...(imported ? { imported: true } : {}),
    columns: columns.map(([columnName, columnComment]) => ({ name: columnName, comment: columnComment }))
  }
}

/** 与回检演示案例配套的默认数据来源目录。 */
export const DEFAULT_DATA_SOURCES: DataSource[] = [
  {
    type: 'database',
    id: 'wuhan-recheck-db',
    name: 'RECHECK_DB',
    mode: 'direct',
    domain: 'mysql.wuhan-branch.internal',
    port: 3306,
    schema: 'recheck',
    userName: 'recheck_ro',
    dbid: '',
    hasPassword: true,
    tables: [
      discoveredTable('api_access_log', '接口访问日志', [['log_id', '日志编号'], ['api_path', '接口路径'], ['cost_ms', '耗时毫秒']]),
      discoveredTable('approval_flow', '审批流程', [['flow_id', '流程编号'], ['flow_name', '流程名称'], ['node_count', '节点数']]),
      discoveredTable('audit_log', '审计日志', [['log_id', '日志编号'], ['operator', '操作人'], ['detail', '审计明细']]),
      discoveredTable('contract', '合同', [['contract_id', '合同编号'], ['contract_name', '合同名称'], ['amount', '合同金额']]),
      discoveredTable('customer', '客户', [['customer_id', '客户编号'], ['customer_name', '客户名称'], ['level', '客户等级']]),
      discoveredTable('datasource_conf', '外部数据源配置', [['conf_id', '配置编号'], ['conf_name', '配置名称'], ['jdbc_url', '连接串']]),
      discoveredTable('department', '部门', [['dept_id', '部门编号'], ['dept_name', '部门名称'], ['parent_id', '上级部门']]),
      discoveredTable('export_task', '导出任务', [['task_id', '任务编号'], ['task_name', '任务名称'], ['status', '导出状态']]),
      discoveredTable('feedback', '反馈', [['feedback_id', '反馈编号'], ['content', '反馈内容'], ['submitter', '反馈人']]),
      discoveredTable('file_attachment', '附件', [['file_id', '文件编号'], ['file_name', '文件名称'], ['file_size', '文件大小']]),
      discoveredTable('invoice', '发票', [['invoice_id', '发票编号'], ['invoice_no', '发票号码'], ['amount', '开票金额']]),
      discoveredTable('issue_record', '问题记录', [['issue_id', '问题编号'], ['issue_title', '问题标题'], ['severity', '严重程度']]),
      discoveredTable('login_log', '登录日志', [['log_id', '日志编号'], ['login_user', '登录用户'], ['login_at', '登录时间']]),
      discoveredTable('mail_record', '邮件记录', [['mail_id', '记录编号'], ['receiver', '收件人'], ['subject', '邮件主题']]),
      discoveredTable('menu', '菜单配置', [['menu_id', '菜单编号'], ['menu_name', '菜单名称'], ['menu_path', '菜单路径']]),
      discoveredTable('message', '消息', [['msg_id', '消息编号'], ['sender', '发送人'], ['content', '消息内容']]),
      discoveredTable('metric_def', '指标定义', [['metric_id', '指标编号'], ['metric_name', '指标名称'], ['unit', '计量单位']]),
      discoveredTable('notice', '系统公告', [['notice_id', '公告编号'], ['title', '公告标题'], ['published_at', '发布时间']]),
      discoveredTable('operation_log', '操作日志', [['log_id', '日志编号'], ['operator', '操作人'], ['action', '操作内容'], ['created_at', '操作时间']]),
      discoveredTable('org', '组织架构', [['org_id', '组织编号'], ['org_name', '组织名称'], ['parent_id', '上级组织']]),
      discoveredTable('payment_record', '支付流水', [['pay_id', '流水编号'], ['pay_amount', '支付金额'], ['pay_time', '支付时间']]),
      discoveredTable('permission', '权限', [['perm_id', '权限编号'], ['perm_code', '权限码'], ['perm_name', '权限名称']]),
      discoveredTable('post', '岗位', [['post_id', '岗位编号'], ['post_name', '岗位名称']]),
      discoveredTable('project_info', '项目信息', [['project_id', '项目编号'], ['project_name', '项目名称'], ['owner', '项目负责人']]),
      discoveredTable('recheck', '需求回检记录', [['id', '回检编号'], ['project_name', '关联项目'], ['status', '处理状态'], ['created_by', '申请人工号'], ['created_at', '提交时间']], true),
      discoveredTable('recheck_audit', '审核轨迹', [['id', '轨迹编号'], ['recheck_id', '回检编号'], ['result', '审核结论'], ['reviewer_id', '审核人工号'], ['created_at', '审核时间']], true),
      discoveredTable('report_snapshot', '报表快照', [['snapshot_id', '快照编号'], ['report_name', '报表名称'], ['generated_at', '生成时间']]),
      discoveredTable('report_template', '报表模板', [['template_id', '模板编号'], ['template_name', '模板名称']]),
      discoveredTable('requirement_item', '需求条目', [['item_id', '条目编号'], ['item_title', '需求标题'], ['priority', '优先级']]),
      discoveredTable('role', '角色', [['role_id', '角色编号'], ['role_code', '角色码'], ['role_name', '角色名称']]),
      discoveredTable('scheduled_job', '定时任务', [['job_id', '任务编号'], ['job_name', '任务名称'], ['cron_expr', '调度表达式']]),
      discoveredTable('sms_record', '短信记录', [['sms_id', '记录编号'], ['phone', '手机号'], ['content', '短信内容']]),
      discoveredTable('sys_dict', '数据字典', [['dict_type', '字典类型'], ['dict_key', '字典键'], ['dict_value', '字典值']]),
      discoveredTable('todo_item', '待办事项', [['todo_id', '事项编号'], ['content', '事项内容'], ['finished', '是否完成']]),
      discoveredTable('user', '员工', [['id', '员工工号'], ['name', '姓名'], ['department', '所属部门']], true),
      discoveredTable('user_role', '用户角色关联', [['user_id', '用户编号'], ['role_id', '角色编号']]),
      discoveredTable('workflow_instance', '流程实例', [['instance_id', '实例编号'], ['instance_name', '实例名称'], ['state', '流程状态']]),
      discoveredTable('workflow_task', '待办任务', [['task_id', '任务编号'], ['assignee', '办理人'], ['arrived_at', '到达时间']]),
        ]
  },
  {
    type: 'database',
    id: 'project-db',
    name: 'PROJECT_DB',
    mode: 'builtin',
    domain: '',
    port: undefined,
    schema: '',
    userName: '',
    dbid: '',
    hasPassword: false,
    tables: [
      discoveredTable('app_log', '应用日志', [['log_id', '日志编号'], ['level', '日志级别'], ['content', '日志内容']]),
      discoveredTable('cache_kv', '键值缓存', [['cache_key', '缓存键'], ['cache_value', '缓存值']]),
      discoveredTable('i18n_text', '多语言文案', [['text_key', '文案键'], ['text_value', '文案内容'], ['lang', '语言']]),
      discoveredTable('page_draft', '页面草稿', [['draft_id', '草稿编号'], ['page_name', '页面名称'], ['content', '草稿内容']]),
      discoveredTable('recheck', '回检单', [['id', '回检编号'], ['project_name', '关联项目'], ['status', '处理状态'], ['created_by', '申请人工号'], ['created_at', '提交时间']], true),
      discoveredTable('recheck_comment', '回检备注', [['id', '备注编号'], ['recheck_id', '回检编号'], ['content', '备注内容'], ['created_by', '备注人工号'], ['created_at', '备注时间']], true),
      discoveredTable('session_record', '会话记录', [['session_id', '会话编号'], ['login_user', '登录用户'], ['active_at', '活跃时间']]),
      discoveredTable('sys_config', '系统配置', [['config_key', '配置项'], ['config_value', '配置值']]),
      discoveredTable('tag', '标签', [['tag_id', '标签编号'], ['tag_name', '标签名称']]),
      discoveredTable('template', '页面模板', [['template_id', '模板编号'], ['template_name', '模板名称']]),
      discoveredTable('upload_file', '上传文件', [['file_id', '文件编号'], ['file_name', '文件名称'], ['upload_at', '上传时间']]),
      discoveredTable('user_pref', '用户偏好', [['user_id', '用户编号'], ['pref_key', '偏好项'], ['pref_value', '偏好值']]),
      discoveredTable('version_info', '版本信息', [['version_id', '版本编号'], ['version_no', '版本号'], ['released_at', '发布时间']]),
        ]
  },
  {
    type: 'external_service',
    id: 'query-user',
    name: '查询用户信息',
    description: '按员工工号返回姓名与所属部门。',
    method: 'GET',
    url: 'https://user-center.wuhan-branch.example/users/{id}',
    headers: [{ name: 'Accept', value: 'application/json' }],
    requestParams: [{ name: 'id', comment: '员工工号', required: true, location: 'path' }],
    responseFields: [
      { name: 'name', comment: '姓名' },
      { name: 'department', comment: '所属部门' }
    ]
  },
  {
    type: 'external_service',
    id: 'send-notify',
    name: '发送消息通知',
    description: '向指定员工发送站内提醒消息。',
    method: 'POST',
    url: 'https://notify.wuhan-branch.example/messages',
    headers: [{ name: 'Content-Type', value: 'application/json' }],
    requestParams: [
      { name: 'channel', comment: '通知渠道，默认站内', required: false, location: 'query' },
      { name: 'userId', comment: '接收人工号', required: true, location: 'body' },
      { name: 'content', comment: '消息内容', required: true, location: 'body' }
    ],
    responseFields: [
      { name: 'messageId', comment: '消息编号' },
      { name: 'sentAt', comment: '发送时间' }
    ]
  },
  {
    type: 'external_service',
    id: 'recheck-center',
    name: '回检中心查询',
    description: '按处理状态查询回检单列表，返回回检明细。',
    method: 'GET',
    url: 'https://recheck-center.wuhan-branch.example/api/rechecks',
    headers: [{ name: 'Accept', value: 'application/json' }],
    requestParams: [
      { name: 'status', comment: '处理状态', required: false, location: 'query' },
      { name: 'page', comment: '页码，从 1 起', required: false, location: 'query' }
    ],
    responseFields: [
      { name: 'recheck_id', comment: '回检单号' },
      { name: 'project_name', comment: '关联项目' },
      { name: 'status', comment: '处理状态' },
      { name: 'created_by', comment: '申请人工号' },
      { name: 'created_at', comment: '提交时间' }
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

/** 在多个应用页面间共享同一份演示数据来源目录。 */
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

/** 提取外部接口 URL 中的路径部分：`https://host/users/{id}` → `/users/{id}`；decodeURI 还原花括号占位符，非标准地址原样返回。 */
export function externalApiPath(source: Pick<ExternalApiSource, 'url'>): string {
  try {
    return decodeURI(new URL(source.url).pathname)
  } catch {
    return source.url
  }
}

/** 接口的契约签名：`GET /users/{id}`，与绑定引用、产物树共用同一格式。 */
export function externalApiSignature(source: Pick<ExternalApiSource, 'method' | 'url'>): string {
  return `${source.method} ${externalApiPath(source)}`
}

/** 一个已添加的数据库表：清单主体，标注其所属连接。 */
export type ImportedDatabaseTable = {
  sourceId: string
  sourceName: string
  sourceMode: DatabaseSourceMode
  table: DatabaseTable
}

/** 汇总各连接下已添加的表，作为数据库页签的平铺清单主体。 */
export function importedTables(sources: DataSource[]): ImportedDatabaseTable[] {
  return sources.flatMap((source) =>
    source.type === 'database'
      ? source.tables
          .filter((table) => table.imported)
          .map((table) => ({
            sourceId: source.id,
            sourceName: source.name,
            sourceMode: source.mode,
            table
          }))
      : []
  )
}

/** 生成来源的稳定资源键：库表/服务接口共用同一命名空间，供绑定引用对齐。 */
export function bindingKey(sourceId: string, targetName: string): string {
  return `${sourceId}::${targetName}`
}

/** 一个可绑定目标的完整信息：已添加的库表或外部接口，含出入参字段候选。 */
export type BindableTarget = {
  key: string
  sourceId: string
  sourceName: string
  sourceKind: 'database' | 'external_service'
  /** 表名或“GET /users/{id}”式接口签名。 */
  targetName: string
  /** 表说明或接口描述。 */
  targetComment: string
  /** 出参候选：表列或接口响应字段。 */
  fields: Array<{ name: string; comment: string }>
  /** 入参候选：仅外部接口具备（逐条带请求部位），库表为空数组。 */
  requestParams: ExternalApiParam[]
  /** 外部接口的 HTTP 方法；库表目标为空字符串。映射分组与请求预览按它决定可用部位。 */
  method: ExternalApiMethod | ''
}

/** 把整个目录展开为可绑定目标列表；只有已添加的表和已登记的接口会出现在这份扁平视图。 */
export function flattenTargets(sources: DataSource[]): BindableTarget[] {
  return sources.flatMap((source): BindableTarget[] => {
    if (source.type === 'database') {
      return source.tables
        .filter((table) => table.imported)
        .map((table) => ({
          key: bindingKey(source.id, table.name),
          sourceId: source.id,
          sourceName: source.name,
          sourceKind: 'database',
          targetName: table.name,
          targetComment: table.comment,
          fields: table.columns.map((column) => ({ name: column.name, comment: column.comment })),
          requestParams: [],
          method: ''
        }))
    }
    return [
      {
        key: bindingKey(source.id, externalApiSignature(source)),
        sourceId: source.id,
        sourceName: source.name,
        sourceKind: 'external_service',
        targetName: externalApiSignature(source),
        targetComment: source.description,
        fields: source.responseFields,
        requestParams: source.requestParams,
        method: source.method
      }
    ]
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
