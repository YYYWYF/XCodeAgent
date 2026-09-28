import { RuleEditingContext } from '../FieldMapping/RuleEditor'
import { Alert, Button, Input, message, Popover, Select, Spin, Tabs, Tag, Typography } from 'antd'
import {
  AimOutlined,
  ApiOutlined,
  ArrowRightOutlined,
  BookOutlined,
  BulbOutlined,
  CopyOutlined,
  ExclamationCircleOutlined,
  FileTextOutlined,
  InfoCircleFilled
} from '@ant-design/icons'
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDatabaseOperation,
  WorkflowApiDesignAction,
  WorkflowApiDesignPayload
} from '../../../../typings'
import {
  requestApiDesignDatabaseColumns,
  requestApiDesignDatabaseTables,
  requestApiDesignExternalOperation,
  requestDataSources
} from '../../../../service/dataSources'
import { apiDesignFieldKey, createApiDesignAction, defaultDatabaseOperation } from './apiDesignSerialization'
import { confirmWorkspaceAction } from '../../../workspaceDialogs'
import { resetDraftForDatabaseOperation } from '../FieldMapping/model'
import DatabaseQueryEditor from '../FieldMapping/DatabaseQueryEditor'
import DatabaseWriteEditor from '../FieldMapping/DatabaseWriteEditor'
import { useApiDesignDraft } from './useApiDesignDraft'
import ApiFieldMappingTable from './ApiFieldMappingTable'
import { API_SOURCE_METADATA_ACTIONS } from './apiSourceSelectorModel'
import type { ApiSourceMetadataAction, ApiSourceMetadataContext, ApiSourceMetadataRequestState } from './apiSourceSelectorModel'
import './ApiDesignPanel.less'

const { Text, Title } = Typography

type ApiDesignPanelProps = {
  disabled?: boolean
  payload: WorkflowApiDesignPayload
  workspaceRoot?: string
  submitLabel?: string
  submitHint?: string
  onAction: (action: WorkflowApiDesignAction) => void
}

