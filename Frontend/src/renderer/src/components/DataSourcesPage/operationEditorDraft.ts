import type { DataSourceFieldType, DataSourceOperation } from '../../typings'
import type { ParameterDraft } from './DataSourceParameterEditor'
import { operationPathParameterNames, synchronizePathParameters, validateOperationParameters } from './dataSourceOperations'
import { buildJsonSchema, readJsonSchemaMetadata } from './jsonSchema'

export type OperationBasic = Pick<DataSourceOperation, 'name' | 'method' | 'path' | 'description'>
export type OperationAddress = Pick<OperationBasic, 'method' | 'path'>
export type OperationUpdater = (latest: DataSourceOperation) => DataSourceOperation
type OperationConfiguration = Omit<DataSourceOperation, keyof OperationBasic | 'id'>

export type OperationDraft = Omit<DataSourceOperation, 'pathParameters' | 'queryParameters' | 'headers'> & {
  pathParameters: ParameterDraft[]
  queryParameters: ParameterDraft[]
  headers: ParameterDraft[]
  requestSampleText: string
  responseSampleText: string
  requestFieldDescriptionsDraft: Record<string, string>
  responseFieldDescriptionsDraft: Record<string, string>
  requestFieldTypesDraft: Record<string, DataSourceFieldType>
  responseFieldTypesDraft: Record<string, DataSourceFieldType>
}

/** 提取基本信息，用于卡片编辑和保存范围划分。 */
export function operationBasic(operation: DataSourceOperation): OperationBasic {
  const { name, method, path, description } = operation
  return { name, method, path, description: description || '' }
}

/** 比较请求方式和路径，确认配置草稿与实际调用属于同一个请求地址。 */
export function sameOperationAddress(left: OperationAddress, right: OperationAddress): boolean {
  return left.method === right.method && left.path.trim() === right.path.trim()
}

