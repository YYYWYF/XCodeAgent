import {
  FileTextOutlined,
  FolderOpenOutlined
} from '@ant-design/icons'
import { Tag, Typography } from 'antd'
import type { CSSProperties, ReactElement } from 'react'
import type { DevelopmentPlanningPageTreeNode } from '../../typings'
import { cx } from '../../utils'
import './ProjectPlanPageTreePreview.less'

const { Text } = Typography

type Props = {
  nodes: DevelopmentPlanningPageTreeNode[]
  title?: string
  emptyText?: string
}

/** 规范化菜单或页面名称，避免空白字符串显示成空标题。 */
function normalizeTreeNodeLabel(value: unknown, fallback: string): string {
  const label = String(value || '').trim()
  return label || fallback
}

/** 递归统计当前菜单节点下的页面叶子数量，便于展示目录规模。 */
function leafPageCount(node: DevelopmentPlanningPageTreeNode): number {
  if (node.type === 'page') return 1
  return (node.children || []).reduce((total, child) => total + leafPageCount(child), 0)
}

/** 仅保留对象数组项，避免菜单树解析被脏数据打断。 */
function recordItems(value: unknown): Array<Record<string, unknown>> {
  return Array.isArray(value)
    ? value.filter(
        (item): item is Record<string, unknown> =>
          Boolean(item) && typeof item === 'object' && !Array.isArray(item)
      )
    : []
}

/** 判断当前 frontend_pages 节点是否为菜单目录节点。 */
function isMenuNode(record: Record<string, unknown>): boolean {
  const pageId = String(record.pageId || record.id || '').trim()
  return Array.isArray(record.children) && !pageId
}

/** 把 ProjectPlan.frontend_pages 递归转换为前端确认界面可复用的树节点。 */
export function projectPlanPageTreeNodes(value: unknown): DevelopmentPlanningPageTreeNode[] {
  const nodes: DevelopmentPlanningPageTreeNode[] = []
  recordItems(value).forEach((record, index) => {
    if (isMenuNode(record)) {
      const children = projectPlanPageTreeNodes(record.children)
      if (children.length === 0) return
      const uniquePath = String(record.unique_path || '').trim()
      nodes.push({
        key: uniquePath || `menu-${index + 1}`,
        type: 'menu',
        label: normalizeTreeNodeLabel(record.name, `菜单 ${index + 1}`),
        uniquePath,
        children
      })
      return
    }
    const pageId = String(record.pageId || record.id || '').trim()
    if (!pageId) return
    nodes.push({
      key: pageId,
      type: 'page',
      pageId,
      label: normalizeTreeNodeLabel(record.name, pageId),
      path: String(record.path || '/'),
      purpose: normalizeTreeNodeLabel(record.description || record.name, '业务页面')
    })
  })
  return nodes
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
      <div
        className={cx('project-plan-tree-node', 'menu-node')}
        style={{ '--tree-level': level } as CSSProperties}
      >
        <div className={cx('project-plan-tree-row')}>
          <span className={cx('project-plan-tree-icon')} aria-hidden="true">
            <FolderOpenOutlined />
          </span>
          <div className={cx('project-plan-tree-copy')}>
            <div className={cx('project-plan-tree-headline')}>
              <Tag color="purple">菜单</Tag>
              <Text strong>{node.label}</Text>
              <Text className={cx('project-plan-tree-count')} type="secondary">
                {childPageCount} 个页面
              </Text>
            </div>
            {node.uniquePath ? (
              <Text className={cx('project-plan-tree-path')} code>
                {node.uniquePath}
              </Text>
            ) : null}
          </div>
        </div>
        <div className={cx('project-plan-tree-children')}>
          {(node.children || []).map((child) => (
            <TreeNodeItem key={child.key} level={level + 1} node={child} />
          ))}
        </div>
      </div>
    )
  }
  return (
    <div
      className={cx('project-plan-tree-node', 'page-node')}
      style={{ '--tree-level': level } as CSSProperties}
    >
      <div className={cx('project-plan-tree-row')}>
        <span className={cx('project-plan-tree-icon')} aria-hidden="true">
          <FileTextOutlined />
        </span>
        <div className={cx('project-plan-tree-copy')}>
          <div className={cx('project-plan-tree-headline')}>
            <Tag>页面</Tag>
            <Text strong>{node.label}</Text>
            {node.pageId ? (
              <Text className={cx('project-plan-tree-page-id')} type="secondary">
                {node.pageId}
              </Text>
            ) : null}
          </div>
          {node.path ? (
            <Text className={cx('project-plan-tree-path')} code>
              {node.path}
            </Text>
          ) : null}
        </div>
      </div>
      {node.purpose ? (
        <Text className={cx('project-plan-tree-purpose')} type="secondary">
          {node.purpose}
        </Text>
      ) : null}
    </div>
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
