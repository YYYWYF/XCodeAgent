import { Alert, Button, Form, Input, Select, Space, Tag, Typography } from 'antd'
import { useMemo, useState } from 'react'
import type { ReactElement } from 'react'
import type {
  WorkflowApiDatabaseFieldNode,
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiExternalFieldNode,
  WorkflowApiField
} from '../../../../typings'
import {
  applyDirectSourceMapping,
  applyEntityMapping,
  ensureTemplateEntityField,
  findSelectedSourceNode,
  pruneUnusedSceneEntities,
  removeSourceFromEntityMapping,
  type ApiFieldMappingMode
} from './apiDesignTableModel'
import {
  createBusinessDescriptionMapping,
  createUnconfiguredFieldMapping,
  findFieldMapping,
  replaceFieldMapping
} from './apiDesignSerialization'
import ApiSourceFieldSelector from './ApiSourceFieldSelector'
import type { ApiSourceMetadataAction, ApiSourceMetadataContext, ApiSourceMetadataRequestState } from './apiSourceSelectorModel'

const { Text } = Typography
type SourceNode = WorkflowApiDatabaseFieldNode | WorkflowApiExternalFieldNode

type Props = {
  endpoint: WorkflowApiField
  initialMode: Exclude<ApiFieldMappingMode, 'unconfigured'>
  payload: WorkflowApiDesignPayload
  draft: WorkflowApiDesignDraft
  disabled?: boolean
  onCancel: () => void
  onClear: () => void
  onSave: (draft: WorkflowApiDesignDraft) => void
  metadataRequest?: ApiSourceMetadataRequestState
  onLoadSource: (
    draft: WorkflowApiDesignDraft,
    action: ApiSourceMetadataAction,
    context: ApiSourceMetadataContext
  ) => Promise<void>
}