/** 校验基本信息草稿，并提供将被移除的路径占位符供保存前确认。 */
export function prepareOperationBasic(draft: OperationBasic, saved: DataSourceOperation): { basic: OperationBasic; removed: string[] } {
  const basic = { ...draft, name: draft.name.trim(), description: draft.description.trim(), path: draft.path.trim() }
  if (!basic.name) throw new Error('请输入接口名称。')
  if (basic.name.length > 256) throw new Error('接口名称不能超过 256 个字符。')
  if (basic.description.length > 500) throw new Error('接口描述不能超过 500 个字符。')
  if (!basic.path || basic.path.length > 2048 || basic.path.startsWith('//') || /[?#\\]/.test(basic.path)) {
    throw new Error('请输入不含查询串的相对接口路径，例如 /posts/{id}。')
  }
  const nextNames = new Set(operationPathParameterNames(basic.path))
  return { basic, removed: saved.pathParameters.map(({ name }) => name).filter((name) => !nextNames.has(name)) }
}

/** 基本信息只更新自己的字段；路径变动时同步调整正式 Path 参数。 */
export function applyOperationBasic(latest: DataSourceOperation, basic: OperationBasic): DataSourceOperation {
  const previous = latest.pathParameters.map((parameter) => ({ ...parameter, rowId: parameter.name }))
  const pathParameters = synchronizePathParameters(basic.path, previous).map(({ rowId: _rowId, ...parameter }) => parameter)
  validateOperationParameters(basic.path, pathParameters, latest.queryParameters)
  return { ...latest, ...basic, pathParameters }
}

/** 将正式接口转换为草稿，行标识与字段说明只用于编辑。 */
export function operationDraftFromSource(editing?: DataSourceOperation): OperationDraft {
  const operation: DataSourceOperation = editing ?? {
    id: 'operation-' + crypto.randomUUID(), name: '', description: '', method: 'GET', path: '/',
    pathParameters: [], queryParameters: [], headers: [], requestStructure: null, responseStructure: null
  }
  const request = readJsonSchemaMetadata(operation.requestStructure)
  const response = readJsonSchemaMetadata(operation.responseStructure)
  return {
    ...operation, description: operation.description || '',
    pathParameters: operation.pathParameters.map((parameter) => ({ ...parameter, rowId: crypto.randomUUID() })),
    queryParameters: operation.queryParameters.map((parameter) => ({ ...parameter, rowId: crypto.randomUUID() })),
    headers: operation.headers.map((header) => ({ ...header, rowId: crypto.randomUUID() })),
    requestSampleText: operation.requestSample === undefined ? '' : JSON.stringify(operation.requestSample, null, 2),
    responseSampleText: operation.responseSample === undefined ? '' : JSON.stringify(operation.responseSample, null, 2),
    requestFieldDescriptionsDraft: request.descriptions, responseFieldDescriptionsDraft: response.descriptions,
    requestFieldTypesDraft: request.fieldTypes, responseFieldTypesDraft: response.fieldTypes
  }
}

/** 基本信息保存后重建 Path 草稿，保留尚未保存的同名定义与其余配置。 */
export function rebaseOperationDraft(draft: OperationDraft, saved: DataSourceOperation): OperationDraft {
  const previous = new Map(draft.pathParameters.map((parameter) => [parameter.name, parameter]))
  const pathParameters = saved.pathParameters.map((parameter) =>
    previous.get(parameter.name) ?? { ...parameter, rowId: crypto.randomUUID() })
  return { ...draft, ...operationBasic(saved), pathParameters }
}

/** 排除基本信息和前端行标识，仅比较编辑接口页负责的内容。 */
export function operationConfigurationSnapshot(draft: OperationDraft): string {
  /** 提取参数配置，忽略输入行的稳定标识。 */
  const fields = (parameters: ParameterDraft[]) => parameters.map(({ name, type, required, description }) =>
    ({ name, type, required, description }))
  return JSON.stringify([
    fields(draft.pathParameters), fields(draft.queryParameters), fields(draft.headers),
    draft.requestSampleText, draft.requestStructure, draft.requestFieldDescriptionsDraft, draft.requestFieldTypesDraft,
    draft.responseSampleText, draft.responseStructure, draft.responseFieldDescriptionsDraft, draft.responseFieldTypesDraft
  ])
}

/** 解析可选 JSON 样例并返回用户可理解的错误。 */
function parseSample(value: string, label: string): unknown {
  if (!value.trim()) return undefined
  try { return JSON.parse(value) } catch { throw new Error(label + '必须是合法 JSON。') }
}

/** 校验配置并生成保存片段，提交时不携带接口名称、方式、路径和描述。 */
export function buildOperationConfiguration(draft: OperationDraft): OperationConfiguration {
  /** 去掉编辑态标识，提交规范化后的字段定义。 */
  const fields = (parameters: ParameterDraft[]) => parameters.map(({ name, type, required, description }) =>
    ({ name: name.trim(), type, required, description: description.trim() }))
  const pathParameters = fields(draft.pathParameters)
  const queryParameters = fields(draft.queryParameters)
  const headers = fields(draft.headers)
  validateOperationParameters(draft.path, pathParameters, queryParameters)
  if (headers.some((header) => !header.name)) throw new Error('Header 名称不能为空。')
  if (new Set(headers.map((header) => header.name.toLowerCase())).size !== headers.length) throw new Error('Header 名称不能重复。')
  const requestSample = parseSample(draft.requestSampleText, '请求样例')
  const responseSample = parseSample(draft.responseSampleText, '响应样例')
  return {
    pathParameters, queryParameters, headers, requestSample, responseSample,
    requestStructure: requestSample === undefined ? draft.requestStructure
      : buildJsonSchema(requestSample, draft.requestFieldDescriptionsDraft, draft.requestFieldTypesDraft),
    responseStructure: responseSample === undefined ? draft.responseStructure
      : buildJsonSchema(responseSample, draft.responseFieldDescriptionsDraft, draft.responseFieldTypesDraft)
  }
}
