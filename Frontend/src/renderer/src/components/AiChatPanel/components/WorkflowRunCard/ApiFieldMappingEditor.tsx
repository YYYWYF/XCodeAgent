import { Alert, Button, Form, Input, Select, Space, Typography } from 'antd'
import { useState } from 'react'
import type { ComponentProps, ReactElement } from 'react'
import type {
  WorkflowApiDesignDraft,
  WorkflowApiDesignPayload,
  WorkflowApiField,
  WorkflowApiSourceField,
  WorkflowApiFieldMapping
} from '../../../../typings'
import type { ApiFieldMappingMode } from './apiDesignTableModel'
import {
  apiDesignFieldKey, endpointFieldSnapshot, validateApiDesignDraft,
  createBusinessDescriptionMapping, findFieldMapping, replaceFieldMapping,
  sourceFieldSnapshot
} from './apiDesignSerialization'
import ApiSourceFieldSelector from './ApiSourceFieldSelector'
import ApiMappingSourceList from './ApiMappingSourceList'
import { sourceSnapshotToNode } from './apiDesignTableModel'
import type {
  ApiSourceMetadataAction,
  ApiSourceMetadataContext,
  ApiSourceMetadataRequestState
} from './apiSourceSelectorModel'

const { Text } = Typography
type SavedMode = Exclude<ApiFieldMappingMode, 'unconfigured'>
type EditorMode = 'direct' | 'single_field_description' | 'multi_field_description' | 'business_description'

type Props = {
  endpoint: WorkflowApiField
  initialMode: SavedMode
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

/** 在抽屉内编辑一条直属来源或业务说明映射，保存前不修改外层草稿。 */
export default function ApiFieldMappingEditor({
  endpoint,
  initialMode,
  payload,
  draft,
  disabled,
  onCancel,
  onClear,
  onSave,
  onLoadSource,
  metadataRequest
}: Props): ReactElement {
  const initialMapping = findFieldMapping(draft, endpoint)
  const [mode, setMode] = useState<EditorMode>(initialMapping?.mappingType === 'source_mapping'
    ? initialMapping.processingType : initialMode === 'business_description' ? 'business_description' : 'direct')
  const [sources, setSources] = useState<WorkflowApiSourceField[]>(initialMapping?.mappingType === 'source_mapping' ? initialMapping.sourceFields : [])
  const [error, setError] = useState('')
  const [description, setDescription] = useState(initialMapping && 'businessDescription' in initialMapping ? initialMapping.businessDescription || '' : '')

  /** 切换只改变编辑模式，保留用户数据；数量约束由保存时校验。 */
  const handleModeChange = (nextMode: EditorMode): void => {
    setMode(nextMode)
    setError('')
  }

  /** 单字段模式直接编辑唯一来源，选择新字段时原子替换旧来源。 */
  const handleSingleSourceSelect = (node: Parameters<NonNullable<ComponentProps<typeof ApiSourceFieldSelector>['onSelect']>>[0]): void => {
    setSources([sourceFieldSnapshot(node, endpoint)])
    setError('')
  }

  /** 单字段模式清空当前来源，等待用户重新选择真实字段。 */
  const handleSingleSourceClear = (): void => {
    setSources([])
    setError('')
  }

  /** 构造当前模式的干净记录，验证后一次性提交外部草稿。 */
  const save = (): void => {
    const endpointSnapshot = endpointFieldSnapshot(endpoint)
    const mapping: WorkflowApiFieldMapping = mode === 'business_description'
      ? createBusinessDescriptionMapping(endpoint, description)
      : mode === 'direct'
        ? { endpointField: endpointSnapshot, mappingType: 'source_mapping' as const, processingType: mode, sourceFields: sources }
        : { endpointField: endpointSnapshot, mappingType: 'source_mapping' as const, processingType: mode, sourceFields: sources, businessDescription: description.trim() }
    const next = replaceFieldMapping(draft, mapping)
    const issue = validateApiDesignDraft(next)[apiDesignFieldKey(endpoint)]
    if (issue) { setError(issue); return }
    onSave(next)
  }

  return <div className="api-design-field-mapping-editor">
    <Form layout="vertical">
      <section className="api-design-editor-summary" aria-label="Endpoint 字段信息">
        <Text className="api-design-editor-kicker" type="secondary">Endpoint 字段</Text>
        <div className="api-design-editor-summary-title">
          <Text strong>{endpoint.path}</Text>
        </div>
        <Text type="secondary">{endpoint.location} · {endpoint.type}</Text>
        {endpoint.description ? <Text className="api-design-editor-summary-description" type="secondary">{endpoint.description}</Text> : null}
      </section>

      <section className="api-design-editor-section api-design-mode-section">
        <div className="api-design-section-heading">
          <Text strong>映射模式</Text>
          <Text type="secondary">选择来源数量及处理方式</Text>
        </div>
        <Form.Item className="api-design-editor-form-item">
          <Select
            className="api-design-editor-select"
            disabled={disabled}
            onChange={handleModeChange}
            options={[
              { value: 'direct', label: '直接映射' },
              { value: 'single_field_description', label: '单字段业务处理' },
              { value: 'multi_field_description', label: '多字段业务处理' },
              { value: 'business_description', label: '纯业务说明' }
            ]}
            value={mode}
          />
        </Form.Item>
      </section>

      {mode !== 'business_description' ? <section className="api-design-editor-section api-design-source-card">
        <div className="api-design-section-heading">
          <Text strong>数据源字段</Text>
          <Text type="secondary">{mode === 'multi_field_description' ? '选择至少两个不同真实字段' : '只能选择一个真实字段'}</Text>
        </div>
        <Form.Item className="api-design-editor-form-item">
          {mode === 'multi_field_description'
            ? <ApiMappingSourceList
              endpoint={endpoint} payload={payload} draft={draft} disabled={disabled}
              sources={sources} onChange={(next) => { setSources(next); setError('') }}
              metadataRequest={metadataRequest}
              onLoad={(action, context) => onLoadSource(draft, action, context)}
            />
            : <ApiSourceFieldSelector
              endpoint={endpoint}
              payload={payload}
              draft={draft}
              disabled={disabled}
              metadataRequest={metadataRequest}
              selectedSourceNode={sources.length === 1 ? sourceSnapshotToNode(sources[0]) : undefined}
              onLoad={(action, context) => onLoadSource(draft, action, context)}
              onSelect={handleSingleSourceSelect}
              onClear={handleSingleSourceClear}
            />}
          <Text type="secondary">{endpoint.side === 'request' ? '请求字段 → 处理说明 → 关联字段' : '来源字段 → 处理说明 → 响应字段'}</Text>
        </Form.Item>
      </section> : null}

      {mode !== 'direct' ? <section className="api-design-editor-section">
        <div className="api-design-section-heading">
          <Text strong>{mode === 'business_description' ? '业务说明' : '业务处理'}</Text>
          <Text type="secondary">描述字段的处理方式，由代码生成消费</Text>
        </div>
        <Form.Item className="api-design-editor-form-item" help="填写完整业务处理方式；不要遗漏影响结果的条件。">
          <Input.TextArea
            autoSize={{ minRows: 4, maxRows: 8 }}
            className="api-design-editor-textarea"
            disabled={disabled}
            maxLength={2000}
            onChange={(event) => { setDescription(event.target.value); setError('') }}
            placeholder={mode === 'business_description' ? '请输入该字段的业务说明。' : '请输入该字段的业务处理内容。'}
            showCount
            value={description}
          />
        </Form.Item>
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
