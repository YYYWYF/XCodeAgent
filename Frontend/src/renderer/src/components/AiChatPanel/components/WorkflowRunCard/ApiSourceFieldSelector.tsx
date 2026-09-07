import { Alert, Button, Select, Tag, Tooltip, Typography } from 'antd'
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiExternalFieldNode,
  WorkflowApiField
} from '../../../../typings'
import {
  allowedDatabaseUsages,
  databaseSourceFieldId,
  resolveDatabaseUsage
} from './apiDesignSerialization'
import {
  API_SOURCE_METADATA_ACTIONS,
  createApiSourceSelectorState,
  filterExternalFields,
  hasMatchingDatabaseMetadata,
  hasMatchingExternalOperation,
  matchesApiSourceMetadataRequest,
  resetApiSourceForDirectory,
  resetApiSourceForOperation,
  resetApiSourceForSource,
  resetApiSourceForTable,
  groupExternalFields
} from './apiSourceSelectorModel'
import type {
  ApiExternalFieldDirection,
  ApiSourceMetadataAction,
  ApiSourceMetadataContext,
  ApiSourceMetadataRequestState
} from './apiSourceSelectorModel'

type SourceNode = WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode

const { Text } = Typography

type Props = {
  endpoint: WorkflowApiField
  mode: 'direct_source' | 'through_entity'
  businessRole?: 'input' | 'output'
  payload: WorkflowApiDesignPayload
  draft: WorkflowApiDesignDraft
  disabled?: boolean
  selectedSourceNode?: SourceNode
  metadataRequest?: ApiSourceMetadataRequestState
  onLoad: (action: ApiSourceMetadataAction, context: ApiSourceMetadataContext) => Promise<void>
  onSelect: (source: SourceNode) => void
  onClear: () => void
}

