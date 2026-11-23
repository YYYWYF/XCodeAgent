import type { DevelopmentArtifacts, DevelopmentPlanningApiContract } from '../../../../typings'
import type { PreviewRuntimePayload } from '../../../../service/previewRuntime'
import type { DebugMethod, InternalDebugDefinition, InternalDebugTarget } from '../../../../service/internalApiDebug'
import type { Draft, Row } from '../../../DataSourcesPage/debugDraft'
import { JSON_FIELD_TYPES, PATH_FIELD_TYPES } from '../../../DataSourcesPage/jsonFieldTypes'

/** 用完整契约身份关联开发状态，保留当前契约下已完成的接口。 */
export function completedInternalEndpoints(
  contracts: DevelopmentPlanningApiContract[],
  artifacts: DevelopmentArtifacts | undefined
) {
  return contracts.flatMap((contract) => contract.endpoints.flatMap((endpoint) => {
    const apiContractId = endpoint.apiContractId || contract.id
    const progress = artifacts?.endpoints[apiContractId]?.[endpoint.id]
    if (progress?.initialDevelopmentStatus !== 'completed' || (progress.changeStatus ?? 'unchanged') !== 'unchanged') return []
    return [{
      key: JSON.stringify([apiContractId, endpoint.id]),
      apiContractId,
      endpointId: endpoint.id,
      name: endpoint.name || endpoint.summary || endpoint.path,
      method: endpoint.method,
      path: endpoint.path
    }]
  }))
}

/** 从正式参数定义创建本轮默认请求，固定 Path 名称并启用必填字段。 */
export function internalDebugDraft(definition: InternalDebugDefinition): Draft<DebugMethod> {
  /** 通用调试表格使用同一套 JSON 字段类型。 */
  const typeFor = (type: string | undefined, allowed = JSON_FIELD_TYPES): Row['type'] =>
    allowed.find((item) => item === type) ?? 'string'
  const path = definition.pathNames.map((name) => {
    const field = definition.parameters.find((item) => item.location === 'path' && item.name === name)
    return { name, type: typeFor(field?.type, PATH_FIELD_TYPES), value: '', enabled: true }
  })
  /** 可选 Query 和 Header 默认关闭，避免空值影响接口实测。 */
  const rows = (location: 'query' | 'header'): Row[] => definition.parameters
    .filter((parameter) => parameter.location === location)
    .map(({ name, type, required }) => ({ name, type: typeFor(type), value: '', enabled: required }))
  return {
    method: definition.method, requestPath: definition.path,
    path, query: rows('query'), headers: rows('header'), body: definition.body, timeout: 10_000
  }
}

/** 草稿跟随当前迭代和接口定义，服务重启或换端口时继续可用。 */
export function internalDebugStorageKey(target: InternalDebugTarget, definition: InternalDebugDefinition): string {
  return 'internal-api-debug.v1:' + JSON.stringify([
    target.workspaceRoot, target.iterationId, target.apiContractId, target.endpointId, definition.method, definition.path
  ])
}

/** 从同一预览快照读取实际 Java 服务地址。 */
export function internalDebugService(runtime?: PreviewRuntimePayload['runtime']) {
  const port = runtime?.backend.port
  if (!runtime?.attemptId || runtime.backend.status !== 'running' || !port || !Number.isInteger(port) || port > 65535 || port < 1) return undefined
  return { base: `http://127.0.0.1:${port}`, attemptId: runtime.attemptId }
}
