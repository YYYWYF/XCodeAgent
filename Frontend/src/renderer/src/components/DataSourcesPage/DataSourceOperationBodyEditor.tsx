import { ApiOutlined, DeleteOutlined, FileTextOutlined } from '@ant-design/icons'
import { Button, Modal, Radio, message } from 'antd'
import { useState } from 'react'
import type { DataSourceFieldType } from '../../typings'
import { cx } from '../../utils'
import DataSourceEditorSection from './DataSourceEditorSection'
import { sectionMeta } from './dataSourceOperationConfig'
import { JsonSampleTabs } from './JsonStructureViewer'
import { convertJsonFieldType } from './jsonFieldTypes'
import { parseJsonSampleText } from './jsonStructure'
import type { OperationDraft } from './operationEditorDraft'

type BodyView = 'structure' | 'sample'
type Props = {
  side: 'request' | 'response'
  operation: OperationDraft
  theme: 'light' | 'dark'
  compact?: boolean
  defaultExpanded?: boolean
  onChange: (next: OperationDraft) => void
  onRemove?: () => void
}

/** 在抽屉子页及接口弹窗中共用 JSON 字段说明、类型和样例编辑逻辑。 */
export default function DataSourceOperationBodyEditor({
  side, operation, theme, compact = false, defaultExpanded, onChange, onRemove
}: Props) {
  const label = sectionMeta(side === 'request' ? 'requestBody' : 'responseBody').title
  const descriptionsKey = side === 'request' ? 'requestFieldDescriptionsDraft' : 'responseFieldDescriptionsDraft'
  const typesKey = side === 'request' ? 'requestFieldTypesDraft' : 'responseFieldTypesDraft'
  const sampleKey = side === 'request' ? 'requestSampleText' : 'responseSampleText'
  const [view, setView] = useState<BodyView>(compact && !operation[sampleKey].trim() ? 'sample' : 'structure')

  /** 更新同一 JSON 字段的描述，不受样例暂时无效影响。 */
  function updateDescription(path: string, description: string): void {
    const descriptions = { ...operation[descriptionsKey] }
    if (description.trim()) descriptions[path] = description
    else delete descriptions[path]
    onChange({ ...operation, [descriptionsKey]: descriptions })
  }

  /** 一起转换样例值和声明的字段类型，移除子结构前沿用确认。 */
  function updateType(path: string, type?: DataSourceFieldType): void {
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
    /** 只有用户确认转换后才提交新的样例与类型。 */
    const apply = (): void => onChange({
      ...operation,
      [sampleKey]: JSON.stringify(conversion.value, null, 2),
      [typesKey]: { ...types, [path]: type }
    })
    if (conversion.destructiveCount) {
      Modal.confirm({
        centered: true, title: '转换字段类型？',
        wrapClassName: cx('data-source-editor-modal-wrap', `theme-${theme}`),
        content: `转换为 ${type} 将移除 ${conversion.destructiveCount} 处对象或数组的子内容。`,
        cancelText: '取消', okText: '确认转换', onOk: apply
      })
    } else apply()
  }

  const switcher = compact
    ? <Radio.Group aria-label={label + '视图'} size="small" value={view} onChange={(event) => setView(event.target.value)}>
        <Radio.Button value="structure">结构</Radio.Button><Radio.Button value="sample">样例</Radio.Button>
      </Radio.Group>
    : <div aria-label="JSON 内容视图" className={cx('data-source-json-header-tabs')} role="tablist">
        <button aria-selected={view === 'structure'} className={cx(view === 'structure' && 'is-active')} onClick={() => setView('structure')} role="tab" type="button">结构</button>
        <button aria-selected={view === 'sample'} className={cx(view === 'sample' && 'is-active')} onClick={() => setView('sample')} role="tab" type="button">样例</button>
      </div>
  const remove = onRemove && <Button aria-label={`删除${label}配置`} className={cx('data-source-editor-remove-button')}
    icon={<DeleteOutlined />} onClick={onRemove} size="small" type="text" />
  const body = <JsonSampleTabs activeKey={view} editable descriptions={operation[descriptionsKey]}
    fieldTypes={operation[typesKey]} label={label} onActiveKeyChange={(key) => setView(key === 'sample' ? 'sample' : 'structure')}
    onChange={(text) => onChange({ ...operation, [sampleKey]: text })} onDescriptionChange={updateDescription}
    onTypeChange={updateType} showTabs={false} showTitle={false} text={operation[sampleKey]} />
  if (compact) return <section className="source-operation-config-section">
    <header className="source-operation-config-heading"><h4>{label}</h4><div>{switcher}{remove}</div></header>
    {body}
  </section>
  return <DataSourceEditorSection actions={remove} defaultExpanded={defaultExpanded} headerContent={switcher}
    icon={side === 'request' ? <ApiOutlined /> : <FileTextOutlined />} title={label} tone={side}>
    {body}
  </DataSourceEditorSection>
}
