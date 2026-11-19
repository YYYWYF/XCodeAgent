import { ApiOutlined, DeleteOutlined, FileTextOutlined, PlusOutlined, SendOutlined } from '@ant-design/icons'
import { Button, Input, Select, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { DataSourceHeader, DataSourceOperationSection } from '../../typings'
import { confirmWorkspaceAction } from '../workspaceDialogs'
import { cx } from '../../utils'
import DataSourceParameterEditor from './DataSourceParameterEditor'
import DataSourceOperationBodyEditor from './DataSourceOperationBodyEditor'
import DataSourceEditorSection from './DataSourceEditorSection'
import { clearOperationSection, sectionMeta } from './dataSourceOperationConfig'
import OperationConfigurationBar, { OperationContentPicker } from './DataSourceOperationConfiguration'
import type { OperationDraft } from './operationEditorDraft'

const { Text } = Typography

/** 编辑域名级共享 Header 的固定名称和值。 */
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
  onPathBlur,
  onSectionsChange,
  phase = 'edit',
  sections,
  theme
}: {
  initiallyCollapsed?: boolean
  operation: OperationDraft
  onChange: (operation: OperationDraft) => void
  onPathBlur: () => void
  onSectionsChange: (sections: Set<DataSourceOperationSection>) => void
  phase?: 'selection' | 'edit'
  sections: ReadonlySet<DataSourceOperationSection>
  theme: 'light' | 'dark'
}): ReactElement {
  /** 关闭一个已选模块，并清理其对应草稿。 */
  const removeSection = (section: DataSourceOperationSection): void => {
    if (section === 'path') return
    const meta = sectionMeta(section)
    const nextOperation = clearOperationSection(operation, section)
    const hasContent = section === 'query' ? operation.queryParameters.length > 0
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
            <Input onBlur={onPathBlur} onChange={(event) => onChange({ ...operation, path: event.target.value })} placeholder="/products/{productId}" value={operation.path} />
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
            {sections.has('path') ? <DataSourceParameterEditor defaultExpanded={!initiallyCollapsed} location="path" nested onChange={(pathParameters) => onChange({ ...operation, pathParameters })} parameters={operation.pathParameters} /> : null}
            {sections.has('query') ? <DataSourceParameterEditor defaultExpanded={!initiallyCollapsed} location="query" nested onChange={(queryParameters) => onChange({ ...operation, queryParameters })} onRemove={() => removeSection('query')} parameters={operation.queryParameters} /> : null}
            {sections.has('header') ? <DataSourceParameterEditor defaultExpanded={!initiallyCollapsed} location="header" nested onChange={(headers) => onChange({ ...operation, headers })} onRemove={() => removeSection('header')} parameters={operation.headers} /> : null}
          </div>
        </DataSourceEditorSection> : null}
        {sections.has('requestBody') && <DataSourceOperationBodyEditor side="request" operation={operation}
          theme={theme} defaultExpanded={!initiallyCollapsed} onChange={onChange} onRemove={() => removeSection('requestBody')} />}
        {sections.has('responseBody') && <DataSourceOperationBodyEditor side="response" operation={operation}
          theme={theme} defaultExpanded={!initiallyCollapsed} onChange={onChange} onRemove={() => removeSection('responseBody')} />}
      </>}
    </div>
  )
}
