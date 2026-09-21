import { ApiOutlined, DeleteOutlined, FileTextOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { Button, Input, Modal, Select, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { DataSourceHeader, DataSourceOperation, DataSourceFieldType, DataSourceOperationSection } from '../../typings'
import { confirmWorkspaceAction } from '../workspaceDialogs'
import { cx } from '../../utils'
import { JsonSampleTabs } from './JsonStructureViewer'
import DataSourceParameterEditor, { type ParameterDraft } from './DataSourceParameterEditor'
import { convertJsonFieldType } from './jsonFieldTypes'
import { parseJsonSampleText } from './jsonStructure'
import { readJsonSchemaMetadata } from './jsonSchema'
import DataSourceEditorSection from './DataSourceEditorSection'
import { clearOperationSection, sectionMeta } from './dataSourceOperationConfig'
import OperationConfigurationBar, { OperationContentPicker } from './DataSourceOperationConfiguration'

const { Text } = Typography

type JsonBodyTab = 'structure' | 'sample'

/** 渲染请求体或响应体标题栏中的结构/样例切换入口。 */
function JsonBodyTabSwitcher({ activeKey, onChange }: { activeKey: JsonBodyTab; onChange: (key: JsonBodyTab) => void }): ReactElement {
  return <div aria-label="JSON 内容视图" className={cx('data-source-json-header-tabs')} role="tablist"><button aria-selected={activeKey === 'structure'} className={cx(activeKey === 'structure' && 'is-active')} onClick={() => onChange('structure')} role="tab" type="button">结构</button><button aria-selected={activeKey === 'sample'} className={cx(activeKey === 'sample' && 'is-active')} onClick={() => onChange('sample')} role="tab" type="button">样例</button></div>
}

/** 描述可编辑的接口草稿，额外保留文本形式的 JSON 样例。 */
export type OperationDraft = Omit<DataSourceOperation, 'pathParameters' | 'queryParameters'> & {
  pathParameters: ParameterDraft[]
  queryParameters: ParameterDraft[]
  requestSampleText: string
  responseSampleText: string
  requestFieldDescriptionsDraft: Record<string, string>
  responseFieldDescriptionsDraft: Record<string, string>
  requestFieldTypesDraft: Record<string, DataSourceFieldType>
  responseFieldTypesDraft: Record<string, DataSourceFieldType>
}

/** 创建一个新的接口草稿。 */
export function emptyOperation(): OperationDraft {
  return {
    id: `operation-${Date.now()}-${Math.random().toString(16).slice(2, 6)}`,
    name: '',
    description: '',
    method: 'GET',
    path: '/',
    pathParameters: [],
    queryParameters: [],
    headers: [],
    requestSample: undefined,
    responseSample: undefined,
    requestStructure: null,
    responseStructure: null,
    requestSampleText: '',
    responseSampleText: '',
    requestFieldDescriptionsDraft: {},
    responseFieldDescriptionsDraft: {},
    requestFieldTypesDraft: {},
    responseFieldTypesDraft: {}
  }
}

/** 将已保存接口转换为可编辑草稿。 */
export function operationDraftFromSource(operation?: DataSourceOperation): OperationDraft {
  if (!operation) return emptyOperation()
  const request = readJsonSchemaMetadata(operation.requestStructure)
  const response = readJsonSchemaMetadata(operation.responseStructure)
  return {
    ...operation,
    description: operation.description || '',
    pathParameters: operation.pathParameters.map((parameter) => ({ ...parameter, rowId: crypto.randomUUID() })),
    queryParameters: operation.queryParameters.map((parameter) => ({ ...parameter, rowId: crypto.randomUUID() })),
    headers: operation.headers.map((header) => ({ ...header })),
    requestSampleText: operation.requestSample === undefined ? '' : JSON.stringify(operation.requestSample, null, 2),
    responseSampleText: operation.responseSample === undefined ? '' : JSON.stringify(operation.responseSample, null, 2),
    requestFieldDescriptionsDraft: request.descriptions,
    responseFieldDescriptionsDraft: response.descriptions,
    requestFieldTypesDraft: request.fieldTypes,
    responseFieldTypesDraft: response.fieldTypes
  }
}

/** 渲染外部 API 的普通 Header 编辑器，可作为请求参数子卡片复用。 */
export function HeaderEditor({
  defaultExpanded = true,
  headers,
  nested = false,
  onChange,
  onRemove,
  title = '接口 Header'
}: {
  defaultExpanded?: boolean
  headers: DataSourceHeader[]
  nested?: boolean
  onChange: (headers: DataSourceHeader[]) => void
  onRemove?: () => void
  title?: string
}): ReactElement {
  /** 在 Header 分组末尾追加一行可编辑请求头。 */
  const addHeader = (): void => onChange([...headers, { name: '', value: '' }])
  const actions = <>
    <Button className={cx('data-source-editor-add-button', 'data-source-editor-parameter-add-button')} icon={<PlusOutlined />} onClick={addHeader} size="small">添加参数</Button>
    {onRemove ? <Button aria-label={`删除${title}`} className={cx('data-source-editor-remove-button')} icon={<DeleteOutlined />} onClick={onRemove} size="small" type="text" /> : null}
  </>
  return (
    <DataSourceEditorSection actions={actions} className={nested ? 'data-source-editor-section-nested' : undefined} defaultExpanded={defaultExpanded} icon={nested ? undefined : <ApiOutlined />} title={title} tone="header">
      <div className={cx('data-source-editor-section-content', 'data-source-header-editor')}>
        {headers.length ? <>
          <div className={cx('data-source-operation-table')}>
            <div aria-hidden="true" className={cx('data-source-header-row-labels')}><span>Header 名称</span><span>Header 值</span><span>操作</span></div>
            <div className={cx('data-source-operation-rows')}>
              {headers.map((header, index) => (
                <div className={cx('data-source-header-row')} key={index}>
                  <Input
                    aria-label={`Header ${index + 1} 名称`}
                    className={cx('data-source-header-name')}
                    onChange={(event) => {
                      const next = [...headers]
                      next[index] = { ...header, name: event.target.value }
                      onChange(next)
                    }}
                    placeholder="例如：X-Client-Id"
                    value={header.name}
                  />
                  <Input
                    aria-label={`Header ${index + 1} 值`}
                    className={cx('data-source-header-value')}
                    onChange={(event) => {
                      const next = [...headers]
                      next[index] = { ...header, value: event.target.value }
                      onChange(next)
                    }}
                    placeholder="Header 值"
                    value={header.value}
                  />
                  <Button
                    aria-label={`删除 Header ${index + 1}`}
                    className={cx('data-source-operation-row-delete')}
                    icon={<DeleteOutlined />}
                    onClick={() => onChange(headers.filter((_item, itemIndex) => itemIndex !== index))}
                    type="text"
                  />
                </div>
              ))}
            </div>
          </div>
        </> : <div className={cx('data-source-operation-section-empty')}><FileTextOutlined /><span>暂无{title}，点击右上角添加</span></div>}
        <Text className={cx('data-source-operation-section-tip')} type="secondary">不支持 Authorization、Cookie、API Key 等敏感 Header。</Text>
      </div>
    </DataSourceEditorSection>
  )
}

/** 渲染单个接口的基本信息和按需配置区域。 */
export function OperationFields({
  initiallyCollapsed = false,
  operation,
  onChange,
  onSectionsChange,
  phase = 'edit',
  sections,
  theme
}: {
  initiallyCollapsed?: boolean
  operation: OperationDraft
  onChange: (operation: OperationDraft) => void
  onSectionsChange: (sections: Set<DataSourceOperationSection>) => void
  phase?: 'selection' | 'edit'
  sections: ReadonlySet<DataSourceOperationSection>
  theme: 'light' | 'dark'
}): ReactElement {
  /** 修改当前请求或响应字段说明草稿，不因为样例暂时无效而清空。 */
  const updateFieldDescription = (key: 'requestFieldDescriptionsDraft' | 'responseFieldDescriptionsDraft', path: string, description: string): void => {
    const descriptions = { ...operation[key] }
    if (description.trim()) descriptions[path] = description
    else delete descriptions[path]
    onChange({ ...operation, [key]: descriptions })
  }

  /** 预计算类型转换，确认可能丢弃的容器内容后同步更新样例与类型草稿。 */
  const updateFieldType = (side: 'request' | 'response', path: string, type?: DataSourceFieldType): void => {
    const typesKey = side === 'request' ? 'requestFieldTypesDraft' : 'responseFieldTypesDraft'
    const sampleKey = side === 'request' ? 'requestSampleText' : 'responseSampleText'
    const types = { ...operation[typesKey] }
    if (!type) {
      delete types[path]
      onChange({ ...operation, [typesKey]: types })
      return
    }
    const parsed = parseJsonSampleText(operation[sampleKey])
    if (parsed.error || parsed.value === undefined) {
      message.error(parsed.error || '请先填写 JSON 样例。')
      return
    }
    const conversion = convertJsonFieldType(parsed.value, path, type)
    if (!conversion.matchedCount) return
    /** 一次更新样例和声明，取消确认时不修改编辑草稿。 */
    const applyConversion = (): void => {
      onChange({ ...operation, [sampleKey]: JSON.stringify(conversion.value, null, 2), [typesKey]: { ...types, [path]: type } })
    }
    if (conversion.destructiveCount) {
      Modal.confirm({
        centered: true, title: '转换字段类型？',
        wrapClassName: cx('data-source-editor-modal-wrap', `theme-${theme}`),
        content: `转换为 ${type} 将移除 ${conversion.destructiveCount} 处对象或数组的子内容。`,
        cancelText: '取消', okText: '确认转换', onOk: applyConversion
      })
    } else applyConversion()
  }

  const [requestTab, setRequestTab] = useState<JsonBodyTab>('structure')
  const [responseTab, setResponseTab] = useState<JsonBodyTab>('structure')

  /** 关闭一个已选模块，并清理其对应草稿。 */
  const removeSection = (section: DataSourceOperationSection): void => {
    const meta = sectionMeta(section)
    const nextOperation = clearOperationSection(operation, section)
    const hasContent = section === 'path' ? operation.pathParameters.length > 0
      : section === 'query' ? operation.queryParameters.length > 0
        : section === 'header' ? operation.headers.length > 0
          : section === 'requestBody' ? Boolean(operation.requestSampleText.trim() || operation.requestStructure)
            : Boolean(operation.responseSampleText.trim() || operation.responseStructure)
    const apply = (): void => {
      onChange(nextOperation)
      onSectionsChange(new Set([...sections].filter((item) => item !== section)))
    }
    if (!hasContent) { apply(); return }
    confirmWorkspaceAction({ title: `删除${meta.title}？`, content: '删除后，本次未保存的配置内容也会被清空。', okText: '删除配置', cancelText: '取消', okButtonProps: { danger: true }, onOk: apply })
  }

  /** 更新模块选择并为新添加模块保留当前编辑草稿。 */
  const updateSections = (next: Set<DataSourceOperationSection>): void => onSectionsChange(next)

  return (
    <div className={cx('data-source-operation-fields')}>
      <DataSourceEditorSection icon={<FileTextOutlined />} title="基本信息" tone="basic">
        <div className={cx('data-source-form-grid', 'data-source-operation-basic')}>
          <label>
            <span><em className={cx('data-source-required')}>*</em>接口名称</span>
            <Input onChange={(event) => onChange({ ...operation, name: event.target.value })} placeholder="例如：查询商品" value={operation.name} />
          </label>
          <label>
            <span><em className={cx('data-source-required')}>*</em>请求方式</span>
            <Select
              onChange={(method) => onChange({ ...operation, method })}
              options={['GET', 'POST', 'PUT', 'DELETE'].map((method) => ({ label: method, value: method }))}
              value={operation.method}
            />
          </label>
          <label className={cx('data-source-form-grid-wide')}>
            <span><em className={cx('data-source-required')}>*</em>路径</span>
            <Input onChange={(event) => onChange({ ...operation, path: event.target.value })} placeholder="/products/{productId}" value={operation.path} />
          </label>
          <label className={cx('data-source-form-grid-wide')}>
            <span>接口描述（选填）</span>
            <Input.TextArea autoSize={{ minRows: 2, maxRows: 4 }} maxLength={500} onChange={(event) => onChange({ ...operation, description: event.target.value })} placeholder="描述接口用途、返回内容或使用场景" showCount value={operation.description} />
          </label>
        </div>
      </DataSourceEditorSection>
      {phase === 'selection' ? <OperationContentPicker selected={sections} onChange={updateSections} /> : <>
        <OperationConfigurationBar selected={sections} onChange={updateSections} />
        {sections.has('path') || sections.has('query') || sections.has('header') ? <DataSourceEditorSection actions={<span className={cx('data-source-editor-section-count')}>已配置 {[sections.has('path'), sections.has('query'), sections.has('header')].filter(Boolean).length} 项</span>} defaultExpanded={!initiallyCollapsed} icon={<SendOutlined />} title="请求参数" tone="request">
          <div className={cx('data-source-operation-parameter-stack')}>
            {sections.has('path') ? <DataSourceParameterEditor defaultExpanded={!initiallyCollapsed} location="path" nested onChange={(pathParameters) => onChange({ ...operation, pathParameters })} onRemove={() => removeSection('path')} parameters={operation.pathParameters} /> : null}
            {sections.has('query') ? <DataSourceParameterEditor defaultExpanded={!initiallyCollapsed} location="query" nested onChange={(queryParameters) => onChange({ ...operation, queryParameters })} onRemove={() => removeSection('query')} parameters={operation.queryParameters} /> : null}
            {sections.has('header') ? <HeaderEditor defaultExpanded={!initiallyCollapsed} headers={operation.headers} nested onChange={(headers) => onChange({ ...operation, headers })} onRemove={() => removeSection('header')} title="Header 参数" /> : null}
          </div>
        </DataSourceEditorSection> : null}
        {sections.has('requestBody') ? <DataSourceEditorSection actions={<Button aria-label="删除请求体配置" className={cx('data-source-editor-remove-button')} icon={<DeleteOutlined />} onClick={() => removeSection('requestBody')} size="small" type="text" />} defaultExpanded={!initiallyCollapsed} headerContent={<JsonBodyTabSwitcher activeKey={requestTab} onChange={setRequestTab} />} icon={<ApiOutlined />} title="请求体" tone="request">
          <JsonSampleTabs activeKey={requestTab} editable descriptions={operation.requestFieldDescriptionsDraft} fieldTypes={operation.requestFieldTypesDraft} label="请求体" onActiveKeyChange={(key) => setRequestTab(key === 'sample' ? 'sample' : 'structure')} onChange={(text) => onChange({ ...operation, requestSampleText: text })} onDescriptionChange={(path, description) => updateFieldDescription('requestFieldDescriptionsDraft', path, description)} onTypeChange={(path, type) => updateFieldType('request', path, type)} showTabs={false} showTitle={false} text={operation.requestSampleText} />
        </DataSourceEditorSection> : null}
        {sections.has('responseBody') ? <DataSourceEditorSection actions={<Button aria-label="删除响应体配置" className={cx('data-source-editor-remove-button')} icon={<DeleteOutlined />} onClick={() => removeSection('responseBody')} size="small" type="text" />} defaultExpanded={!initiallyCollapsed} headerContent={<JsonBodyTabSwitcher activeKey={responseTab} onChange={setResponseTab} />} icon={<FileTextOutlined />} title="响应体" tone="response">
          <JsonSampleTabs activeKey={responseTab} editable descriptions={operation.responseFieldDescriptionsDraft} fieldTypes={operation.responseFieldTypesDraft} label="响应体" onActiveKeyChange={(key) => setResponseTab(key === 'sample' ? 'sample' : 'structure')} onChange={(text) => onChange({ ...operation, responseSampleText: text })} onDescriptionChange={(path, description) => updateFieldDescription('responseFieldDescriptionsDraft', path, description)} onTypeChange={(path, type) => updateFieldType('response', path, type)} showTabs={false} showTitle={false} text={operation.responseSampleText} />
        </DataSourceEditorSection> : null}
      </>}
    </div>
  )
}
