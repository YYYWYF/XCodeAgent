import type { DataSourceOperationSection, ExternalApiDataSource } from '../../typings'
import { sameOperationAddress, type OperationAddress, type OperationDraft } from './operationEditorDraft'
import { initialDraft, type DebugResult, type Draft } from './debugDraft'
import { isSensitiveDebugField, publicDraft } from './debugPrivacy'
import { operationPathParameterNames, synchronizeOperationPath, validateOperationParameters } from './dataSourceOperations'

export type DebugImport = { draft: Draft; requestSample?: string; responseSample?: string }

/** 将当前编辑草稿投影成可发送配置，只校验请求相关内容。 */
export function setupDebug(source: ExternalApiDataSource, savedAddress: OperationAddress, edit: OperationDraft): Draft {
  const { operation } = synchronizeOperationPath({ ...edit, method: savedAddress.method, path: savedAddress.path }, new Set<DataSourceOperationSection>())
  validateOperationParameters(operation.path, operation.pathParameters, operation.queryParameters)
  const request = initialDraft(source, { ...operation, requestSample: undefined })
  const text = operation.requestSampleText
  if (text.trim()) {
    try { JSON.parse(text) } catch { throw new Error('请先修正请求体 JSON，再同步到调试页。') }
  }
  const safe = publicDraft({ ...request, body: text })
  return { ...safe, body: jsonSample(safe.body) ?? '' }
}

/** 将已脱敏的 JSON 转成可保存的样例，沿用正式目录的大小限制。 */
function jsonSample(text: string): string | undefined {
  try {
    const parsed: unknown = JSON.parse(text)
    if (parsed === null || new TextEncoder().encode(JSON.stringify(parsed)).byteLength > 256 * 1024) return undefined
    return JSON.stringify(parsed, null, 2)
  } catch { return undefined }
}

/** 使用响应所对应的脱敏请求快照，保证重新打开后仍能同步同一次调用。 */
export function captureDebugImport({ input, response }: DebugResult): DebugImport {
  const responseIsUsable = response.statusCode >= 200 && response.statusCode < 300 && !response.truncated
  return {
    draft: input,
    requestSample: jsonSample(input.body),
    responseSample: responseIsUsable ? jsonSample(response.body) : undefined
  }
}

/** 筛选可写入接口定义的参数名与类型，跳过敏感及域共享 Header。 */
function importableRows(rows: Draft['query'], shared: Set<string>): Draft['query'] {
  return rows.map((row) => ({ ...row, name: row.name.trim() }))
    .filter(({ enabled, name }) => enabled && name && !isSensitiveDebugField(name) && !shared.has(name.toLowerCase()))
}

/** 检查本次调用的名称能否按目录规则导入，避免静默合并两条不同参数。 */
export function validateDebugImport({ draft }: DebugImport, source: ExternalApiDataSource): void {
  operationPathParameterNames(draft.requestPath)
  const shared = new Set(source.headers.map(({ name }) => name.toLowerCase()))
  for (const rows of [importableRows(draft.query, new Set()), importableRows(draft.headers, shared)]) {
    const names = new Set<string>()
    for (const { name } of rows) {
      const key = name.toLowerCase()
      if (names.has(key)) throw new Error(`本次调用的参数「${name}」名称重复，请修正后重新发送再同步。`)
      names.add(key)
    }
  }
}

/** 获取响应对应的请求方式与路径，不能用后来试填的地址替代。 */
export function debugImportAddress({ draft }: DebugImport): OperationAddress {
  return { method: draft.method, path: draft.requestPath.trim() }
}

/** 将实际调用的名称和类型合并进编辑草稿，保留原有说明和未覆盖的参数定义。 */
export function applyDebugImport(
  operation: OperationDraft,
  incoming: DebugImport,
  source: ExternalApiDataSource
): OperationDraft {
  if (!sameOperationAddress(operation, debugImportAddress(incoming))) {
    throw new Error('请先保存本次调用的请求方式和路径。')
  }
  const { draft, requestSample, responseSample } = incoming
  const shared = new Set(source.headers.map(({ name }) => name.toLowerCase()))
  const pathTypes = new Map(draft.path.map(({ name, type }) => [name, type]))
  const pathParameters = operation.pathParameters.map((parameter) =>
    ({ ...parameter, type: pathTypes.get(parameter.name) ?? parameter.type }))
  /** 沿用同名参数的描述及必填配置，只同步名称和字段类型。 */
  const merge = (current: OperationDraft['queryParameters'], rows: Draft['query']): typeof current => {
    const result = [...current]
    for (const { name, type } of rows) {
      const existing = result.findIndex((parameter) => parameter.name.trim().toLowerCase() === name.toLowerCase())
      if (existing < 0) result.push({ name, type, required: false, description: '', rowId: crypto.randomUUID() })
      else result[existing] = { ...result[existing], name, type }
    }
    return result
  }
  const queryParameters = merge(operation.queryParameters, importableRows(draft.query, new Set()))
  const headers = merge(operation.headers, importableRows(draft.headers, shared))
  return {
    ...operation,
    pathParameters,
    queryParameters,
    headers,
    requestSampleText: requestSample ?? operation.requestSampleText,
    responseSampleText: responseSample ?? operation.responseSampleText,
    // 本次响应从实际 JSON 推导类型，避免沿用旧声明覆盖结构视图中的实测类型。
    responseFieldTypesDraft: responseSample === undefined ? operation.responseFieldTypesDraft : {}
  }
}
