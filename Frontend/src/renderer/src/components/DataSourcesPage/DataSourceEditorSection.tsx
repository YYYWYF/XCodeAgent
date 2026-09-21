import { DownOutlined, UpOutlined } from '@ant-design/icons'
import type { ReactNode, ReactElement } from 'react'
import { useState } from 'react'
import { cx } from '../../utils'

export type DataSourceEditorSectionTone = 'basic' | 'path' | 'query' | 'header' | 'request' | 'response'

type Props = {
  actions?: ReactNode
  children: ReactNode
  className?: string
  defaultExpanded?: boolean
  headerContent?: ReactNode
  icon?: ReactNode
  title: string
  tone?: DataSourceEditorSectionTone
}

/** 渲染编辑态统一的卡片标题、操作区和折叠状态。 */
export default function DataSourceEditorSection({ actions, children, className, defaultExpanded = true, headerContent, icon, title, tone = 'basic' }: Props): ReactElement {
  const [expanded, setExpanded] = useState(defaultExpanded)
  return (
    <section className={cx('data-source-operation-section', 'data-source-editor-section', `tone-${tone}`, !expanded && 'is-collapsed', className)}>
      <header className={cx('data-source-editor-section-header')}>
        <button aria-expanded={expanded} className={cx('data-source-editor-section-toggle')} onClick={() => setExpanded((current) => !current)} type="button">
          {icon ? <span className={cx('data-source-editor-section-icon')}>{icon}</span> : null}
          <span className={cx('data-source-editor-section-heading')}><strong>{title}</strong></span>
        </button>
        {headerContent ? <div className={cx('data-source-editor-section-header-content')}>{headerContent}</div> : null}
        <div className={cx('data-source-editor-section-actions')}>
          {actions}
          <button aria-label={expanded ? `收起${title}` : `展开${title}`} aria-expanded={expanded} className={cx('data-source-editor-section-arrow')} onClick={() => setExpanded((current) => !current)} type="button">{expanded ? <UpOutlined /> : <DownOutlined />}</button>
        </div>
      </header>
      {expanded ? <div className={cx('data-source-editor-section-body')}>{children}</div> : null}
    </section>
  )
}
