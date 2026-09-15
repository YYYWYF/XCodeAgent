import type { BusinessObject, BusinessOperation } from '../../../BusinessObjects/model'

/** 技术规划方案「API 契约」页签里的单个接口投影。 */
export type ProjectedEndpoint = {
  id: string
  method: string
  path: string
  summary: string
  /** 来源实体操作的 id，页面绑定反查接口时使用。 */
  operationId: string
  operationName: string
  /** 消费该接口的页面名（继承操作上的页面关联）。 */
  pages: string[]
  /** 计划阶段记录的数据实现意向（来源级，开发阶段落实为具体绑定）。 */
  intentSources: string[]
  /** 演示契约的参数 / 请求体 / 响应 / 错误码描述。 */
  params: string
  requestBody: string
  response: string
  outputs: string[]
  errors: string[]
  operationType: 'builtin' | 'custom'
}

/** 技术规划方案「API 契约」页签里的单个资源契约：一个实体对应一个 REST 资源。 */
export type ProjectedContract = {
  id: string
  name: string
  basePath: string
  endpoints: ProjectedEndpoint[]
}

/** 把操作名映射为 REST 方法：查询类→GET、删除→DELETE、更新/审核→PUT、其余→POST；查询类优先判定。 */
function methodForOperation(name: string): string {
  if (/^(查询|分页|获取|搜索)/.test(name)) return 'GET'
  if (name.includes('删除')) return 'DELETE'
  if (name.includes('更新') || name.includes('审核')) return 'PUT'
  return 'POST'
}

/** 截取需求操作描述的一句话摘要：去掉 `操作()` 调用形态，取冒号后的第一个分句。 */
function summaryOfOperation(operation: BusinessOperation): string {
  const raw = operation.description || ''
  const afterColon = raw.includes('：') ? raw.split('：').slice(1).join('：') : raw
  return afterColon.split('；')[0].split('。')[0].trim() || `${operation.name}的业务能力`
}

/** 把操作映射为资源路径：内置 CRUD 模板路径；自定义动作优先用演示短 id，否则退化为 actions/{n}。 */
function pathForOperation(operation: BusinessOperation, basePath: string, index: number): string {
  const method = methodForOperation(operation.name)
  if (operation.name.includes('分页')) return basePath
  if (operation.name.includes('详情') || method === 'DELETE' || operation.name.includes('更新')) {
    return `${basePath}/{id}`
  }
  // 投影生成的操作 id 形如 `{entityId}-op-{n}`，没有可读语义，退化为 actions/{n}。
  if (operation.id.includes('-op-')) return `${basePath}/actions/${index + 1}`
  return `${basePath}/${operation.id.split('-').pop() || `actions/${index + 1}`}`
}

/** 组装单个接口的演示契约细节：参数、请求体、响应形态与错误码。 */
function describeEndpoint(
  operation: BusinessOperation,
  method: string,
  entityName: string
): Pick<ProjectedEndpoint, 'params' | 'requestBody' | 'response' | 'errors'> {
  const outputs = operation.outputs
  const isList =
    operation.name.includes('分页') || (method === 'GET' && operation.name.includes('我的'))
  const params = method.startsWith('GET')
    ? isList
      ? 'query：status、page、size'
      : operation.name.includes('详情')
        ? 'path：id'
        : 'query：按操作条件过滤'
    : '—'
  const requestBody =
    method === 'GET'
      ? '无'
      : `body：${outputs.slice(0, 3).join('、')}${outputs.length > 3 ? ' 等' : ''}`
  const response = isList
    ? `{ code, data: { list: ${entityName}[], total }, message }`
    : method === 'GET'
      ? `{ code, data: { ${outputs.join(', ')} }, message }`
      : '{ code, data: 执行结果, message }'
  const errors = method.startsWith('GET')
    ? ['400 参数错误', '404 资源不存在', '500 服务异常']
    : ['400 参数错误', '500 服务异常']
  return { params, requestBody, response, errors }
}

/**
 * 把实体投影为 REST 资源契约：每个对象一个资源（复数资源名），每个操作一个接口。
 * 这是计划阶段的演示投影，字段级绑定与实现由开发阶段完成，因此只承诺方法、路径与形态。
 */
export function apiContractsFromObjects(objects: BusinessObject[]): ProjectedContract[] {
  return objects.map((object) => {
    // 资源名取对象级表绑定（如 recheck → /api/rechecks）；未绑定时退化为对象 id。
    const table = String(object.tableBinding?.targetName || object.id)
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, '-')
    const basePath = `/api/${table.endsWith('s') ? table : `${table}s`}`
    const endpoints = object.operations.map((operation, index) => {
      const method = methodForOperation(operation.name)
      return {
        id: `${object.id}.${operation.id}`,
        method,
        path: pathForOperation(operation, basePath, index),
        summary:
          operation.operationType === 'custom'
            ? summaryOfOperation(operation)
            : operation.description,
        operationId: operation.id,
        operationName: operation.name,
        pages: operation.pages,
        intentSources: operation.implementation.intentSources,
        operationType: operation.operationType,
        outputs: operation.outputs,
        ...describeEndpoint(operation, method, object.name)
      }
    })
    return { id: `${object.id}-api`, name: object.name, basePath, endpoints }
  })
}
