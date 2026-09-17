import type { AppApi } from '../../../AppApis/model'

/** 技术规划方案「应用API」页签里的单条接口投影。 */
export type ProjectedEndpoint = {
  id: string
  method: string
  path: string
  summary: string
  /** 来源应用API的 id，页面绑定反查接口时使用。 */
  operationId: string
  operationName: string
  /** 消费该接口的页面名（继承契约上的页面关联）。 */
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

/** 把接口名映射为 REST 方法：查询类→GET、删除→DELETE、更新/审核→PUT、其余→POST；查询类优先判定。 */
function methodForOperation(name: string): string {
  if (/^(查询|分页|获取|搜索)/.test(name)) return 'GET'
  if (name.includes('删除')) return 'DELETE'
  if (name.includes('更新') || name.includes('审核')) return 'PUT'
  return 'POST'
}

/** 组装单条接口的演示契约细节：参数、请求体、响应形态与错误码。 */
function describeEndpoint(
  operation: AppApi,
  method: string
): Pick<ProjectedEndpoint, 'params' | 'requestBody' | 'response' | 'errors'> {
  const outputs = operation.response.map((output) =>
    output.code ? `${output.code}（${output.name}）` : output.name
  )
  const isList =
    method === 'GET' && (operation.name.includes('分页') || operation.path.endsWith('/my'))
  const params = method.startsWith('GET')
    ? isList
      ? 'query：状态（可选）、page、size'
      : operation.path.includes('{id}')
        ? 'path：id'
        : 'query：按接口条件过滤'
    : '—'
  const requestBody =
    method === 'GET'
      ? '无'
      : `body：${outputs.slice(0, 3).join('、')}${outputs.length > 3 ? ' 等' : ''}`
  const response = isList
    ? `{ code, data: { list: ${operation.name}[], total }, message }`
    : method === 'GET'
      ? `{ code, data: { ${outputs.join(', ')} }, message }`
      : '{ code, data: 执行结果, message }'
  const errors = method.startsWith('GET')
    ? ['400 参数错误', '404 资源不存在', '500 服务异常']
    : ['400 参数错误', '500 服务异常']
  return { params, requestBody, response, errors }
}

/**
 * 把应用API清单投影为技术规划方案的接口视图：每条接口一个投影，
 * 契约的 method/path 直接沿用，参数、响应形态与错误码在此补全（技术细化属计划阶段）。
 */
export function apiEndpointsFromObjects(objects: AppApi[]): ProjectedEndpoint[] {
  return objects.map((operation) => {
    const method = operation.method || methodForOperation(operation.name)
    return {
      id: operation.id,
      method,
      path: operation.path,
      summary: operation.description || `${operation.name}的业务能力`,
      operationId: operation.id,
      operationName: operation.name,
      pages: operation.pages,
      intentSources: operation.implementation.intentSources,
      operationType: 'custom' as const,
      outputs: operation.response.map((output) =>
        output.code ? `${output.code}（${output.name}）` : output.name
      ),
      ...describeEndpoint(operation, method)
    }
  })
}
