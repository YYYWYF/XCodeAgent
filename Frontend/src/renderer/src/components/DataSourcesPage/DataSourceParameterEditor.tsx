import { DeleteOutlined, FileTextOutlined, LinkOutlined, PlusOutlined, SearchOutlined } from '@ant-design/icons'
import { Button, Checkbox, Input, Select } from 'antd'
import type { ReactElement } from 'react'
import type { DataSourceParameter } from '../../typings'
import { cx } from '../../utils'
import { FIELD_TYPE_OPTIONS, PATH_FIELD_TYPES } from './jsonFieldTypes'
import DataSourceEditorSection from './DataSourceEditorSection'

/** 参数编辑行的稳定标识只用于草稿，不进入接口存储。 */
export type ParameterDraft = DataSourceParameter & { rowId: string }

type Props = {
  defaultExpanded?: boolean
  location: 'path' | 'query'
  nested?: boolean
  parameters: ParameterDraft[]
  onChange: (parameters: ParameterDraft[]) => void
  onRemove?: () => void
}

/** 独立编辑传入的 Path 或 Query 数组，以稳定行标识更新参数。 */
export default function DataSourceParameterEditor({ defaultExpanded = true, location, nested = false, parameters, onChange, onRemove }: Props): ReactElement {
  const title = location === 'path' ? 'Path 参数' : 'Query 参数'
  const rows = parameters
  const options = location === 'path' ? FIELD_TYPE_OPTIONS.filter((option) => PATH_FIELD_TYPES.includes(option.value)) : FIELD_TYPE_OPTIONS
  const icon = location === 'path' ? <LinkOutlined /> : <SearchOutlined />
  /** 在当前分组新增一行，Path 参数固定必填。 */
  const addParameter = (): void => onChange([...parameters, {
    rowId: crypto.randomUUID(), name: '', type: 'string', required: location === 'path', description: ''
  }])
  /** 根据稳定行标识修改当前数组，不影响另一组参数或输入焦点。 */
  const updateParameter = (rowId: string, patch: Partial<DataSourceParameter>): void => {
    onChange(parameters.map((parameter) => parameter.rowId === rowId ? { ...parameter, ...patch } : parameter))
  }
  const actions = <>
    <Button className={cx('data-source-editor-add-button', 'data-source-editor-parameter-add-button')} icon={<PlusOutlined />} onClick={addParameter} size="small">添加参数</Button>
    {onRemove ? <Button aria-label={`删除${title}`} className={cx('data-source-editor-remove-button')} icon={<DeleteOutlined />} onClick={onRemove} size="small" type="text" /> : null}
  </>
  return <DataSourceEditorSection actions={actions} className={nested ? 'data-source-editor-section-nested' : undefined} defaultExpanded={defaultExpanded} icon={nested ? undefined : icon} title={title} tone={location}>
    <div className={cx('data-source-editor-section-content')}>
      {rows.length ? <>
        <div className={cx('data-source-operation-table')}>
          <div aria-hidden="true" className={cx('data-source-parameter-row-labels')}><span>参数名</span><span>字段类型</span><span>必填</span><span>描述</span><span>操作</span></div>
          <div className={cx('data-source-operation-rows')}>
            {rows.map((parameter) => <div className={cx('data-source-parameter-row')} key={parameter.rowId}>
              <Input aria-label={`${title}名称`} className={cx('data-source-parameter-name')} onChange={(event) => updateParameter(parameter.rowId, { name: event.target.value })} placeholder="参数名" value={parameter.name} />
              <Select aria-label={`${title} ${parameter.name} 类型`} className={cx('data-source-parameter-type')} getPopupContainer={(trigger: HTMLElement) => trigger.closest<HTMLElement>('.ant-modal-wrap') || trigger.closest<HTMLElement>('.source-drawer-stack') || document.body} onChange={(type) => updateParameter(parameter.rowId, { type })} options={options} value={parameter.type} />
              <Checkbox checked={parameter.required} className={cx('data-source-parameter-required')} disabled={location === 'path'} onChange={(event) => updateParameter(parameter.rowId, { required: event.target.checked })}>必填</Checkbox>
              <Input aria-label={`${title} ${parameter.name} 描述`} className={cx('data-source-parameter-description')} onChange={(event) => updateParameter(parameter.rowId, { description: event.target.value })} placeholder="描述（可选）" value={parameter.description} />
              <Button aria-label={`删除${title} ${parameter.name}`} className={cx('data-source-operation-row-delete')} icon={<DeleteOutlined />} onClick={() => onChange(parameters.filter((item) => item.rowId !== parameter.rowId))} type="text" />
            </div>)}
          </div>
        </div>
      </> : <div className={cx('data-source-operation-section-empty')}><FileTextOutlined /><span>暂无 {title}，点击右上角添加</span></div>}
    </div>
  </DataSourceEditorSection>
}
