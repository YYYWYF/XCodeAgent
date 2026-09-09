import { Alert, Button, Input, message, Popover, Spin, Tabs, Tag, Typography } from 'antd'
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
import { useEffect, useMemo, useRef, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDesignAction,
  WorkflowApiDesignPayload
} from '../../../../typings'
import {
  requestApiDesignDatabaseColumns,
  requestApiDesignDatabaseTables,
  requestApiDesignExternalOperation,
  requestDataSources
} from '../../../../service/dataSources'
import { apiDesignFieldKey, createApiDesignAction } from './apiDesignSerialization'
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
  const { draft, errors, setDraft } = useApiDesignDraft(payload)
  const [activeSide, setActiveSide] = useState<'request' | 'response'>('request')
  const [sources, setSources] = useState<NonNullable<WorkflowApiDesignPayload['sources']>>([])
  const [databaseMetadata, setDatabaseMetadata] = useState<WorkflowApiDesignPayload['databaseMetadata']>()
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
  const endpointMethod = String(payload.endpoint?.method || 'API').toUpperCase()
  const endpointPath = String(payload.endpoint?.path || draft.endpointId)

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

  return <div className="api-design-panel" ref={panelRef}>
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
    </div>

    <Alert
      className="api-design-instruction"
      icon={<InfoCircleFilled />}
      message="配置说明"
      description="请在表格中为请求或返回字段选择数据源字段映射或填写业务处理内容。每个 Endpoint 字段都要配置来源或非空业务处理内容。"
      showIcon
      type="info"
    />

    <section className="api-design-implementation-description" aria-label="API 实现描述">
      <div className="api-design-description-icon" aria-hidden="true">
        <FileTextOutlined />
      </div>
      <div className="api-design-description-content">
        <div className="api-design-implementation-description-heading">
          <div>
            <Text strong>API 实现描述</Text>
          </div>
          <Text type="secondary">描述该接口整体如何实现，例如查询步骤、事务、缓存、异常处理或外部 API 编排。</Text>
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
      payload={viewPayload}
    />

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
        disabled={disabled || Object.keys(errors).length > 0}
        onClick={() => onAction(createApiDesignAction(draft, 'confirm'))}
        type="primary"
      >
        <span>{submitLabel}</span><ArrowRightOutlined />
      </Button>
    </footer>
  </div>
}
