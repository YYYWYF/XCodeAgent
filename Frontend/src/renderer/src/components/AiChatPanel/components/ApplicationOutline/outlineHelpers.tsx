import {
  CaretDownOutlined,
  FileTextOutlined,
  FolderOpenOutlined,
  FolderOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import { useState } from 'react'
import type { ApplicationMenuItem } from '../../../../typings'
import { cx } from '../../../../utils'

type OutlineRowProps = {
  disabled?: boolean
  item: ApplicationMenuItem
  level: number
  onSelect: (key: string) => void
  selectedKey: string
  visibleKeys: Set<string>
}

/** 递归统计当前目录节点下的页面数量，用于目录标签展示。 */
function outlineLeafCount(item: ApplicationMenuItem): number {
  if (item.type !== 'menu') return 1
  return (item.children || []).reduce((total, child) => total + outlineLeafCount(child), 0)
}

/** 渲染单个页面目录节点，展示名称、路径和目录页面数量。 */
export function OutlineRow({
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
  const childPageCount = isFolder ? outlineLeafCount(item) : 0

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
              <span className={cx('outline-menu-count')}>{childPageCount} 个页面</span>
            ) : null}
          </span>
          {item.path ? <span className={cx('outline-meta')}>{item.path}</span> : null}
        </span>
      </button>
      {isFolder && expanded && children.length > 0 ? (
        <div className={cx('outline-children')}>
          {children.map((child) => (
            <OutlineRow
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
