import { FileTextOutlined, FolderOpenOutlined } from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { ReactElement } from 'react'
import type { DevelopmentPlanningPageTreeNode } from '../../typings'
import { cx } from '../../utils'
import './ProjectPlanPageTreePreview.less'

const { Text } = Typography

type Props = {
  nodes: DevelopmentPlanningPageTreeNode[]
  title?: string
  emptyText?: string
}

/** 递归统计当前菜单节点下的页面叶子数量，便于展示目录规模。 */
function leafPageCount(node: DevelopmentPlanningPageTreeNode): number {
  if (node.type === 'page') return 1
  return (node.children || []).reduce((total, child) => total + leafPageCount(child), 0)
}

/** 递归渲染单个菜单节点或页面节点。 */
function TreeNodeItem({
  level,
  node
}: {
  level: number
  node: DevelopmentPlanningPageTreeNode
}): ReactElement {
  if (node.type === 'menu') {
    const childPageCount = leafPageCount(node)
    return (
      <section className={cx('project-plan-tree-node', 'menu-node', level > 0 && 'is-nested')}>
        <header className={cx('project-plan-tree-menu-header')}>
          <span className={cx('project-plan-tree-menu-icon')} aria-hidden="true">
            <FolderOpenOutlined />
          </span>
          <div className={cx('project-plan-tree-menu-copy')}>
            <Text strong>{node.label}</Text>
            <Text type="secondary">{childPageCount} 个页面</Text>
          </div>
          {node.uniquePath ? (
            <Tag className={cx('project-plan-code-tag')}>{node.uniquePath}</Tag>
          ) : null}
        </header>
        <div className={cx('project-plan-tree-children')}>
          {(node.children || []).map((child) => (
            <TreeNodeItem key={child.key} level={level + 1} node={child} />
          ))}
        </div>
      </section>
    )
  }
  return (
    <article className={cx('project-plan-tree-node', 'page-node')}>
      <span className={cx('project-plan-tree-page-icon')} aria-hidden="true">
        <FileTextOutlined />
      </span>
      <div className={cx('project-plan-tree-page-copy')}>
        <div className={cx('project-plan-tree-page-title')}>
          <Text strong>{node.label}</Text>
          {node.path ? <Tag className={cx('project-plan-code-tag')}>{node.path}</Tag> : null}
        </div>
        {node.purpose ? <Text type="secondary">{node.purpose}</Text> : null}
      </div>
    </article>
  )
}

/** 以可视化层级预览 ProjectPlan 中的菜单与页面归属关系。 */
export default function ProjectPlanPageTreePreview({
  nodes,
  title = '菜单结构预览',
  emptyText = '项目计划中暂无菜单结构。'
}: Props): ReactElement {
  return (
    <section className={cx('project-plan-tree-preview')}>
      <div className={cx('project-plan-tree-header')}>
        <Text strong>{title}</Text>
      </div>
      {nodes.length ? (
        <div className={cx('project-plan-tree-list')}>
          {nodes.map((node) => (
            <TreeNodeItem key={node.key} level={0} node={node} />
          ))}
        </div>
      ) : (
        <div className={cx('project-plan-tree-empty')}>{emptyText}</div>
      )}
    </section>
  )
}
