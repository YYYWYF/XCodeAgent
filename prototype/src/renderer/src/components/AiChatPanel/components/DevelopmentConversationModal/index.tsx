import {
  ApiOutlined,
  AppstoreOutlined,
  CaretDownOutlined,
  CheckCircleOutlined,
  FileTextOutlined,
  FolderOutlined,
  MessageOutlined
} from '@ant-design/icons'
import { Button, Modal } from 'antd'
import type { ReactElement } from 'react'
import { useEffect, useMemo, useRef, useState } from 'react'
import { cx } from '../../../../utils'
import './DevelopmentConversationModal.less'

export type DevelopmentConversationTarget = {
  apiContractId?: string
  description?: string
  endpointId?: string
  id: string
  kind: 'page' | 'endpoint'
  label: string
  pageId?: string
  path: string
  relatedArtifactLabel?: string
}

export type DevelopmentConversationTreeNode = {
  children?: DevelopmentConversationTreeNode[]
  id: string
  kind: 'application' | 'group' | 'page' | 'endpoint'
  label: string
  target?: DevelopmentConversationTarget
}

type Props = {
  open: boolean
  tree: DevelopmentConversationTreeNode
  onCancel: () => void
  onConfirm: (target: DevelopmentConversationTarget) => Promise<void>
}

type CompletionProps = {
  endpointCount: number
  onCancel: () => void
  onConfirm: () => void
  open: boolean
  pageCount: number
}

type StageCompletionProps = {
  onCancel: () => void
  onConfirm: () => void
  open: boolean
}

type ConversationConfirmProps = {
  onCancel: () => void
  onConfirm: () => Promise<void>
  open: boolean
  target?: DevelopmentConversationTarget
}

/** 深度遍历产物树并找到当前选中的可开发产物。 */
function findTarget(
  node: DevelopmentConversationTreeNode,
  selectedId: string
): DevelopmentConversationTarget | undefined {
  if (node.target?.id === selectedId) return node.target
  for (const child of node.children || []) {
    const target = findTarget(child, selectedId)
    if (target) return target
  }
  return undefined
}

/** 收集树中默认展开的分组，确保首次打开即可看见真实产物层级。 */
function collectExpandableIds(node: DevelopmentConversationTreeNode): string[] {
  return [
    ...(node.children?.length ? [node.id] : []),
    ...(node.children || []).flatMap(collectExpandableIds)
  ]
}

/** 在首次进入开发阶段时，让用户明确选择首个产物并创建拥有编辑权的对话。 */
export default function DevelopmentConversationModal({
  open,
  tree,
  onCancel,
  onConfirm
}: Props): ReactElement {
  const [selectedId, setSelectedId] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const defaultExpandedIds = useMemo(() => collectExpandableIds(tree), [tree])
  const [expandedIds, setExpandedIds] = useState<string[]>(defaultExpandedIds)
  const previousOpenRef = useRef(open)

  useEffect(() => {
    // 只在弹框开关边沿同步状态；树数据刷新不应反复重置展开集合并触发 Modal 更新死循环。
    if (previousOpenRef.current === open) return
    previousOpenRef.current = open
    if (!open) {
      setSelectedId('')
      setSubmitting(false)
      return
    }
    setExpandedIds(defaultExpandedIds)
  }, [defaultExpandedIds, open])

  /** 创建所选产物的首个对话，并在异步保存期间防止重复提交。 */
  const handleConfirm = async (): Promise<void> => {
    const target = findTarget(tree, selectedId)
    if (!target || submitting) return
    setSubmitting(true)
    try {
      await onConfirm(target)
    } finally {
      setSubmitting(false)
    }
  }

  /** 展开或收起指定分组，并维持其他分组的浏览状态。 */
  const toggleExpanded = (nodeId: string): void => {
    setExpandedIds((current) =>
      current.includes(nodeId) ? current.filter((id) => id !== nodeId) : [...current, nodeId]
    )
  }

  /** 递归渲染紧凑产物树；只有页面和接口叶子可被选择。 */
  const renderTreeNode = (node: DevelopmentConversationTreeNode, depth = 0): ReactElement => {
    const expandable = Boolean(node.children?.length)
    const expanded = expandedIds.includes(node.id)
    const selected = node.target?.id === selectedId
    const leaf = node.kind === 'page' || node.kind === 'endpoint'
    const icon =
      node.kind === 'application' ? (
        <AppstoreOutlined />
      ) : node.kind === 'group' ? (
        <FolderOutlined />
      ) : node.kind === 'page' ? (
        <FileTextOutlined />
      ) : (
        <ApiOutlined />
      )

    return (
      <div className={cx('development-conversation-tree-node')} key={node.id} role="none">
        <button
          aria-expanded={expandable ? expanded : undefined}
          aria-selected={leaf ? selected : undefined}
          className={cx('development-conversation-tree-row', node.kind, selected && 'selected')}
          onClick={() =>
            leaf && node.target ? setSelectedId(node.target.id) : toggleExpanded(node.id)
          }
          role="treeitem"
          style={{ paddingLeft: 12 + depth * 18 }}
          type="button"
        >
          <span className={cx('development-conversation-tree-caret')} aria-hidden="true">
            {expandable ? <CaretDownOutlined className={cx(!expanded && 'collapsed')} /> : null}
          </span>
          <span className={cx('development-conversation-tree-icon')} aria-hidden="true">
            {icon}
          </span>
          <span className={cx('development-conversation-tree-label')}>{node.label}</span>
          {node.target ? (
            <code title={node.target.path}>{node.target.path}</code>
          ) : node.children ? (
            <small>{node.children.length}</small>
          ) : null}
          {leaf ? (
            <span className={cx('development-conversation-radio')} aria-hidden="true" />
          ) : null}
        </button>
        {expandable && expanded ? (
          <div role="group">{node.children?.map((child) => renderTreeNode(child, depth + 1))}</div>
        ) : null}
      </div>
    )
  }

  return (
    <Modal
      centered
      closable={false}
      footer={null}
      getContainer={false}
      maskClosable={false}
      onCancel={onCancel}
      open={open}
      width={560}
      wrapClassName={cx('development-conversation-modal')}
    >
      <div className={cx('development-conversation-dialog')}>
        <header className={cx('development-conversation-header')}>
          <span className={cx('development-conversation-header-icon')} aria-hidden="true">
            <MessageOutlined />
          </span>
          <span>
            <strong>从一个产物开始开发</strong>
            <small>开发阶段 · 创建首个对话</small>
          </span>
        </header>

        <div className={cx('development-conversation-body')}>
          <p>开发没有固定顺序。请选择一个页面或接口，Agent 将创建对话并取得对应产物的编辑权。</p>
          <div
            aria-label="选择首个开发产物"
            className={cx('development-conversation-tree')}
            role="tree"
          >
            {renderTreeNode(tree)}
          </div>
        </div>

        <footer className={cx('development-conversation-footer')}>
          <Button disabled={submitting} onClick={onCancel}>
            稍后再说
          </Button>
          <Button
            disabled={!selectedId}
            loading={submitting}
            onClick={() => void handleConfirm()}
            type="primary"
          >
            创建对话
          </Button>
        </footer>
      </div>
    </Modal>
  )
}

