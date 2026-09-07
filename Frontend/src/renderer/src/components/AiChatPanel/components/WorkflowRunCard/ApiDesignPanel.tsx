import { Alert, Button, Input, Spin, Tabs, Tag, Typography } from 'antd'
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
import { cx } from '../../../../utils'
import { createApiDesignAction } from './apiDesignSerialization'
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
  onAction: (action: WorkflowApiDesignAction) => void
}

/** 渲染 Endpoint API 设计容器，并把字段绑定交互交给表格工作台。 */
export default function ApiDesignPanel({ disabled, payload, workspaceRoot, onAction }: ApiDesignPanelProps): ReactElement {
  const { draft, errors, setDraft } = useApiDesignDraft(payload)
  const [activeSide, setActiveSide] = useState<'request' | 'response'>('request')
  const [sources, setSources] = useState<NonNullable<WorkflowApiDesignPayload['sources']>>([])
  const [databaseMetadata, setDatabaseMetadata] = useState<WorkflowApiDesignPayload['databaseMetadata']>()
  const [externalOperation, setExternalOperation] = useState<WorkflowApiDesignPayload['externalOperation']>()
  const [sourceLoading, setSourceLoading] = useState(false)
  const [sourceError, setSourceError] = useState('')
  const [metadataRequest, setMetadataRequest] = useState<ApiSourceMetadataRequestState>({ status: 'idle' })
  const metadataRequestSequence = useRef(0)

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

  return <div className={cx('api-design-panel')}>
    <div className={cx('api-design-panel-header')}>
      <div>
        <Title level={5}>Endpoint 字段映射</Title>
        <Text code>{String(payload.endpoint?.method || 'API')} {String(payload.endpoint?.path || draft.endpointId)}</Text>
      </div>
      <Tag>{payload.existingStatus?.status === 'stale' ? '需重新设计' : 'Endpoint 独立设计'}</Tag>
    </div>
    <Alert
      message="请在表格中为请求体或返回体字段选择直接映射、场景实体映射或一句话业务说明。分页、排序、控制参数等不对应实体或数据源字段的字段，也可用业务说明描述其用途。场景实体只能来自 TechnicalPlan 只读模板。"
      showIcon
      type="info"
    />
    <section className="api-design-implementation-description" aria-label="API 实现描述">
      <div className="api-design-implementation-description-heading">
        <div>
          <Text strong>API 实现描述</Text>
          <Text type="secondary">可选</Text>
        </div>
        <Text type="secondary">描述该接口整体如何实现，例如查询步骤、事务、缓存、异常处理或外部 API 编排。</Text>
      </div>
      <Input.TextArea
        autoSize={{ minRows: 3, maxRows: 8 }}
        className="api-design-implementation-description-input"
        disabled={disabled}
        maxLength={4000}
        onChange={(event) => setDraft({ ...draft, implementationDescription: event.target.value })}
        placeholder="请输入该接口的实现思路（可选），例如：先校验查询条件，再执行分页查询并统一处理空结果。"
        showCount
        value={draft.implementationDescription || ''}
      />
    </section>
    {sourceLoading ? <div className="api-design-metadata-loading"><Spin size="small" /> 正在读取数据源目录…</div> : null}
    {sourceError ? <Alert message={sourceError} showIcon type="error" /> : null}
    <Tabs
      activeKey={activeSide}
      onChange={(key) => setActiveSide(key as 'request' | 'response')}
      items={[{ key: 'request', label: '请求映射' }, { key: 'response', label: '返回映射' }]}
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
    {Object.keys(errors).length ? <Alert message="请修正映射校验错误" description={Object.values(errors).join('；')} showIcon type="error" /> : null}
    <div className={cx('api-design-actions')}>
      <Text type="secondary">确认后生成当前 Endpoint 的字段映射 JSON/Markdown，并自动进入 API 就绪检查和完整开发流程；TechnicalPlan 契约不会被修改。</Text>
      <Button disabled={disabled || Object.keys(errors).length > 0} onClick={() => onAction(createApiDesignAction(draft, 'confirm'))} type="primary">确认映射设计</Button>
    </div>
  </div>
}
