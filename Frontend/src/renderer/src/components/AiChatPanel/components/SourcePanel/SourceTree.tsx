import {
  CaretDownOutlined,
  CaretRightOutlined,
  CodeOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  LoadingOutlined
} from '@ant-design/icons'
import type { ReactElement } from 'react'
import type { WorkspaceTreeNode } from '../../../../service/workspaceTools'
import { cx } from '../../../../utils'

type Props = {
  node: WorkspaceTreeNode
  depth?: number
  expanded: Set<string>
  selectedPath: string
  branchLoading: Set<string>
  branchErrors: Record<string, string>
  onToggle: (path: string) => void
  onRetry: (path: string) => void
  onSelectFile: (node: WorkspaceTreeNode) => void
}

/** 递归渲染目录节点及其加载、失败重试状态，深度不限制可展开范围。 */
export default function SourceTree({ node, depth = 0, ...props }: Props): ReactElement {
  const isDir = node.kind === 'directory'
  const isExpanded = props.expanded.has(node.path)
  const isLoading = props.branchLoading.has(node.path)
  const error = props.branchErrors[node.path]
  const paddingLeft = 8 + depth * 14
  return (
    <li className={cx('source-tree-node')}>
      <button
        aria-expanded={isDir ? isExpanded : undefined}
        aria-busy={isLoading || undefined}
        className={cx(
          'source-tree-row',
          isDir && 'is-dir',
          node.path === props.selectedPath && 'is-selected'
        )}
        onClick={() => (isDir ? props.onToggle(node.path) : props.onSelectFile(node))}
        style={{ paddingLeft }}
        title={node.name}
        type="button"
      >
        {isDir ? (
          <>
            {isExpanded ? <CaretDownOutlined /> : <CaretRightOutlined />}
            {isLoading ? (
              <LoadingOutlined />
            ) : isExpanded ? (
              <FolderOpenOutlined />
            ) : (
              <FolderOutlined />
            )}
          </>
        ) : (
          <>
            <span className={cx('source-tree-leaf-spacer')} aria-hidden="true" />
            {/\.tsx?$|\.jsx?$|\.json$|\.css$|\.less$|\.scss$|\.html?$|\.md$/.test(node.name) ? (
              <CodeOutlined />
            ) : (
              <FileOutlined />
            )}
          </>
        )}
        <span className={cx('source-tree-name')}>{node.name}</span>
      </button>
      {isDir && isExpanded ? (
        <ul className={cx('source-tree-children')}>
          {node.children
            ?.slice()
            .sort((a, b) => {
              const directoryOrder = Number(b.kind === 'directory') - Number(a.kind === 'directory')
              return directoryOrder || a.name.localeCompare(b.name)
            })
            .map((child) => (
              <SourceTree {...props} depth={depth + 1} key={child.path} node={child} />
            ))}
          {error ? (
            <li>
              <button
                className={cx('source-tree-row')}
                onClick={() => props.onRetry(node.path)}
                style={{ paddingLeft: paddingLeft + 14 }}
                title={error}
                type="button"
              >
                读取失败，点击重试
              </button>
            </li>
          ) : null}
        </ul>
      ) : null}
    </li>
  )
}
