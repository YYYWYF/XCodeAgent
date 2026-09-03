import { Alert, Button, Input, Select, Tabs } from 'antd'
import { DownOutlined, RightOutlined } from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useMemo, useState } from 'react'
import { cx } from '../../utils'
import type { DataSourceFieldType } from '../../typings'
import { FIELD_TYPE_OPTIONS, normalizeJsonFieldTypes } from './jsonFieldTypes'
import { inferJsonStructure, jsonArrayItemPath, jsonPropertyPath, formatJsonSample, parseJsonSampleText, type JsonShape } from './jsonStructure'

import './JsonStructureViewer.less'

const { TabPane } = Tabs

type FieldTypeProps = {
  fieldTypes?: Record<string, DataSourceFieldType>
  onTypeChange?: (path: string, type?: DataSourceFieldType) => void
}


/** 渲染可编辑的字段说明输入，并在聚焦时显示输入进度。 */
function FieldDescriptionEditor({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }): ReactElement {
  const [focused, setFocused] = useState(false)
  return (
    <span className={cx('data-source-json-description-editor')}>
      <Input.TextArea
        aria-label={`${label} 字段说明`}
        autoSize={{ minRows: 1, maxRows: 4 }}
        className={cx('data-source-json-tree-description-input')}
        maxLength={1024}
        onBlur={() => setFocused(false)}
        onChange={(event) => onChange(event.target.value)}
        onClick={(event) => event.stopPropagation()}
        onFocus={() => setFocused(true)}
        onKeyDown={(event) => event.stopPropagation()}
        placeholder="填写字段含义"
        value={value}
      />
      {focused ? <span className={cx('data-source-json-description-count')}>{value.length}/1024</span> : null}
    </span>
  )
}

/** 获取 JSON 节点的直接子节点，数组元素使用统一的共享路径。 */
function childEntries(shape: JsonShape, path: string): Array<{ label: string; path: string; shape: JsonShape }> {
  const entries: Array<{ label: string; path: string; shape: JsonShape }> = []
  if (shape.arrayItem) entries.push({ label: '[item]', path: jsonArrayItemPath(path), shape: shape.arrayItem })
  for (const key of shape.childOrder) entries.push({ label: key, path: jsonPropertyPath(path, key), shape: shape.children[key] })
  return entries
}

/** 递归渲染一行字段及其子节点，只有字段名列随层级缩进。 */
function renderTreeRows(shape: JsonShape, label: string, path: string, level: number, expandedPaths: Set<string>, descriptions: Record<string, string>, editable: boolean, onToggle: (path: string) => void, onDescriptionChange: ((path: string, description: string) => void) | undefined, fieldTypes: Record<string, DataSourceFieldType>, onTypeChange?: FieldTypeProps['onTypeChange']): ReactElement[] {
  const entries = childEntries(shape, path)
  const expanded = expandedPaths.has(path)
  const description = descriptions[path] || ''
  const row = (
    <div aria-expanded={entries.length ? expanded : undefined} aria-level={level + 1} className={cx('data-source-json-tree-row')} key={path} role="treeitem">
      <div className={cx('data-source-json-tree-field')} style={{ paddingLeft: `${level * 16}px` }}>
        {entries.length ? <button aria-label={expanded ? `收起 ${label}` : `展开 ${label}`} className={cx('data-source-json-tree-toggle')} onClick={() => onToggle(path)} type="button">{expanded ? <DownOutlined /> : <RightOutlined />}</button> : <span className={cx('data-source-json-tree-toggle-placeholder')} />}
        <span className={cx('data-source-json-tree-field-text')}>{label}</span>
      </div>
      {editable ? <div className={cx('data-source-json-tree-type-editor')} onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
        <Select
          aria-label={`${label} 字段类型`}
          className={cx('data-source-json-type-select')}
          getPopupContainer={(trigger: HTMLElement) => trigger.closest<HTMLElement>('.ant-modal-wrap') || trigger.parentElement!}
          onChange={(type: DataSourceFieldType | 'auto') => onTypeChange?.(path, type === 'auto' ? undefined : type)}
          options={[{ label: shape.kind, value: 'auto' }, ...FIELD_TYPE_OPTIONS]}
          value={fieldTypes[path] || 'auto'}
        />
      </div> : <code className={cx('data-source-json-tree-type')}>{fieldTypes[path] || shape.kind}</code>}
      {editable ? <FieldDescriptionEditor label={label} onChange={(value) => onDescriptionChange?.(path, value)} value={description} /> : <span className={cx('data-source-json-tree-description')}>{description || '—'}</span>}
    </div>
  )
  if (!expanded || !entries.length) return [row]
  return [row, ...entries.flatMap((entry) => renderTreeRows(entry.shape, entry.label, entry.path, level + 1, expandedPaths, descriptions, editable, onToggle, onDescriptionChange, fieldTypes, onTypeChange))]
}


