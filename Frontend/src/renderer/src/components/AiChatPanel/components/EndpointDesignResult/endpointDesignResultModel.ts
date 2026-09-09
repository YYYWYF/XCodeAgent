import type { EndpointDesignDetail } from '../../../../typings'

export type EndpointDesignResultRow = {
  key: string
  endpoint: string
  location: string
  locationLabel: string
  locationGroupLabel: string
  type: string
  mapping: string
  dataSourceType: string
  dataSource: string
  mappingField: string
  description: string
}

export type EndpointDesignResultGroup = {
  location: string
  label: string
  rows: EndpointDesignResultRow[]
}

/** 将来源字段转换为用户可读的具体定位标签。 */
export function sourceLabel(value: unknown, design?: Record<string, unknown>): string {
  if (!value || typeof value !== 'object') return ''
  const source = value as Record<string, unknown>
  const snapshots = Array.isArray(design?.sourceSnapshots)
    ? design.sourceSnapshots.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []
  const snapshot = snapshots.find((item) => String(item.sourceId || '') === String(source.sourceId || ''))
  if (source.sourceType === 'database') {
    // 数据库名称优先取确认时保存的业务名称，避免把数据源内部 ID 当作用户可读内容。
    const databaseName = firstHumanLabel(snapshot?.name)
    // Schema、表和列共同确定数据库字段，避免同名表在不同 Schema 下指向不清。
    return [databaseName, source.schema, source.table, source.column].filter(Boolean).join(' · ')
  }
  const details = snapshot?.details && typeof snapshot.details === 'object'
    ? snapshot.details as Record<string, unknown>
    : undefined
  const operation = details?.operation && typeof details.operation === 'object'
    ? details.operation as Record<string, unknown>
    : undefined
  const operationName = firstHumanLabel(
    operation?.name,
    operation?.summary,
    operation?.title,
    snapshot?.name
  )
  return [operationName, source.section, source.path].filter(Boolean).join(' · ')
}

/** 将来源字段投影为设计表中的数据源名称。 */
export function sourceDataSourceLabel(value: unknown, design?: Record<string, unknown>): string {
  if (!value || typeof value !== 'object') return ''
  const source = value as Record<string, unknown>
  const snapshots = Array.isArray(design?.sourceSnapshots)
    ? design.sourceSnapshots.filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    : []
  const snapshot = snapshots.find((item) => String(item.sourceId || '') === String(source.sourceId || ''))
  return firstHumanLabel(snapshot?.name) || String(source.sourceId || '')
}

/** 将来源字段投影为设计表中的映射字段路径。 */
export function sourceMappingFieldLabel(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const source = value as Record<string, unknown>
  if (source.sourceType === 'database') {
    return [source.schema, source.table, source.column].filter(Boolean).join('.')
  }
  return [source.directoryId, source.operationId, source.section, source.path].filter(Boolean).join(' / ')
}

/** 将来源字段类型转换为独立的数据源类型标签。 */
export function sourceTypeLabel(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const sourceType = String((value as Record<string, unknown>).sourceType || '')
  return ({
    database: '数据库',
    external_api: '外部 API'
  } as Record<string, string>)[sourceType] || sourceType
}

/** 将 Endpoint 字段位置转换成展示标签。 */
export function endpointLabel(value: unknown): string {
  if (!value || typeof value !== 'object') return ''
  const field = value as Record<string, unknown>
  return String(field.path || '')
}

/** 将请求/返回字段位置转换为用户可读的分组名称。 */
export function endpointLocationLabel(value: unknown): string {
  return ({
    path: 'Path 参数',
    query: 'Query 参数',
    header: 'Header 参数',
    request_body: '请求体',
    response_body: '返回体'
  } as Record<string, string>)[String(value)] || String(value || '其他字段')
}

/** 将 Endpoint 字段位置转换为设计表使用的短标签。 */
export function endpointLocationDisplayLabel(value: unknown): string {
  return ({
    path: 'Path',
    query: 'Query',
    header: 'Header',
    request_body: 'Request Body',
    response_body: 'Response Body'
  } as Record<string, string>)[String(value)] || String(value || '其他')
}

/** 从多个候选值中选择一个不含内部标识的业务名称。 */
function firstHumanLabel(...values: unknown[]): string {
  return values
    .map((value) => String(value || '').trim())
    .find((value) => value && !isInternalIdentifier(value)) || ''
}

/** 判断名称是否明显是数据源或 Operation 的内部标识。 */
function isInternalIdentifier(value: string): boolean {
  return /^(ds-|source-|operation-|directory-|entity-|scene:)|^[0-9a-f]{16,}$/i.test(value)
}

/** 把一份正式设计投影为请求或返回侧只读表格行。 */
export function projectEndpointDesignRows(
  detail: EndpointDesignDetail | undefined,
  side: 'request' | 'response'
): EndpointDesignResultRow[] {
  const mappings = detail?.design?.fieldMappings
  if (!Array.isArray(mappings)) return []
  return mappings
    .filter((item): item is Record<string, unknown> => Boolean(item && typeof item === 'object'))
    .filter((mapping) => {
      const endpoint = mapping.endpointField
      return Boolean(endpoint && typeof endpoint === 'object' && (endpoint as Record<string, unknown>).side === side)
    })
    .map((mapping, index) => {
      const endpoint = mapping.endpointField as Record<string, unknown>
      const mappingType = String(mapping.mappingType || 'unconfigured')
      const sources = Array.isArray(mapping.sourceFields) ? mapping.sourceFields : []
      const dataSource = sources.map((item) => sourceDataSourceLabel(item, detail?.design)).filter(Boolean).join('\n')
      const mappingField = sources.map(sourceMappingFieldLabel).filter(Boolean).join('\n')
      const sourceType = [...new Set(sources.map(sourceTypeLabel))].join(' / ')
      const description = String(mapping.businessDescription || '')
      return {
        key: `${side}-${index}-${endpointLabel(endpoint)}`,
        endpoint: endpointLabel(endpoint),
        location: String(endpoint.location || ''),
        locationLabel: endpointLocationDisplayLabel(endpoint.location),
        locationGroupLabel: endpointLocationLabel(endpoint.location),
        type: String(endpoint.type || 'unknown'),
        mapping: mappingType === 'business_description'
          ? '业务说明'
          : mappingType === 'source_mapping'
            ? ({ direct: '直接映射', single_field_description: '单字段业务处理', multi_field_description: '多字段业务处理' } as Record<string, string>)[String(mapping.processingType)]
              : '未配置',
        dataSourceType: sourceType,
        dataSource,
        mappingField,
        description
      }
    })
}

/** 按 Path、Query、请求体等位置分组，供结果面板分段展示。 */
export function groupEndpointDesignRows(rows: EndpointDesignResultRow[]): EndpointDesignResultGroup[] {
  const groups = new Map<string, EndpointDesignResultGroup>()
  for (const row of rows) {
    const current = groups.get(row.location)
    if (current) current.rows.push(row)
    else groups.set(row.location, { location: row.location, label: row.locationGroupLabel, rows: [row] })
  }
  return Array.from(groups.values())
}

/** 计算当前设计的请求与返回映射数量。 */
export function endpointDesignSummary(detail: EndpointDesignDetail | undefined): {
  requestCount: number
  responseCount: number
  totalCount: number
} {
  const requestCount = projectEndpointDesignRows(detail, 'request').length
  const responseCount = projectEndpointDesignRows(detail, 'response').length
  return { requestCount, responseCount, totalCount: requestCount + responseCount }
}