/** 渲染 Endpoint API 设计容器，并把字段绑定交互交给表格工作台。 */
export default function ApiDesignPanel({
  disabled,
  payload,
  workspaceRoot,
  submitLabel = '确认映射设计',
  submitHint = '保存后将生成当前 Endpoint 的字段映射 JSON/Markdown；开发是否继续由原会话门禁确认，TechnicalPlan 契约不会被修改。',
  onAction
}: ApiDesignPanelProps): ReactElement {
  const [editingRules, setEditingRules] = useState(0)
  /** 完整编辑器与简化工作台共用未应用规则的提交保护。 */
  const trackRuleEditing = useCallback((delta: number) => setEditingRules((count) => count + delta), [])
  const { draft, errors, validationErrors, showValidationErrors, setDraft } = useApiDesignDraft(payload)
  const [activeSide, setActiveSide] = useState<'request' | 'response'>('request')
  const [sources, setSources] = useState<NonNullable<WorkflowApiDesignPayload['sources']>>([])
  const [databaseMetadata, setDatabaseMetadata] = useState<WorkflowApiDesignPayload['databaseMetadata']>()
  const [queryMetadata, setQueryMetadata] = useState<WorkflowApiDesignPayload['databaseMetadata']>()
  const [querySourceId, setQuerySourceId] = useState(() => {
    const first = draft.databaseQuery?.items[0]
    return first?.kind === 'condition' ? first.sourceId : first?.items[0]?.sourceId || draft.databaseWrites?.[0]?.sourceId || ''
  })
  const [queryTable, setQueryTable] = useState(() => {
    const first = draft.databaseQuery?.items[0]
    return first?.kind === 'condition' ? first.table : first?.items[0]?.table || draft.databaseWrites?.[0]?.table || ''
  })
  const [externalOperation, setExternalOperation] = useState<WorkflowApiDesignPayload['externalOperation']>()
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const [endpointCopied, setEndpointCopied] = useState(false)
  const [metadataRequest, setMetadataRequest] = useState<ApiSourceMetadataRequestState>({ status: 'idle' })
  const metadataRequestSequence = useRef(0)
  const panelRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    let disposed = false
    if (!workspaceRoot) {
      setSources([])
      setSourceError('当前工作区路径不可用，无法读取数据源目录。')
      return () => { disposed = true }
    }
    setSourceLoading(true)
    setSourceError('')
    requestDataSources(workspaceRoot).then((catalog) => {
      if (disposed) return
      const candidates = catalog.sources.map((source) => ({
        ...source,
        metadataSupported: source.type === 'database' ? source.mode === 'direct' : true,
        metadataMessage: source.type === 'database' && source.mode !== 'direct'
          ? '当前数据库模式不支持实时读取元数据。'
          : undefined
      })) as NonNullable<WorkflowApiDesignPayload['sources']>
      setSources(candidates)
    }).catch((error: unknown) => {
      if (!disposed) setSourceError(error instanceof Error ? error.message : '读取数据源目录失败。')
    }).finally(() => {
      if (!disposed) setSourceLoading(false)
    })
    return () => { disposed = true }
  }, [workspaceRoot])

  useEffect(() => {
    if (!workspaceRoot || !querySourceId) return
    let disposed = false
    const request = queryTable ? requestApiDesignDatabaseColumns(workspaceRoot, querySourceId, queryTable)
      : requestApiDesignDatabaseTables(workspaceRoot, querySourceId)
    request
      .then((value) => { if (!disposed) setQueryMetadata(value) })
      .catch((reason: unknown) => { if (!disposed) setSourceError(reason instanceof Error ? reason.message : '读取查询表字段失败。') })
    return () => { disposed = true }
  }, [workspaceRoot, querySourceId, queryTable])

  const viewPayload = useMemo(() => ({
    ...payload,
    sources,
    databaseMetadata,
    externalOperation
  }), [databaseMetadata, externalOperation, payload, sources])

  const sideCounts = useMemo(() => ({
    request: payload.endpointFields.filter((field) => field.side === 'request').length,
    response: payload.endpointFields.filter((field) => field.side === 'response').length
  }), [payload.endpointFields])
  // 优先汇总当前页签；当前侧通过后，继续明确提示另一侧的错误。
  const invalidFields = payload.endpointFields.filter((field) => Boolean(errors[apiDesignFieldKey(field)]))
  const activeInvalidFields = invalidFields.filter((field) => field.side === activeSide)
  const displayedInvalidFields = activeInvalidFields.length ? activeInvalidFields : invalidFields
  const fieldErrorCount = displayedInvalidFields.length
  const sideHint = !activeInvalidFields.length && fieldErrorCount
    ? `${activeSide === 'request' ? '返回' : '请求'}映射中` : '当前映射中'
  const generalErrors = Object.entries(errors).filter(([key]) => key.startsWith('__')).map(([, value]) => value)
  const mappingErrorDescription = [
    fieldErrorCount ? `${sideHint}存在 ${fieldErrorCount} 个字段未通过校验，请选择数据来源或填写有效业务处理内容。` : '',
    ...new Set(generalErrors)
  ].filter(Boolean).join('；')

  /** 首次确认时显示必填校验，修正完成后再提交正式映射。 */
  const submitDesign = (): void => {
    if (Object.keys(validationErrors).length) {
      showValidationErrors()
      return
    }
    onAction(createApiDesignAction(draft, 'confirm'))
  }

  const endpointMethod = String(payload.endpoint?.method || 'API').toUpperCase()
  const endpointPath = String(payload.endpoint?.path || draft.endpointId)
  const hasMappedDatabase = draft.fieldMappings.some((mapping) => mapping.mappingType === 'source_mapping' && mapping.sourceFields.some((source) => source.sourceType === 'database'))
  const hasDatabaseMapping = hasMappedDatabase || Boolean(draft.databaseWrites?.length) || Boolean(draft.databaseQuery?.items.length) || Boolean(querySourceId)
  const queryOperation = draft.databaseOperation || (querySourceId && defaultDatabaseOperation(endpointMethod) === 'create' ? 'read' : defaultDatabaseOperation(endpointMethod))
  const databaseOperationLabels: Record<WorkflowApiDatabaseOperation, string> = { create: '新增', read: '查询', update: '修改', delete: '删除' }

  /** 更换数据库来源时明确清空原表查询和写入字段并加载新表清单。 */
  const selectQuerySource = (sourceId: string): void => {
    const apply = (): void => {
      setQuerySourceId(sourceId)
      setQueryTable('')
      setQueryMetadata(undefined)
      setDraft({ ...draft, databaseQuery: undefined, databaseWrites: [], databaseOperation: hasDatabaseMapping ? draft.databaseOperation : undefined })
    }
    if (draft.databaseQuery?.items.length || draft.databaseWrites?.length) confirmWorkspaceAction({ title: '更换数据库来源？', content: '现有写入字段和查询条件将清空。', okText: '更换并清空', cancelText: '取消', onOk: apply })
    else apply()
  }

  /** 更换数据库表时明确清空旧表查询和写入字段并读取当前列。 */
  const selectQueryTable = (table: string): void => {
    const apply = (): void => {
      setQueryTable(table)
      setDraft({ ...draft, databaseQuery: undefined, databaseWrites: [], databaseOperation: hasDatabaseMapping ? draft.databaseOperation : undefined })
    }
    if (draft.databaseQuery?.items.length || draft.databaseWrites?.length) confirmWorkspaceAction({ title: '更换数据库表？', content: '现有写入字段和查询条件将清空。', okText: '更换并清空', cancelText: '取消', onOk: apply })
    else apply()
  }

  /** 将当前 Endpoint 路径复制到系统剪贴板，并短暂反馈复制结果。 */
  const handleCopyEndpoint = async (): Promise<void> => {
    try {
      await navigator.clipboard.writeText(endpointPath)
      setEndpointCopied(true)
      window.setTimeout(() => setEndpointCopied(false), 1600)
    } catch {
      message.error('复制 Endpoint 路径失败。')
    }
  }

  /** 将用户定位到第一个字段映射错误，减少长表格中的查找成本。 */
  const handleLocateFirstError = (): void => {
    const firstError = displayedInvalidFields[0]
    if (!firstError) return
    setActiveSide(firstError.side)
    window.setTimeout(() => {
      panelRef.current?.querySelector('.api-design-table-row.is-error')?.scrollIntoView({ behavior: 'smooth', block: 'center' })
    }, 0)
  }

  /** 通过独立数据源 AG-UI 接口读取元数据，不把查询动作送入工作流 run。 */
  const handleLoadSource = (
    _currentDraft: WorkflowApiDesignPayload['draft'],
    action: ApiSourceMetadataAction,
    context: ApiSourceMetadataContext
  ): Promise<void> => {
    const requestId = metadataRequestSequence.current + 1
    metadataRequestSequence.current = requestId
    const requestState: ApiSourceMetadataRequestState = {
      action,
      sourceId: context.sourceId,
      table: context.table,
      directoryId: context.directoryId,
      operationId: context.operationId,
      status: 'loading'
    }
    if (!workspaceRoot) {
      setMetadataRequest({ ...requestState, status: 'error', message: '当前工作区路径不可用，无法读取数据源元数据。' })
      return Promise.resolve()
    }
    setMetadataRequest(requestState)
    const task = action === API_SOURCE_METADATA_ACTIONS.databaseTables
      ? requestApiDesignDatabaseTables(workspaceRoot, String(context.sourceId || ''))
      : action === API_SOURCE_METADATA_ACTIONS.databaseColumns
        ? requestApiDesignDatabaseColumns(workspaceRoot, String(context.sourceId || ''), String(context.table || ''))
        : requestApiDesignExternalOperation(
          workspaceRoot,
          String(context.sourceId || ''),
          String(context.directoryId || ''),
          String(context.operationId || '')
        )
    return task.then((metadata) => {
      if (metadataRequestSequence.current !== requestId) return
      if (action === API_SOURCE_METADATA_ACTIONS.externalOperation) setExternalOperation(metadata)
      else setDatabaseMetadata(metadata)
      setMetadataRequest({ ...requestState, status: 'success' })
    }).catch((error: unknown) => {
      if (metadataRequestSequence.current !== requestId) return
      setMetadataRequest({
        ...requestState,
        status: 'error',
        message: error instanceof Error ? error.message : '读取数据源元数据失败。'
      })
    })
  }

  return <RuleEditingContext.Provider value={trackRuleEditing}><div className="api-design-panel" ref={panelRef}>
    <header className="api-design-hero">
      <div className="api-design-hero-icon" aria-hidden="true">
        <ApiOutlined />
      </div>
      <div className="api-design-hero-copy">
        <Title level={4}>Endpoint 字段映射</Title>
        <Text>为该接口配置请求/返回字段的数据来源与处理逻辑，生成完整的字段映射规则。</Text>
      </div>
      <Popover
        content={(
          <div className="api-design-example-content">
            <Text strong>字段映射示例</Text>
            <code>请求字段：userId → users.id</code>
            <code>返回字段：users.name → name</code>
            <code>分页参数：pageSize ⇒ 业务说明</code>
          </div>
        )}
        title="如何配置映射"
        trigger="click"
      >
        <Button className="api-design-example-button" icon={<BookOutlined />}>查看示例</Button>
      </Popover>
    </header>

    <div className="api-design-endpoint-meta">
      <Tag className={`api-design-method-tag method-${endpointMethod.toLowerCase()}`}>{endpointMethod}</Tag>
      <div className="api-design-endpoint-path">
        <Text code>{endpointPath}</Text>
        <Button
          aria-label="复制 Endpoint 路径"
          className="api-design-copy-button"
          icon={<CopyOutlined />}
          onClick={() => void handleCopyEndpoint()}
          title={endpointCopied ? '已复制' : '复制路径'}
          type="text"
        />
      </div>
      <Tag className="api-design-independent-tag">
        {payload.existingStatus?.status === 'stale' ? '需重新设计' : 'Endpoint 独立设计'}
      </Tag>
      {hasDatabaseMapping ? <Select
        className="api-design-database-operation-select"
        disabled={disabled}
        options={Object.entries(databaseOperationLabels).map(([value, label]) => ({ value, label }))}
        placeholder="选择数据库操作"
        value={queryOperation}
        onChange={(value: WorkflowApiDatabaseOperation) => confirmWorkspaceAction({
          title: '切换数据库操作？',
          content: '数据库操作会更新写入映射；切到新增时清空查询条件。',
          okText: '切换并重排', cancelText: '取消',
          onOk: () => setDraft(resetDraftForDatabaseOperation(draft, value))
        })}
      /> : null}
    </div>

    <Alert
      className="api-design-instruction"
      icon={<InfoCircleFilled />}
      message="配置说明"
      description="查询条件请在下方手动添加；未使用的请求参数可以不映射，返回字段仍需完整配置。"
      showIcon
      type="info"
    />

    <section className="api-design-implementation-description" aria-label="接口映射说明">
      <div className="api-design-description-icon" aria-hidden="true">
        <FileTextOutlined />
      </div>
      <div className="api-design-description-content">
        <div className="api-design-implementation-description-heading">
          <div>
            <Text strong>接口映射说明</Text>
          </div>
          <Text type="secondary">可选，用自然语言描述接口整体业务逻辑，例如参数校验、执行顺序、业务分支和返回结果处理。</Text>
        </div>
        <Input.TextArea
          autoSize={{ minRows: 3, maxRows: 8 }}
          className="api-design-implementation-description-input"
          disabled={disabled}
          maxLength={4000}
          onChange={(event) => setDraft({ ...draft, implementationDescription: event.target.value })}
          placeholder="请输入该接口的实现思路，例如：先校验查询条件，再执行分页查询并统一处理空结果。"
          showCount
          value={draft.implementationDescription || ''}
        />
      </div>
    </section>

    {sourceLoading ? <div className="api-design-metadata-loading"><Spin size="small" /> 正在读取数据源目录…</div> : null}
    {sourceError ? <Alert message={sourceError} showIcon type="error" /> : null}

    <Tabs
      activeKey={activeSide}
      className="api-design-tabs"
      onChange={(key) => setActiveSide(key as 'request' | 'response')}
      items={[
        { key: 'request', label: <span>请求映射（{sideCounts.request}）</span> },
        { key: 'response', label: <span>返回映射（{sideCounts.response}）</span> }
      ]}
    />
    <ApiFieldMappingTable
      activeSide={activeSide}
      disabled={disabled}
      errors={errors}
      draft={draft}
      onDraftChange={setDraft}
      onLoadSource={handleLoadSource}
      metadataRequest={metadataRequest}
      operation={draft.databaseOperation || defaultDatabaseOperation(endpointMethod)}
      payload={viewPayload}
    />

    <section className="api-design-query-section" aria-label="数据库表和查询条件">
      <Typography.Title level={5}>数据库数据表</Typography.Title>
      <Select className="api-design-query-source" placeholder="选择数据库来源" disabled={disabled} value={querySourceId || undefined}
        options={sources.filter((source) => source.type === 'database' && source.metadataSupported !== false).map((source) => ({ value: source.id, label: source.name || source.id }))}
        onChange={selectQuerySource} />
      {querySourceId ? <Select className="api-design-query-table" placeholder="选择数据表" disabled={disabled} value={queryTable || undefined}
        options={queryMetadata?.sourceId === querySourceId ? (queryMetadata.tables || []).map((table) => ({ value: table.name, label: table.name })) : []}
        onChange={selectQueryTable} /> : null}
      {draft.databaseOperation === 'create' ? <Text type="secondary">新增操作不使用查询条件。</Text> : null}
      {draft.databaseOperation !== 'create' && querySourceId && queryTable && queryMetadata?.sourceId === querySourceId && queryMetadata.table === queryTable
        ? <DatabaseQueryEditor selection={{ sourceType: 'database', sourceId: querySourceId, schema: queryMetadata.schema || '', table: queryTable }}
          columns={queryMetadata.columns || []} fields={payload.endpointFields} query={draft.databaseQuery}
          editable={!disabled} readOnly={false} busy={false} error={errors.__databaseQuery}
          onChange={(databaseQuery) => setDraft({ ...draft, databaseQuery, databaseOperation: databaseQuery ? queryOperation || 'read' : hasDatabaseMapping ? draft.databaseOperation : undefined })} />
        : null}
    </section>

    {draft.databaseOperation === 'create' || draft.databaseOperation === 'update' ? <section className="api-design-query-section" aria-label="数据库写入字段">
      <Typography.Title level={5}>写入字段</Typography.Title>
      {querySourceId && queryTable && queryMetadata?.sourceId === querySourceId && queryMetadata.table === queryTable
        ? <DatabaseWriteEditor selection={{ sourceType: 'database', sourceId: querySourceId, schema: queryMetadata.schema || '', table: queryTable }}
          columns={queryMetadata.columns || []} fields={payload.endpointFields} writes={draft.databaseWrites || []}
          editable={!disabled} readOnly={false} busy={false} errors={errors}
          onChange={(databaseWrites) => setDraft({ ...draft, databaseWrites, databaseOperation: databaseWrites.length ? queryOperation || 'create' : draft.databaseOperation })} />
        : <Text type="secondary">请先选择数据库数据表，再配置写入列和值来源。</Text>}
    </section> : null}

    {Object.keys(errors).length ? (
      <Alert
        action={fieldErrorCount > 0 ? <Button icon={<AimOutlined />} onClick={handleLocateFirstError}>一键定位</Button> : undefined}
        className="api-design-validation-alert"
        description={mappingErrorDescription}
        icon={<ExclamationCircleOutlined />}
        message={fieldErrorCount > 0 ? `请修正映射校验错误（${fieldErrorCount}）` : '请修正 API 设计校验错误'}
        showIcon
        type="error"
      />
    ) : null}

    <footer className="api-design-actions">
      <div className="api-design-actions-tip">
        <BulbOutlined />
        <Text type="secondary">{submitHint}</Text>
      </div>
      <Button
        disabled={disabled || editingRules > 0 || Object.keys(errors).length > 0}
        onClick={submitDesign}
        type="primary"
      >
        <span>{submitLabel}</span><ArrowRightOutlined />
      </Button>
    </footer>
  </div></RuleEditingContext.Provider>
}
