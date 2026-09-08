import {
  CaretDownOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  FolderOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { ApplicationMenuItem, DevelopmentArtifactProgress } from '../../../../typings'
import { developmentCompletedCount } from '../../../../developmentArtifacts'
import DevelopmentStatusDot from './DevelopmentStatusDot'
import { cx } from '../../../../utils'

type OutlineRowProps = {
  progressByPage?: Record<string, DevelopmentArtifactProgress>
  disabled?: boolean
  item: ApplicationMenuItem
  level: number
  onSelect: (key: string) => void
  selectedKey: string
  visibleKeys: Set<string>
}

/** 递归统计当前目录节点下的页面数量，用于目录标签展示。 */
function outlineLeafKeys(item: ApplicationMenuItem): string[] {
  if (item.type !== 'menu') return [item.pageKey || item.key]
  return (item.children || []).flatMap(outlineLeafKeys)
}

/** 渲染单个页面目录节点，展示名称、路径和目录页面数量。 */
export function OutlineRow({
  progressByPage,
  disabled = false,
  item,
  level,
  onSelect,
  selectedKey,
  visibleKeys
}: OutlineRowProps): ReactElement {
  const [expanded, setExpanded] = useState(true)
  const children = item.children?.filter((child) => visibleKeys.has(child.key)) || []
  const isFolder = item.type === 'menu' || children.length > 0
  const selected = selectedKey === item.key
  const pageKeys = outlineLeafKeys(item)
  const completed = developmentCompletedCount(pageKeys.map((key) => progressByPage?.[key]))

  return (
    <div className={cx('outline-node')}>
      <button
        aria-current={selected ? 'page' : undefined}
        aria-expanded={isFolder ? expanded : undefined}
        className={cx('outline-row', selected && 'selected')}
        disabled={disabled && !isFolder}
        onClick={() => {
          if (isFolder) setExpanded((current) => !current)
          else if (!disabled) onSelect(item.key)
        }}
        style={{ '--outline-level': level } as React.CSSProperties}
        type="button"
      >
        <span className={cx('outline-caret')}>
          {isFolder ? <CaretDownOutlined className={cx(!expanded && 'collapsed')} /> : null}
        </span>
        <span className={cx('outline-icon')}>
          {isFolder ? expanded ? <FolderOpenOutlined /> : <FolderOutlined /> : <FileTextOutlined />}
        </span>
        <span className={cx('outline-copy')}>
          <span className={cx('outline-label-row')}>
            <span className={cx('outline-label')}>{item.label}</span>
            {isFolder ? (
              <span className={cx('development-count')}>
                {completed}/{pageKeys.length}
              </span>
            ) : null}
          </span>
          {item.path ? <span className={cx('outline-meta')}>{item.path}</span> : null}
        </span>
        {!isFolder ? (
          <DevelopmentStatusDot progress={progressByPage?.[item.pageKey || item.key]} />
        ) : null}
      </button>
      {isFolder && expanded && children.length > 0 ? (
        <div className={cx('outline-children')}>
          {children.map((child) => (
            <OutlineRow
              progressByPage={progressByPage}
              disabled={disabled}
              item={child}
              key={child.key}
              level={level + 1}
              onSelect={onSelect}
              selectedKey={selectedKey}
              visibleKeys={visibleKeys}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}