/** 在产物树点击未开始产物后，取得用户创建正式对话的明确授权。 */
export function DevelopmentArtifactConversationConfirmModal({
  onCancel,
  onConfirm,
  open,
  target
}: ConversationConfirmProps): ReactElement {
  const [submitting, setSubmitting] = useState(false)

  useEffect(() => {
    if (!open) setSubmitting(false)
  }, [open])

  /** 确认创建并在落盘期间锁定操作，避免重复建立默认对话。 */
  const handleConfirm = async (): Promise<void> => {
    if (!target || submitting) return
    setSubmitting(true)
    try {
      await onConfirm()
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <Modal
      centered
      closable={false}
      footer={null}
      getContainer={false}
      maskClosable={false}
      onCancel={onCancel}
      open={open}
      width={460}
      wrapClassName={cx('development-artifact-confirm-modal')}
    >
      <div className={cx('development-artifact-confirm-dialog')}>
        <span className={cx('development-artifact-confirm-icon')} aria-hidden="true">
          <MessageOutlined />
        </span>
        <div className={cx('development-artifact-confirm-copy')}>
          <strong>创建产物对话</strong>
          <p>
            将基于“{target?.label || '当前产物'}
            ”创建一个正式对话。确认后，该对话将取得产物编辑权，产物状态进入进行中。
          </p>
          {target?.path ? <code>{target.path}</code> : null}
        </div>
        <footer className={cx('development-conversation-footer')}>
          <Button disabled={submitting} onClick={onCancel}>
            取消
          </Button>
          <Button loading={submitting} onClick={() => void handleConfirm()} type="primary">
            确认创建
          </Button>
        </footer>
      </div>
    </Modal>
  )
}

/** 在全部开发产物完成后，询问用户是否进入测试阶段。 */
export function DevelopmentStageCompleteModal({
  endpointCount,
  onCancel,
  onConfirm,
  open,
  pageCount
}: CompletionProps): ReactElement {
  return (
    <Modal
      centered
      closable={false}
      footer={null}
      getContainer={false}
      maskClosable={false}
      onCancel={onCancel}
      open={open}
      width={460}
      wrapClassName={cx('development-complete-modal')}
    >
      <div className={cx('development-complete-dialog')}>
        <span className={cx('development-complete-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div className={cx('development-complete-copy')}>
          <strong>开发阶段的产物已全部完成</strong>
          <p>
            已完成 {pageCount} 个页面和 {endpointCount}{' '}
            个接口。是否进入测试阶段？测试报告会记录整体验证结果。
          </p>
        </div>
        <footer className={cx('development-conversation-footer')}>
          <Button onClick={onCancel}>暂不进入</Button>
          <Button onClick={onConfirm} type="primary">
            进入测试阶段
          </Button>
        </footer>
      </div>
    </Modal>
  )
}

/** 测试报告通过后，询问用户是否进入审查阶段；暂不进入时保留测试阶段。 */
export function TestingStageCompleteModal({
  onCancel,
  onConfirm,
  open
}: StageCompletionProps): ReactElement {
  return (
    <Modal
      centered
      closable={false}
      footer={null}
      getContainer={false}
      maskClosable={false}
      onCancel={onCancel}
      open={open}
      width={460}
      wrapClassName={cx('development-complete-modal')}
    >
      <div className={cx('development-complete-dialog')}>
        <span className={cx('development-complete-icon')} aria-hidden="true">
          <CheckCircleOutlined />
        </span>
        <div className={cx('development-complete-copy')}>
          <strong>测试阶段已完成</strong>
          <p>测试报告已保存，启动、非功能和业务测试均已通过。是否进入审查阶段？</p>
        </div>
        <footer className={cx('development-conversation-footer')}>
          <Button onClick={onCancel}>暂不进入</Button>
          <Button onClick={onConfirm} type="primary">
            进入审查阶段
          </Button>
        </footer>
      </div>
    </Modal>
  )
}
