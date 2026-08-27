import {
  CaretDownOutlined,
  CaretRightOutlined,
  CodeOutlined,
  FileOutlined,
  FolderOpenOutlined,
  FolderOutlined,
  LeftOutlined,
  RightOutlined
} from '@ant-design/icons'
import { Empty } from 'antd'
import hljs from 'highlight.js/lib/core'
import java from 'highlight.js/lib/languages/java'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import python from 'highlight.js/lib/languages/python'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import { useEffect, useMemo, useRef, useState, type ReactElement } from 'react'
import type { WorkspaceSourceFile } from '../../../../mock/workspaceFiles'
import { cx } from '../../../../utils'
import FileDiffView, { type PendingFileDiff } from '../FileDiffView'
import DocPanel from '../DocPanel'
import './SourcePanel.less'

hljs.registerLanguage('java', java)
hljs.registerLanguage('javascript', javascript)
hljs.registerLanguage('json', json)
hljs.registerLanguage('python', python)
hljs.registerLanguage('typescript', typescript)
hljs.registerLanguage('xml', xml)

type Props = {
  files: WorkspaceSourceFile[]
  /** 允许工作区预置空目录，但目录本身不能伪装成正式文件。 */
  directories?: string[]
  initialFilePath?: string
  /** 生成中的单文件 Diff：存在时该页签呈现 Diff 写入过程，接受后恢复文件树浏览。 */
  diff?: PendingFileDiff | null
  /** 选中 Markdown 文档时的编辑配置（内容/只读/保存草稿），未提供时按纯文本展示。 */
  docConfig?: (path: string) =>
    | { content: string; readOnly?: boolean; onSaveEdit?: (draft: string) => void }
    | undefined
}

type FileNode = {
  kind: 'file'
  name: string
  path: string
}

type DirectoryNode = {
  kind: 'directory'
  name: string
  path: string
  children: TreeNode[]
}

type TreeNode = FileNode | DirectoryNode

/** 按文件扩展名推断 highlight.js 语言；无匹配返回 undefined（纯文本展示）。 */
function languageForFile(name: string): string | undefined {
  const ext = name.split('.').pop()?.toLowerCase() || ''
  if (['ts', 'tsx'].includes(ext)) return 'typescript'
  if (['js', 'jsx', 'mjs', 'cjs'].includes(ext)) return 'javascript'
  if (ext === 'json') return 'json'
  if (['html', 'svg', 'xml'].includes(ext)) return 'xml'
  if (ext === 'java') return 'java'
  if (ext === 'py') return 'python'
  return undefined
}

/** 把 HTML 特殊字符转义为纯文本，作为高亮失败时的兜底。 */
function escapeHtml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
}

