import type { ReactElement } from 'react'
import { mappingFieldTitle } from './model'
import './MappingFieldLabel.less'

type Props = { name: string; description?: string; title?: string; monospace?: boolean }

/** 并列展示字段名和注释，列宽不足时先省略注释。 */
export function MappingFieldLabel({ name, description, title, monospace }: Props): ReactElement {
  const Name = monospace ? 'code' : 'span'
  const comment = description?.trim()
  return <span className="mapping-field-label" title={title}>
    <Name className="mapping-field-label-name">{name}</Name>
    {comment && <span className="mapping-field-label-comment">（{comment}）</span>}
  </span>
}

/** 下拉列表展示注释，选中值保持简洁，悬停和搜索可使用完整字段信息。 */
export function mappingFieldOption({ name, type, description, value = name }: { name: string; type: string; description?: string; value?: string }) {
  const title = mappingFieldTitle(name, type, description)
  return {
    value,
    title,
    'aria-label': title,
    selectedLabel: name,
    label: <MappingFieldLabel name={name} description={description} />
  }
}