/** 展示从 JSON 样例推导出的结构树，并按字段路径编辑或显示说明。 */
export function JsonStructureViewer({ value, descriptions = {}, editable = false, onDescriptionChange, fieldTypes = {}, onTypeChange }: { value: unknown; descriptions?: Record<string, string>; editable?: boolean; onDescriptionChange?: (path: string, description: string) => void } & FieldTypeProps): ReactElement {
  const result = useMemo(() => inferJsonStructure(value), [value])
  const effectiveTypes = useMemo(() => normalizeJsonFieldTypes(value, fieldTypes), [value, fieldTypes])
  const [expandedPaths, setExpandedPaths] = useState<Set<string>>(() => new Set(['$']))
  const rows = useMemo(() => renderTreeRows(result.shape, '$', '$', 0, expandedPaths, descriptions, editable, (path) => setExpandedPaths((current) => { const next = new Set(current); if (next.has(path)) next.delete(path); else next.add(path); return next }), onDescriptionChange, effectiveTypes, onTypeChange), [descriptions, editable, expandedPaths, onDescriptionChange, result.shape, effectiveTypes, onTypeChange])
  return (
    <div className={cx('data-source-json-structure')}>
      <div className={cx('data-source-json-tree-header')}><span>字段</span><span>类型</span><span>字段说明</span></div>
      <div aria-label="JSON 字段结构" className={cx('data-source-json-tree')} role="tree">{rows}</div>
      {result.truncated ? <div className={cx('data-source-json-structure-truncated')}>结构较大，仅展示前 300 个节点或 8 层；字段说明仍按完整样例保存。</div> : null}
    </div>
  )
}


/** 渲染请求或响应 JSON 的结构/样例页签，编辑态支持实时预览和字段说明。 */
export function JsonSampleTabs({ label, text, value, descriptions = {}, editable = false, onChange, onDescriptionChange, fieldTypes, onTypeChange }: { label: string; text?: string; value?: unknown; descriptions?: Record<string, string>; editable?: boolean; onChange?: (text: string) => void; onDescriptionChange?: (path: string, description: string) => void } & FieldTypeProps): ReactElement {
  const sampleText = editable ? text || '' : formatJsonSample(value)
  const parsed = useMemo(() => parseJsonSampleText(sampleText), [sampleText])
  const [activeKey, setActiveKey] = useState('structure')
  const hasNoDescribableFields = parsed.value === null
  const displayedSampleText = editable && hasNoDescribableFields ? '' : sampleText
  return (
    <section className={cx('data-source-json-panel')}>
      <Tabs activeKey={activeKey} defaultActiveKey="structure" destroyInactiveTabPane={false} onChange={setActiveKey} tabBarExtraContent={{ left: <div className={cx('data-source-json-panel-title')}><strong>{label}</strong><span>可选</span><small>用于记录接口请求和响应示例</small></div> }}>
        <TabPane key="structure" tab="结构">
          {parsed.error ? <Alert message={parsed.error} showIcon type="warning" /> : parsed.value === undefined ? <div className={cx('data-source-json-empty')}><span>添加 JSON 样例后，可查看结构并配置字段类型与说明。</span>{editable ? <Button onClick={() => setActiveKey('sample')} type="link">去填写样例</Button> : null}</div> : hasNoDescribableFields ? <div className={cx('data-source-json-empty')}>没有对应的结构内容。</div> : <JsonStructureViewer descriptions={descriptions} editable={editable} onDescriptionChange={onDescriptionChange} fieldTypes={fieldTypes} onTypeChange={onTypeChange} value={parsed.value} />}
        </TabPane>
        <TabPane key="sample" tab="样例">
          {editable ? <textarea aria-label={label} className={cx('data-source-json-editor')} onChange={(event) => onChange?.(event.target.value)} placeholder={'{\n  "items": []\n}'} value={displayedSampleText} /> : hasNoDescribableFields ? <div className={cx('data-source-json-empty', 'data-source-json-example')}><span>暂无样例内容，可参考：</span><code>{'{\n  "items": []\n}'}</code></div> : sampleText ? <pre className={cx('data-source-manager-json')}>{sampleText}</pre> : <div className={cx('data-source-json-empty')}>未配置 JSON 样例</div>}
        </TabPane>
      </Tabs>
    </section>
  )
}