/** 把扁平的文件路径列表组装成目录树；目录按名称排序并始终排在文件前。 */
function buildTree(files: WorkspaceSourceFile[], directories: string[] = []): DirectoryNode {
  const root: DirectoryNode = { kind: 'directory', name: '', path: '', children: [] }
  directories.forEach((directory) => {
    const segments = directory.split('/').filter(Boolean)
    let current = root
    segments.forEach((segment, index) => {
      const path = segments.slice(0, index + 1).join('/')
      let next = current.children.find(
        (child) => child.kind === 'directory' && child.name === segment
      ) as DirectoryNode | undefined
      if (!next) {
        next = { kind: 'directory', name: segment, path, children: [] }
        current.children.push(next)
      }
      current = next
    })
  })
  for (const file of files) {
    const segments = file.path.split('/').filter(Boolean)
    let current = root
    segments.forEach((segment, index) => {
      const path = segments.slice(0, index + 1).join('/')
      const isFile = index === segments.length - 1
      if (isFile) {
        current.children.push({ kind: 'file', name: segment, path })
        return
      }
      let next = current.children.find(
        (child) => child.kind === 'directory' && child.name === segment
      ) as DirectoryNode | undefined
      if (!next) {
        next = { kind: 'directory', name: segment, path, children: [] }
        current.children.push(next)
      }
      current = next
    })
  }
  const sortNode = (node: TreeNode): void => {
    if (node.kind !== 'directory') return
    node.children.sort((a, b) => {
      if (a.kind !== b.kind) return a.kind === 'directory' ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    node.children.forEach(sortNode)
  }
  sortNode(root)
  return root
}

/** 查找目标文件的所有祖先目录路径，用于展开并定位到该文件。 */
function ancestorDirPaths(root: TreeNode, targetPath: string): string[] {
  const result: string[] = []
  const walk = (node: TreeNode, ancestors: string[]): boolean => {
    if (node.kind === 'file' && node.path === targetPath) return true
    if (node.kind !== 'directory') return false
    for (const child of node.children) {
      if (walk(child, [...ancestors, node.path])) {
        result.push(...ancestors, node.path)
        return true
      }
    }
    return false
  }
  walk(root, [])
  return Array.from(new Set(result)).filter(Boolean)
}

/** 递归渲染目录树行：目录可折叠，文件点击后切换右侧查看器内容。 */
function renderTreeNodes(
  node: TreeNode,
  depth: number,
  expanded: Set<string>,
  selectedPath: string,
  onToggle: (path: string) => void,
  onSelectFile: (node: FileNode) => void
): ReactElement {
  const isDir = node.kind === 'directory'
  const isExpanded = expanded.has(node.path)
  const isSelected = node.path === selectedPath
  return (
    <li className={cx('source-tree-node')} key={node.path || 'root'}>
      <button
        className={cx('source-tree-row', isDir && 'is-dir', isSelected && 'is-selected')}
        onClick={() => (isDir ? onToggle(node.path) : onSelectFile(node as FileNode))}
        style={{ paddingLeft: `${8 + depth * 14}px` }}
        title={node.name}
        type="button"
      >
        {isDir ? (
          <>
            {isExpanded ? <CaretDownOutlined /> : <CaretRightOutlined />}
            {isExpanded ? <FolderOpenOutlined /> : <FolderOutlined />}
          </>
        ) : (
          <>
            <span aria-hidden="true" className={cx('source-tree-leaf-spacer')} />
            {/\.(tsx?|jsx?|json|css|less|scss|html?|md|java|py|xml|ya?ml)$/.test(node.name) ? (
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
          {node.children.map((child) =>
            renderTreeNodes(child, depth + 1, expanded, selectedPath, onToggle, onSelectFile)
          )}
        </ul>
      ) : null}
    </li>
  )
}

/** 「应用文件」面板：左侧文件内容查看器 + 右侧可整栏收起的应用目录树。 */
export default function SourcePanel({
  files,
  directories,
  initialFilePath,
  diff,
  docConfig
}: Props): ReactElement {
  const tree = useMemo(() => buildTree(files, directories), [directories, files])
  // 目录树默认折叠；仅定位 initialFilePath 时自动展开其祖先目录。
  const [expanded, setExpanded] = useState<Set<string>>(() => new Set<string>())
  const [selectedPath, setSelectedPath] = useState(initialFilePath || '')
  const [sourceScrollTop, setSourceScrollTop] = useState(0)
  const sourceLineNumbersRef = useRef<HTMLDivElement>(null)
  // 目录栏收起态：收起后仅保留右侧窄条入口，让文件内容区占满剩余宽度。
  const [treeCollapsed, setTreeCollapsed] = useState(false)

  // Diff 写入期间定位到正在生成的文件；平时定位当前产物文件，均只展开必要祖先。
  useEffect(() => {
    const target = diff?.path || initialFilePath
    if (!target) return
    setSelectedPath(target)
    setExpanded((current) => new Set([...current, ...ancestorDirPaths(tree, target)]))
  }, [diff?.path, initialFilePath, tree])

  const selectedFile = files.find((file) => file.path === selectedPath)
  // 新建 Markdown 尚未接受时不在文件树中，但 Diff 仍需在同一份文档编辑区展示。
  const documentPath = selectedFile?.path || (diff?.path.endsWith('.md') ? diff.path : '')
  const selectedDoc = documentPath ? docConfig?.(documentPath) : undefined
  const highlighted = useMemo(() => {
    if (!selectedFile) return ''
    const lang = languageForFile(selectedFile.path)
    try {
      if (lang) return hljs.highlight(selectedFile.content, { language: lang }).value
    } catch {
      // 高亮失败时回退为转义后的纯文本。
    }
    return escapeHtml(selectedFile.content)
  }, [selectedFile])

  /** 折叠/展开目录节点。 */
  const handleToggle = (path: string): void => {
    setExpanded((current) => {
      const next = new Set(current)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })
  }

  /** 收起/展开右侧目录栏；目录节点自身的折叠状态保持不变。 */
  const handleToggleTreePanel = (): void => {
    setTreeCollapsed((current) => !current)
  }

  return (
    <div className={cx('source-panel')}>
      {/* 内容区固定在左：Diff 写入、文档编辑与代码阅读共用同一块区域。 */}
      <div className={cx('source-panel-viewer')}>
        {selectedDoc ? (
          // Markdown 的 Diff 与编辑器共用同一个文档内容区，接受后自然恢复编辑态。
            <DocPanel
            content={selectedDoc.content}
            diff={diff}
            docName={documentPath.split('/').pop()}
            onSaveEdit={selectedDoc.onSaveEdit}
            readOnly={selectedDoc.readOnly}
          />
        ) : diff ? (
          // 代码文件没有 Markdown 编辑器，仍复用同一个内容区展示单文件 Diff。
          <FileDiffView diff={diff} />
        ) : selectedFile ? (
          <div className={cx('source-panel-code-editor')}>
            <div
              aria-hidden="true"
              className={cx('source-panel-line-numbers')}
              ref={sourceLineNumbersRef}
              style={{ transform: `translateY(-${sourceScrollTop}px)` }}
            >
              {selectedFile.content.split('\n').map((_, index) => (
                <div className={cx('source-panel-line-number')} key={index}>
                  <span className={cx('source-panel-line-marker')} aria-hidden="true">
                    {' '}
                  </span>
                  {index + 1}
                </div>
              ))}
            </div>
            <pre
              className={cx('source-panel-pre')}
              onScroll={(event) => setSourceScrollTop(event.currentTarget.scrollTop)}
            >
              <code className="hljs" dangerouslySetInnerHTML={{ __html: highlighted }} />
            </pre>
          </div>
        ) : (
          <div className={cx('source-panel-viewer-empty')}>
            <Empty description="选择右侧文件查看内容" image={Empty.PRESENTED_IMAGE_SIMPLE} />
          </div>
        )}
      </div>
      {/* 目录树固定在右：可整栏收起为窄条，目录节点默认折叠、仅自动展开定位文件的祖先链。 */}
      <aside
        className={cx('source-panel-tree', treeCollapsed && 'is-collapsed')}
        aria-label="目录树"
      >
        {treeCollapsed ? (
          <div className={cx('source-panel-tree-rail')}>
            <button
              className={cx('source-panel-tree-toggle')}
              onClick={handleToggleTreePanel}
              title="展开目录树"
              aria-label="展开目录树"
              aria-expanded="false"
              type="button"
            >
              <LeftOutlined />
            </button>
            <span className={cx('source-panel-tree-rail-label')}>目录树</span>
          </div>
        ) : (
          <>
            <header className={cx('source-panel-tree-header')}>
              <span className={cx('source-panel-tree-title')}>
                <FolderOpenOutlined />
                目录树
              </span>
              <button
                className={cx('source-panel-tree-toggle')}
                onClick={handleToggleTreePanel}
                title="收起目录树"
                aria-label="收起目录树"
                aria-expanded="true"
                type="button"
              >
                <RightOutlined />
              </button>
            </header>
            <div className={cx('source-panel-tree-body')}>
              {tree.children.length > 0 ? (
                <ul className={cx('source-tree')}>
                  {tree.children.map((child) =>
                    renderTreeNodes(child, 0, expanded, selectedPath, handleToggle, (node) =>
                      setSelectedPath(node.path)
                    )
                  )}
                </ul>
              ) : (
                <div className={cx('source-panel-tree-empty')}>
                  <Empty description="暂无应用文件" image={Empty.PRESENTED_IMAGE_SIMPLE} />
                </div>
              )}
            </div>
          </>
        )}
      </aside>
    </div>
  )
}
