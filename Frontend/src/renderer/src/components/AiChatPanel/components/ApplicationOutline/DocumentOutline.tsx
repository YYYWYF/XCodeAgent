import { CaretDownOutlined } from '@ant-design/icons'
import { useState } from 'react'
import type { ReactElement } from 'react'
import { cx } from '../../../../utils'

import { ARTIFACT_DOCUMENTS, type ArtifactDocumentKey } from './artifactDocuments'

type Props = {
  query: string
  selectedKey?: ArtifactDocumentKey
  onSelect: (key: ArtifactDocumentKey) => void
}

/** 在应用产物目录中集中展示上游文档，独立支持搜索、展开和键盘选择。 */
export default function DocumentOutline({ query, selectedKey, onSelect }: Props): ReactElement {
  const [expanded, setExpanded] = useState(true)
  const documents = ARTIFACT_DOCUMENTS.filter((doc) =>
    doc.label.toLocaleLowerCase().includes(query.trim().toLocaleLowerCase())
  )
  return (
    <section className={cx('outline-section')}>
      <button
        aria-expanded={expanded}
        className={cx('outline-section-heading')}
        onClick={() => setExpanded((current) => !current)}
        type="button"
      >
        <CaretDownOutlined className={cx(!expanded && 'collapsed')} />
        <span>文档</span>
      </button>
      {expanded ? (
        <div className={cx('api-list')}>
          {documents.map(({ key, label, icon: Icon }) => (
            <button
              aria-current={selectedKey === key ? 'page' : undefined}
              className={cx('document-row', selectedKey === key && 'selected')}
              key={key}
              onClick={() => onSelect(key)}
              type="button"
            >
              <Icon aria-hidden="true" className={cx('document-icon-' + key)} />
              <span>{label}</span>
            </button>
          ))}
          {documents.length === 0 ? (
            <div className={cx('outline-empty')}>没有匹配的文档</div>
          ) : null}
        </div>
      ) : null}
    </section>
  )
}