/** 在抽屉内编辑一条自包含字段映射，未点击保存前不修改外层正式草稿。 */
export default function ApiFieldMappingEditor({ endpoint, initialMode, payload, draft, disabled, onCancel, onClear, onSave, onLoadSource, metadataRequest }: Props): ReactElement {
  const initialMapping = findFieldMapping(draft, endpoint)
  const initialEntity = initialMapping?.mappingType === 'through_entity'
    ? draft.sceneEntities.find((entity) => entity.id === initialMapping.entityField.entityId)
    : undefined
  const [workingDraft, setWorkingDraft] = useState(draft)
  const [mode, setMode] = useState<Exclude<ApiFieldMappingMode, 'unconfigured'>>(initialMode)
  const [entityRef, setEntityRef] = useState(
    initialEntity && initialMapping?.mappingType === 'through_entity'
      ? `${initialEntity.templateEntityId}::${initialMapping.entityField.path}`
      : ''
  )
  const [selectedSourceNode, setSelectedSourceNode] = useState<SourceNode | undefined>(
    () => findSelectedSourceNode(initialMapping)
  )
  const [error, setError] = useState('')
  const [description, setDescription] = useState(
    initialMapping?.mappingType === 'business_description'
      ? initialMapping.businessDescription
      : ''
  )
  const templates = Array.isArray(payload.entityTemplates) ? payload.entityTemplates : []
  const entityOptions = useMemo(() => templates.flatMap((template) => template.fields.map((field) => ({
    value: `${template.id}::${field.name}`,
    label: `${template.name}.${field.name} (${field.type})`
  }))), [templates])

  /** 切换映射模式并把当前字段恢复为未配置记录。 */
  const handleModeChange = (nextMode: Exclude<ApiFieldMappingMode, 'unconfigured'>): void => {
    setMode(nextMode)
    setEntityRef('')
    setSelectedSourceNode(undefined)
    setWorkingDraft((current) =>
      pruneUnusedSceneEntities(replaceFieldMapping(current, createUnconfiguredFieldMapping(endpoint)))
    )
    setError('')
  }

  /** 选择只读 TechnicalPlan 模板字段并直接写入当前字段记录。 */
  const handleEntityChange = (value?: string): void => {
    setEntityRef(value || '')
    setSelectedSourceNode(undefined)
    if (!value) {
      setWorkingDraft((current) =>
        pruneUnusedSceneEntities(replaceFieldMapping(current, createUnconfiguredFieldMapping(endpoint)))
      )
      return
    }
    const separator = value.indexOf('::')
    const template = templates.find((item) => item.id === value.slice(0, separator))
    const fieldName = value.slice(separator + 2)
    if (!template || !template.fields.some((item) => item.name === fieldName)) return
    const ensured = ensureTemplateEntityField(workingDraft, template, fieldName)
    setWorkingDraft(applyEntityMapping(ensured.draft, endpoint, ensured.field))
    setError('')
  }

  /** 选择数据源字段并内嵌到当前字段映射。 */
  const handleSourceSelect = (source: SourceNode): void => {
    setSelectedSourceNode(source)
    if (mode === 'direct_source') {
      setWorkingDraft(applyDirectSourceMapping(workingDraft, endpoint, source))
      return
    }
    const mapping = findFieldMapping(workingDraft, endpoint)
    if (mapping?.mappingType === 'through_entity') {
      setWorkingDraft(applyEntityMapping(workingDraft, endpoint, mapping.entityField, source))
    }
  }

  /** 取消当前来源选择；经实体模式保留实体字段引用。 */
  const handleSourceClear = (): void => {
    setSelectedSourceNode(undefined)
    if (mode === 'direct_source') {
      setWorkingDraft((current) =>
        pruneUnusedSceneEntities(replaceFieldMapping(current, createUnconfiguredFieldMapping(endpoint)))
      )
      return
    }
    setWorkingDraft((current) => removeSourceFromEntityMapping(current, endpoint))
  }

  /** 校验抽屉内最小映射条件后一次性提交正式草稿。 */
  const save = (): void => {
    if (mode === 'business_description') {
      const value = description.trim()
      if (!value) {
        setError('请输入一句业务说明。')
        return
      }
      onSave(pruneUnusedSceneEntities(
        replaceFieldMapping(workingDraft, createBusinessDescriptionMapping(endpoint, value))
      ))
      return
    }
    const mapping = findFieldMapping(workingDraft, endpoint)
    if (!mapping || mapping.mappingType !== mode) {
      setError(mode === 'direct_source' ? '请选择一个数据源字段。' : '请选择一个场景实体字段。')
      return
    }
    onSave(pruneUnusedSceneEntities(workingDraft))
  }

  const entityFlowLabel = endpoint.side === 'request'
    ? '数据流：Endpoint → 场景实体 → 数据源'
    : '数据流：数据源 → 场景实体 → Endpoint'

  return <div className="api-design-field-mapping-editor">
    <Form layout="vertical">
      <section className="api-design-editor-summary" aria-label="Endpoint 字段信息">
        <Text className="api-design-editor-kicker" type="secondary">Endpoint 字段</Text>
        <div className="api-design-editor-summary-title">
          <Text strong>{endpoint.path}</Text>
          <Tag>{endpoint.required ? '必填' : '可选'}</Tag>
        </div>
        <Text type="secondary">{endpoint.location} · {endpoint.type}</Text>
        {endpoint.description ? <Text className="api-design-editor-summary-description" type="secondary">{endpoint.description}</Text> : null}
      </section>

      <section className="api-design-editor-section api-design-mode-section">
        <div className="api-design-section-heading">
          <Text strong>映射模式</Text>
          <Text type="secondary">选择该字段的数据来源或业务含义</Text>
        </div>
        <Form.Item className="api-design-editor-form-item" required>
          <Select className="api-design-editor-select" disabled={disabled} onChange={handleModeChange} options={[
            { value: 'direct_source', label: '直接映射数据源' },
            { value: 'through_entity', label: '经场景实体映射' },
            { value: 'business_description', label: '一句话业务说明' }
          ]} value={mode} />
        </Form.Item>
      </section>

      {mode === 'business_description' ? <section className="api-design-editor-section">
        <div className="api-design-section-heading">
          <Text strong>业务说明</Text>
          <Text type="secondary">仅提供给代码生成，不执行表达式</Text>
        </div>
        <Form.Item className="api-design-editor-form-item" required help="用一句话说明该字段的用途，例如分页、排序或控制参数。">
          <Input.TextArea autoSize={{ minRows: 4, maxRows: 8 }} className="api-design-editor-textarea" disabled={disabled} maxLength={2000} onChange={(event) => { setDescription(event.target.value); setError('') }} placeholder="请输入该字段的业务用途说明。" showCount value={description} />
        </Form.Item>
      </section> : null}

      {mode === 'through_entity' ? <section className="api-design-editor-section">
        <div className="api-design-section-heading">
          <Text strong>场景实体字段</Text>
          <Text type="secondary">只能选择 TechnicalPlan 只读模板字段</Text>
        </div>
        <Form.Item className="api-design-editor-form-item" required>
          <Select allowClear className="api-design-editor-select" disabled={disabled} onChange={handleEntityChange} options={entityOptions} placeholder="选择 TechnicalPlan 只读模板字段" value={entityRef || undefined} />
          {!entityOptions.length ? <Alert className="api-design-editor-help" message="当前 Endpoint 没有关联的 TechnicalPlan 实体模板，可改用直接映射或业务说明。" type="info" /> : null}
        </Form.Item>
        <div className="api-design-flow-hint">{entityFlowLabel}</div>
      </section> : null}

      {mode !== 'business_description' ? <section className="api-design-editor-section api-design-source-card">
        <div className="api-design-section-heading">
          <Text strong>数据源字段</Text>
          <Text type="secondary">{mode === 'direct_source' ? '必须选择一个字段' : '可选，选择后补充实体与数据源之间的关系'}</Text>
        </div>
        <Form.Item className="api-design-editor-form-item" required={mode === 'direct_source'}>
          <ApiSourceFieldSelector
            endpoint={endpoint}
            mode={mode}
            payload={payload}
            draft={workingDraft}
            disabled={disabled || (mode === 'through_entity' && !entityRef)}
            selectedSourceNode={selectedSourceNode}
            metadataRequest={metadataRequest}
            onLoad={(action, context) => onLoadSource(workingDraft, action, context)}
            onSelect={handleSourceSelect}
            onClear={handleSourceClear}
          />
        </Form.Item>
        {mode === 'through_entity' ? <Text className="api-design-source-note" type="secondary">不选择数据源时，仅保存 Endpoint 与场景实体之间的映射。</Text> : null}
      </section> : null}
      {error ? <Alert message={error} type="error" /> : null}
      <div className="api-design-editor-actions">
        <Button danger disabled={disabled} onClick={onClear} type="text">清除映射</Button>
        <Space>
          <Button disabled={disabled} onClick={onCancel}>取消</Button>
          <Button disabled={disabled} onClick={save} type="primary">保存映射</Button>
        </Space>
      </div>
    </Form>
  </div>
}


