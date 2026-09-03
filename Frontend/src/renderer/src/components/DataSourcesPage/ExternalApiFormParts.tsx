import { DeleteOutlined, PlusOutlined } from '@ant-design/icons'
import { Button, Input, Modal, Select, Typography, message } from 'antd'
import type { ReactElement } from 'react'
import type { DataSourceHeader, DataSourceOperation, DataSourceFieldType } from '../../typings'
import { cx } from '../../utils'
import { JsonSampleTabs } from './JsonStructureViewer'
import DataSourceParameterEditor, { type ParameterDraft } from './DataSourceParameterEditor'
import { convertJsonFieldType } from './jsonFieldTypes'
import { parseJsonSampleText } from './jsonStructure'
import { readJsonSchemaMetadata } from './jsonSchema'

const { Text } = Typography

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

/** 渲染外部 API 的普通 Header 编辑器。 */
export function HeaderEditor({
  description = '为当前接口补充普通请求头',
  headers,
  onChange,
  title = '接口 Header'
}: {
  description?: string
  headers: DataSourceHeader[]
  onChange: (headers: DataSourceHeader[]) => void
  title?: string
}): ReactElement {
  return (
    <section className={cx('data-source-operation-section', 'data-source-header-editor')}>
      <header className={cx('data-source-operation-section-header')}>
        <div><strong>{title}</strong><Text type="secondary">{description}</Text></div>
        <Button icon={<PlusOutlined />} onClick={() => onChange([...headers, { name: '', value: '' }])} size="small" type="text">添加 Header</Button>
      </header>
      <div className={cx('data-source-operation-section-body')}>
        {headers.length ? <>
          <div aria-hidden="true" className={cx('data-source-header-row-labels')}><span>Header 名称</span><span>Header 值</span><span /></div>
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
        </> : <div className={cx('data-source-operation-section-empty')}>暂无{title}，点击右上角添加</div>}
        <Text className={cx('data-source-operation-section-tip')} type="secondary">不支持 Authorization、Cookie、API Key 等敏感 Header。</Text>
      </div>
    </section>
  )
}

/** 渲染单个接口的参数、Header 和 JSON 样例编辑区域。 */
export function OperationFields({
  operation,
  onChange,
  theme
}: {
  operation: OperationDraft
  onChange: (operation: OperationDraft) => void
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

  return (
    <div className={cx('data-source-operation-fields')}>
      <div className={cx('data-source-form-grid', 'data-source-operation-basic')}>
        <label>
          <span>接口名称</span>
          <Input onChange={(event) => onChange({ ...operation, name: event.target.value })} placeholder="例如：查询商品" value={operation.name} />
        </label>
        <label>
          <span>请求方式</span>
          <Select
            onChange={(method) => onChange({ ...operation, method })}
            options={['GET', 'POST', 'PUT', 'DELETE'].map((method) => ({ label: method, value: method }))}
            value={operation.method}
          />
        </label>
        <label className={cx('data-source-form-grid-wide')}>
          <span>路径</span>
          <Input onChange={(event) => onChange({ ...operation, path: event.target.value })} placeholder="/products/{productId}" value={operation.path} />
        </label>
      </div>
      <DataSourceParameterEditor location="path" onChange={(pathParameters) => onChange({ ...operation, pathParameters })} parameters={operation.pathParameters} />
      <DataSourceParameterEditor location="query" onChange={(queryParameters) => onChange({ ...operation, queryParameters })} parameters={operation.queryParameters} />
      <HeaderEditor headers={operation.headers} onChange={(headers) => onChange({ ...operation, headers })} />
      <section className={cx('data-source-operation-section', 'data-source-json-samples-section')}>
        <div className={cx('data-source-operation-section-body')}><div className={cx('data-source-sample-stack')}>
          <JsonSampleTabs editable descriptions={operation.requestFieldDescriptionsDraft} fieldTypes={operation.requestFieldTypesDraft} label="请求体" onChange={(text) => onChange({ ...operation, requestSampleText: text })} onDescriptionChange={(path, description) => updateFieldDescription('requestFieldDescriptionsDraft', path, description)} onTypeChange={(path, type) => updateFieldType('request', path, type)} text={operation.requestSampleText} />
          <JsonSampleTabs editable descriptions={operation.responseFieldDescriptionsDraft} fieldTypes={operation.responseFieldTypesDraft} label="响应体" onChange={(text) => onChange({ ...operation, responseSampleText: text })} onDescriptionChange={(path, description) => updateFieldDescription('responseFieldDescriptionsDraft', path, description)} onTypeChange={(path, type) => updateFieldType('response', path, type)} text={operation.responseSampleText} />
        </div></div>
      </section>
    </div>
  )
}