/** 在字段表格中提供直属 MySQL 与外部 Operation 的级联来源选择。 */
export default function ApiSourceFieldSelector({
  endpoint, mode, businessRole, payload, disabled, selectedSourceNode, metadataRequest, onLoad, onSelect, onClear
}: Props): ReactElement {
  const resolvedSelectedSourceNode = selectedSourceNode
  const [state, setState] = useState(() => createApiSourceSelectorState(endpoint, resolvedSelectedSourceNode))
  const initialMetadataLoadRequested = useRef(false)
  const { sourceId, table, column, directoryId, operationId, externalFieldKey, usage } = state

  const sources = Array.isArray(payload.sources) ? payload.sources : []
  const source = sources.find((item) => String(item.id || '') === sourceId)
  const database = payload.databaseMetadata
  const tables = database?.sourceId === sourceId ? (database.tables || []) : []
  const columns = database?.sourceId === sourceId && database.table === table ? (database.columns || []) : []
  const directories = Array.isArray(source?.directories) ? source.directories : []
  const directory = directories.find((item) => String(item.id || '') === directoryId)
  const operations = Array.isArray(directory?.operations) ? directory.operations : []
  const external = payload.externalOperation
  const externalFields = external?.sourceId === sourceId && external.directoryId === directoryId
    && String(external.operation?.id || '') === operationId
    ? filterExternalFields(external.fields || [], businessRole ? `business_${businessRole}` : endpoint.side as ApiExternalFieldDirection)
    : []

  const direction: ApiExternalFieldDirection = businessRole ? `business_${businessRole}` : endpoint.side
  const externalFieldGroups = external?.sourceId === sourceId && external.directoryId === directoryId
    && String(external.operation?.id || '') === operationId
    ? groupExternalFields(external.fields || [], direction)
    : []

  const tablesLoaded = hasMatchingDatabaseMetadata(payload, sourceId)
    && Array.isArray(database?.tables)
  const columnsLoaded = Boolean(table)
    && hasMatchingDatabaseMetadata(payload, sourceId, table)
    && Array.isArray(database?.columns)
  const operationLoaded = hasMatchingExternalOperation(payload, sourceId, directoryId, operationId)
  const tablesLoading = matchesApiSourceMetadataRequest(metadataRequest, API_SOURCE_METADATA_ACTIONS.databaseTables, { sourceId })
    && metadataRequest?.status === 'loading'
  const columnsLoading = matchesApiSourceMetadataRequest(metadataRequest, API_SOURCE_METADATA_ACTIONS.databaseColumns, { sourceId, table })
    && metadataRequest?.status === 'loading'
  const operationLoading = matchesApiSourceMetadataRequest(metadataRequest, API_SOURCE_METADATA_ACTIONS.externalOperation, { sourceId, directoryId, operationId })
    && metadataRequest?.status === 'loading'
  const tablesError = metadataRequest?.status === 'error'
    && matchesApiSourceMetadataRequest(metadataRequest, API_SOURCE_METADATA_ACTIONS.databaseTables, { sourceId })
  const columnsError = metadataRequest?.status === 'error'
    && matchesApiSourceMetadataRequest(metadataRequest, API_SOURCE_METADATA_ACTIONS.databaseColumns, { sourceId, table })
  const operationError = metadataRequest?.status === 'error'
    && matchesApiSourceMetadataRequest(metadataRequest, API_SOURCE_METADATA_ACTIONS.externalOperation, { sourceId, directoryId, operationId })
  const metadataError = (tablesError || columnsError || operationError) ? metadataRequest?.message || '读取数据源元数据失败。' : ''

  useEffect(() => {
    // 打开已有数据库映射时，选择器已经恢复了 source/table；此时主动补发元数据动作，
    // 否则表格只会显示已保存的来源摘要而不会查询可选表和列。
    if (initialMetadataLoadRequested.current || !resolvedSelectedSourceNode || !sourceId) return
    const selectedSource = sources.find((item) => String(item.id || '') === sourceId)
    if (selectedSource?.type !== 'database' || selectedSource.metadataSupported === false) return
    initialMetadataLoadRequested.current = true
    if (payload.databaseMetadata?.sourceId !== sourceId || !Array.isArray(payload.databaseMetadata?.tables)) {
      void onLoad(API_SOURCE_METADATA_ACTIONS.databaseTables, { sourceId })
      return
    }
    if (table && (payload.databaseMetadata.table !== table || !payload.databaseMetadata.columns?.length)) {
      void onLoad(API_SOURCE_METADATA_ACTIONS.databaseColumns, { sourceId, table })
    }
  }, [onLoad, payload.databaseMetadata, resolvedSelectedSourceNode, sourceId, sources, table])

  /** 根据当前规则角色恢复数据库字段用途，避免业务规则输入写入数据库。 */
  const resolveSelectorUsage = (requested?: WorkflowApiDatabaseFieldNode['usage']): WorkflowApiDatabaseFieldNode['usage'] => {
    if (businessRole === 'input') return 'read'
    if (businessRole === 'output') return requested === 'write' ? 'write' : 'filter'
    return resolveDatabaseUsage(endpoint, requested)
  }

  /** 返回当前选择器允许的数据库用途。 */
  const selectorUsages = businessRole === 'input'
    ? ['read' as const]
    : businessRole === 'output'
      ? ['filter' as const, 'write' as const]
      : allowedDatabaseUsages(endpoint)

  /** 选择来源后重置旧级联状态，并按来源类型自动加载第一层元数据。 */
  const handleSourceChange = (nextSourceId: string | undefined): void => {
    const next = nextSourceId || ''
    setState((current) => resetApiSourceForSource(current, next, endpoint))
    if (!next) {
      onClear()
      return
    }
    const nextSource = sources.find((item) => String(item.id || '') === next)
    if (nextSource?.type === 'database' && nextSource.metadataSupported !== false) {
      void onLoad(API_SOURCE_METADATA_ACTIONS.databaseTables, { sourceId: next })
    }
  }

  /** 选择数据库表后自动加载列元数据。 */
  const handleTableChange = (nextTable: string): void => {
    setState((current) => resetApiSourceForTable(current, nextTable))
    if (sourceId && nextTable) void onLoad(API_SOURCE_METADATA_ACTIONS.databaseColumns, { sourceId, table: nextTable })
  }

  /** 将选中的数据库列转换成带用途的 Source Field 节点。 */
  const selectDatabaseColumn = (nextColumn: string): void => {
    setState((current) => ({ ...current, column: nextColumn }))
    const metadata = columns.find((item) => item.name === nextColumn)
    if (!metadata || !sourceId || !table) return
    const nextUsage = resolveSelectorUsage(usage)
    onSelect({
      nodeType: 'source_field', sourceType: 'database',
      id: databaseSourceFieldId({
        nodeType: 'source_field', sourceType: 'database', sourceId,
        schema: String(database?.schema || ''), table, column: nextColumn,
        type: metadata.type, usage: nextUsage
      }),
      sourceId, schema: String(database?.schema || ''), table, column: nextColumn,
      type: metadata.type, usage: nextUsage, description: metadata.description
    })
  }

  /** 修改数据库用途后重新提交同一列，保证用途属于节点身份。 */
  const handleUsageChange = (nextUsage: WorkflowApiDatabaseFieldNode['usage']): void => {
    setState((current) => ({ ...current, usage: nextUsage }))
    if (!column) return
    const metadata = columns.find((item) => item.name === column)
    if (!metadata || !sourceId || !table) return
    onSelect({
      nodeType: 'source_field', sourceType: 'database',
      id: databaseSourceFieldId({
        nodeType: 'source_field', sourceType: 'database', sourceId,
        schema: String(database?.schema || ''), table, column,
        type: metadata.type, usage: nextUsage
      }),
      sourceId, schema: String(database?.schema || ''), table, column,
      type: metadata.type, usage: nextUsage, description: metadata.description
    })
  }

  /** 选择外部数据源目录并重置 Operation 与字段。 */
  const handleDirectoryChange = (nextDirectoryId: string): void => {
    setState((current) => resetApiSourceForDirectory(current, nextDirectoryId))
  }

  /** 选择外部 Operation 后自动加载最新 Operation Schema。 */
  const handleOperationChange = (nextOperationId: string): void => {
    setState((current) => resetApiSourceForOperation(current, nextOperationId))
    if (sourceId && directoryId && nextOperationId) void onLoad(API_SOURCE_METADATA_ACTIONS.externalOperation, { sourceId, directoryId, operationId: nextOperationId })
  }

  /** 将外部 Operation 叶子字段转换成 Source Field 节点。 */
  const selectExternalField = (key: string): void => {
    setState((current) => ({ ...current, externalFieldKey: key }))
    const metadata = externalFields.find((item) => `${item.section}:${item.path}` === key)
    if (!metadata || !sourceId || !directoryId || !operationId) return
    onSelect({
      nodeType: 'source_field', sourceType: 'external_api',
      id: `source:external:${sourceId}:${directoryId}:${operationId}:${metadata.section}:${metadata.path}`,
      sourceId, directoryId, operationId,
      section: metadata.section as WorkflowApiExternalFieldNode['section'],
      path: metadata.path, type: metadata.type, description: metadata.description
    })
  }

  const sourceOptions = useMemo(() => sources.map((item) => {
    const label = `${String(item.name || item.id || '')}${item.type === 'database' ? ` · ${item.mode || 'database'}` : ' · external API'}`
    return { value: String(item.id || ''), label, title: label }
  }), [sources])

  const sourceSelectProps = {
    allowClear: true,
    className: 'api-design-source-select',
    disabled,
    onChange: handleSourceChange,
    options: sourceOptions,
    optionFilterProp: 'label' as const,
    showSearch: true,
    value: sourceId || undefined
  }

  /** 渲染数据源级联中的统一字段标题，确保不同来源模式保持一致层级。 */
  const renderSourceFieldLabel = (label: string, required = false): ReactElement => (
    <Tooltip title={label}>
      <Text className="api-design-source-field-label" strong={required} title={label}>
        {required ? <span className="api-design-required-mark">*</span> : null}{label}
      </Text>
    </Tooltip>
  )

  if (!source) {
    return <div className="api-design-source-selector">
      <div className="api-design-source-grid">
        <div className="api-design-source-field api-design-source-field-full">
          {renderSourceFieldLabel('数据源', mode === 'direct_source')}
          <Select {...sourceSelectProps} placeholder={mode !== 'direct_source' ? '可选：选择数据源' : '选择数据源'} />
        </div>
      </div>
      <Typography.Text type="secondary">请选择数据源。</Typography.Text>
    </div>
  }
  if (source.type === 'database' && source.metadataSupported === false) {
    return <div className="api-design-source-selector">
      <div className="api-design-source-grid">
        <div className="api-design-source-field api-design-source-field-full">
          {renderSourceFieldLabel('数据源', mode === 'direct_source')}
          <Select {...sourceSelectProps} placeholder={mode !== 'direct_source' ? '可选：选择数据源' : '选择数据源'} />
        </div>
      </div>
      <div className="api-design-source-status">
        <Alert banner message={String(source.metadataMessage || '当前数据库模式不支持实时读取元数据。')} type="warning" />
      </div>
    </div>
  }
  if (source.type === 'database') {
    return <div className="api-design-source-selector">
      <div className="api-design-source-grid api-design-source-grid-database">
        <div className="api-design-source-field api-design-source-field-full">
          {renderSourceFieldLabel('数据源', mode === 'direct_source')}
          <Select {...sourceSelectProps} placeholder={mode !== 'direct_source' ? '可选：选择数据源' : '选择数据源'} />
        </div>
        <div className="api-design-source-field">
          {renderSourceFieldLabel('数据表', mode === 'direct_source')}
          <Select
            className="api-design-source-select"
            disabled={disabled || tablesLoading || !tables.length}
            loading={tablesLoading}
            onChange={handleTableChange}
            options={tables.map((item) => ({
              value: item.name,
              label: item.description ? `${item.name} · ${item.description}` : item.name,
              title: item.description ? `${item.name} · ${item.description}` : item.name
            }))}
            optionFilterProp="label"
            placeholder="选择数据表"
            showSearch
            value={table || undefined}
          />
        </div>
        <div className="api-design-source-field">
          {renderSourceFieldLabel('数据字段', mode === 'direct_source')}
          <Select
            allowClear
            className="api-design-source-select"
            disabled={disabled || columnsLoading || !columns.length}
            loading={columnsLoading}
            onChange={selectDatabaseColumn}
            options={columns.map((item) => ({ value: item.name, label: `${item.name} · ${item.type}`, title: `${item.name} · ${item.type}` }))}
            optionFilterProp="label"
            placeholder="选择数据字段"
            showSearch
            value={column || undefined}
          />
        </div>
      </div>
      {column ? <div className="api-design-source-field api-design-source-usage">
        {renderSourceFieldLabel('数据库用途')}
        <Select
          className="api-design-source-select api-design-source-usage-select"
          disabled={disabled}
          onChange={handleUsageChange}
          options={selectorUsages.map((item) => ({ value: item, label: `用途：${item}`, title: `用途：${item}` }))}
          optionFilterProp="label"
          showSearch
          value={usage}
        />
      </div> : null}
      <div className="api-design-source-status">
        {metadataError ? <Alert banner message={metadataError} type="error" /> : null}
        {tablesLoading ? <Tag>正在加载数据表</Tag> : null}
        {!tablesLoading && tablesLoaded && !tables.length && !metadataError ? <Typography.Text type="secondary">当前数据库未查询到可用数据表。</Typography.Text> : null}
        {table && columnsLoading ? <Tag>正在加载字段</Tag> : null}
        {table && !columnsLoading && columnsLoaded && !columns.length && !metadataError ? <Typography.Text type="secondary">当前数据表未查询到字段。</Typography.Text> : null}
        {!tablesLoading && (tablesError || (tablesLoaded && !tables.length)) ? <Button className="api-design-source-reload" disabled={disabled} onClick={() => void onLoad(API_SOURCE_METADATA_ACTIONS.databaseTables, { sourceId })} size="small" type="link">重新加载数据表</Button> : null}
      </div>
    </div>
  }
  return <div className="api-design-source-selector">
    <div className="api-design-source-grid api-design-source-grid-external">
      <div className="api-design-source-field api-design-source-field-full">
        {renderSourceFieldLabel('数据源', mode === 'direct_source')}
        <Select {...sourceSelectProps} placeholder={mode !== 'direct_source' ? '可选：选择数据源' : '选择数据源'} />
      </div>
      <div className="api-design-source-field">
        {renderSourceFieldLabel('数据目录', mode === 'direct_source')}
        <Select
          className="api-design-source-select"
          disabled={disabled || !directories.length}
          onChange={handleDirectoryChange}
          options={directories.map((item) => {
            const label = String(item.name || item.id || '')
            return { value: String(item.id || ''), label, title: label }
          })}
          optionFilterProp="label"
          placeholder="选择数据目录"
          showSearch
          value={directoryId || undefined}
        />
      </div>
      <div className="api-design-source-field">
        {renderSourceFieldLabel('Operation', mode === 'direct_source')}
        <Select
          className="api-design-source-select"
          disabled={disabled || !operations.length}
          onChange={handleOperationChange}
          options={operations.map((item) => {
            const label = String(item.name || item.id || '')
            return { value: String(item.id || ''), label, title: label }
          })}
          optionFilterProp="label"
          placeholder="选择 Operation"
          showSearch
          value={operationId || undefined}
        />
      </div>
      <div className="api-design-source-field api-design-source-field-full">
        {renderSourceFieldLabel('Operation 字段', mode === 'direct_source')}
        <Select
          allowClear
          className="api-design-source-select"
          disabled={disabled || !externalFields.length}
          loading={operationLoading}
          onChange={selectExternalField}
          options={externalFieldGroups}
          optionFilterProp="label"
          placeholder="选择 Operation 字段"
          showSearch
          value={externalFieldKey || undefined}
        />
      </div>
    </div>
    <div className="api-design-source-status">
      {metadataError ? <Alert banner message={metadataError} type="error" /> : null}
      {operationLoading ? <Tag>正在加载 Operation 字段</Tag> : null}
      {sourceId && operationId && operationLoaded && !externalFields.length && !operationLoading && !metadataError ? <Typography.Text type="secondary">当前 Operation 未查询到可用字段。</Typography.Text> : null}
    </div>
  </div>
}
